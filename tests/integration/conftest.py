"""Integration test infrastructure — real LLM calls, real tool execution, LLM judge.

Unlike the e2e suite (which uses ScriptedLLM for deterministic replay), these
tests send real messages through the full Prax pipeline with real LLM calls.
An LLM judge then evaluates whether the agentic workflow and output artifacts
met expectations.

Requires a real API key (OPENAI_KEY or ANTHROPIC_KEY).  Skipped automatically
when no key is available.

Run with::

    pytest tests/integration/ -m integration -v

Artifacts (response, execution graph, workspace files) are saved to
``tests/integration/.artifacts/`` for human review.  This directory is
git-ignored.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from langchain_core.callbacks import BaseCallbackHandler

# ---------------------------------------------------------------------------
# Skip when no API key is available
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Artifact storage
# ---------------------------------------------------------------------------

ARTIFACTS_DIR = Path(__file__).parent / ".artifacts"


def _sanitize_name(name: str) -> str:
    """Turn a scenario name into a filesystem-safe directory name."""
    return name.lower().replace(" ", "_").replace("/", "_")[:60]


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

# Per-million-token pricing (USD).  Updated 2026-03.
# Override via PRAX_MODEL_PRICING env var — JSON dict, e.g.:
#   PRAX_MODEL_PRICING='{"my-model": {"input": 1.0, "output": 5.0}}'
# User overrides are merged on top of defaults (user values win).
_DEFAULT_MODEL_PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-5.4-nano":  {"input": 0.10,  "output": 0.40},
    "gpt-5.4-mini":  {"input": 0.40,  "output": 1.60},
    "gpt-5.4":       {"input": 2.50,  "output": 10.00},
    "gpt-5.4-pro":   {"input": 15.00, "output": 60.00},
    "gpt-4o-mini":   {"input": 0.15,  "output": 0.60},
    "gpt-4o":        {"input": 2.50,  "output": 10.00},
    # Anthropic
    "claude-haiku-4-5":          {"input": 1.00,  "output": 5.00},
    "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":           {"input": 5.00,  "output": 25.00},
    # Legacy Anthropic model aliases
    "claude-3-5-haiku-latest":   {"input": 1.00,  "output": 5.00},
    "claude-3-5-sonnet-latest":  {"input": 3.00,  "output": 15.00},
    "claude-sonnet-4-5":         {"input": 3.00,  "output": 15.00},
    "claude-opus-4-5":           {"input": 15.00, "output": 75.00},
    # Google
    "gemini-3.1-pro":      {"input": 2.50,  "output": 15.00},
    "gemini-3.1-flash":    {"input": 0.15,  "output": 0.60},
    "gemini-2.0-flash":    {"input": 0.10,  "output": 0.40},
    "gemini-2.5-pro":      {"input": 1.25,  "output": 10.00},
}

# Fallback pricing for unknown models (roughly medium-tier)
_FALLBACK_PRICING = {"input": 1.00, "output": 4.00}

_warned_models: set[str] = set()


def _load_model_pricing() -> dict[str, dict[str, float]]:
    """Load model pricing from defaults + optional PRAX_MODEL_PRICING env override."""
    pricing = dict(_DEFAULT_MODEL_PRICING)
    env_val = os.environ.get("PRAX_MODEL_PRICING", "").strip()
    if env_val:
        try:
            user_pricing = json.loads(env_val)
            if isinstance(user_pricing, dict):
                pricing.update(user_pricing)
                logging.getLogger(__name__).info(
                    "Loaded %d model pricing override(s) from PRAX_MODEL_PRICING",
                    len(user_pricing),
                )
        except (json.JSONDecodeError, TypeError) as exc:
            logging.getLogger(__name__).warning(
                "PRAX_MODEL_PRICING env var is not valid JSON, ignoring: %s", exc,
            )
    return pricing


MODEL_PRICING = _load_model_pricing()


def _get_pricing(model: str) -> dict[str, float]:
    """Look up pricing, stripping date suffixes (e.g. gpt-5.4-nano-2026-03-17).

    Warns once per unknown model instead of silently falling back.
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # Try stripping a trailing date suffix (-YYYY-MM-DD)
    import re
    base = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
    if base in MODEL_PRICING:
        return MODEL_PRICING[base]
    # Try prefix matching (longest match wins)
    candidates = [k for k in MODEL_PRICING if model.startswith(k)]
    if candidates:
        return MODEL_PRICING[max(candidates, key=len)]
    # Warn once per unknown model
    if model not in _warned_models:
        _warned_models.add(model)
        logging.getLogger(__name__).warning(
            "No pricing data for model '%s' — using fallback ($%.2f/$%.2f per M tokens). "
            "Set PRAX_MODEL_PRICING env var to add pricing for this model.",
            model, _FALLBACK_PRICING["input"], _FALLBACK_PRICING["output"],
        )
    return _FALLBACK_PRICING


@dataclass
class LLMCall:
    """Token usage and cost for a single LLM invocation."""
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_seconds: float


class CostTracker(BaseCallbackHandler):
    """LangChain callback that records token usage and cost per LLM call.

    Attach as a callback to any LLM.  After the run, inspect ``.calls``
    for per-call data and ``.total_cost`` / ``.total_tokens`` for aggregates.
    """

    def __init__(self):
        super().__init__()
        self.calls: list[LLMCall] = []
        self._start_times: dict[UUID, float] = {}

    @property
    def total_cost(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._start_times[run_id] = time.monotonic()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._start_times[run_id] = time.monotonic()

    def on_llm_end(self, response, *, run_id, **kwargs):
        elapsed = time.monotonic() - self._start_times.pop(run_id, time.monotonic())

        # Extract token usage — same logic as OTelLLMCallback
        usage = {}
        if hasattr(response, "llm_output") and response.llm_output:
            usage = response.llm_output.get("token_usage", {})
        elif response.generations:
            gen = response.generations[0][0] if response.generations[0] else None
            if gen and hasattr(gen, "generation_info") and gen.generation_info:
                usage = gen.generation_info.get("token_usage", {})

        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        model = (
            usage.get("model_name")
            or (response.llm_output or {}).get("model_name", "unknown")
        )

        # Calculate cost
        pricing = _get_pricing(model)
        cost = (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )

        self.calls.append(LLMCall(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            duration_seconds=elapsed,
        ))

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._start_times.pop(run_id, None)

    def summary(self) -> dict:
        """Structured summary for artifact persistence."""
        by_model: dict[str, dict] = {}
        for call in self.calls:
            entry = by_model.setdefault(call.model, {
                "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "total_seconds": 0.0,
            })
            entry["calls"] += 1
            entry["input_tokens"] += call.input_tokens
            entry["output_tokens"] += call.output_tokens
            entry["cost_usd"] += call.cost_usd
            entry["total_seconds"] += call.duration_seconds

        return {
            "total_cost_usd": round(self.total_cost, 6),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_llm_calls": len(self.calls),
            "by_model": {
                model: {k: round(v, 6) if isinstance(v, float) else v for k, v in d.items()}
                for model, d in by_model.items()
            },
            "calls": [
                {
                    "model": c.model,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "cost_usd": round(c.cost_usd, 6),
                    "duration_seconds": round(c.duration_seconds, 2),
                }
                for c in self.calls
            ],
        }


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class IntegrationResult:
    """Everything captured from a single Prax run."""

    response: str
    graph_summary: str
    workspace_files: dict[str, str]  # relative path -> content
    trace_log: str
    duration_seconds: float
    span_nodes: list[dict] = field(default_factory=list)  # structured spans
    cost: dict = field(default_factory=dict)  # cost tracker summary
    tier_choices: list[dict] = field(default_factory=list)  # tier→model log


@dataclass
class JudgeVerdict:
    """Structured evaluation from the LLM judge."""

    passed: bool
    flow_correct: bool
    output_correct: bool
    reasoning: str
    issues: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Real Prax runner
# ---------------------------------------------------------------------------


# Keys that the root conftest.py overwrites with test stubs.
# We restore the real values so integration tests hit the real API.
_REAL_KEY_ENVS = [
    "OPENAI_KEY", "ANTHROPIC_KEY", "GOOGLE_API_KEY", "GOOGLE_CSE_ID",
    "GOOGLE_VERTEX_PROJECT", "GOOGLE_VERTEX_LOCATION",
    "LLM_PROVIDER", "BASE_MODEL",
    "LOW_MODEL", "MEDIUM_MODEL", "HIGH_MODEL", "PRO_MODEL",
]


def _load_real_env() -> dict[str, str]:
    """Load real API keys from the .env file and current environment.

    The root conftest.py (autouse) replaces env vars with test stubs before
    our module-level code runs, so we read the .env file directly to get
    the real keys.
    """
    real: dict[str, str] = {}
    # First, try the .env file (most reliable source)
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key in _REAL_KEY_ENVS and value:
                    real[key] = value
    # Overlay with actual env vars (they take precedence, e.g. from CI)
    for key in _REAL_KEY_ENVS:
        val = os.environ.get(key)
        if val and val not in ("sk-test", "sk-ant-test", "g-test", "cx-test"):
            real[key] = val
    return real


_real_env = _load_real_env()


@pytest.fixture(autouse=True)
def _restore_real_keys(monkeypatch):
    """Re-inject real API keys that the root conftest.py replaced with stubs."""
    if not _real_env.get("OPENAI_KEY") and not _real_env.get("ANTHROPIC_KEY"):
        pytest.skip("No real LLM API key available (check .env file)")
    for key, value in _real_env.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def run_prax(tmp_path):
    """Run a real message through the full Prax pipeline.

    Returns an :class:`IntegrationResult` with the response, execution graph,
    workspace files, trace log, and cost breakdown.
    """
    def _run(
        message: str,
        *,
        timeout: float = 120,
        conversation: list | None = None,
        workspace_context: str = "",
        require_plugins: bool = False,
    ) -> IntegrationResult:
        workspace_dir = tmp_path / "ws"
        workspace_dir.mkdir(exist_ok=True)

        # Restore real API keys and point settings at temp workspace
        for key, value in _real_env.items():
            os.environ[key] = value
        os.environ["WORKSPACE_DIR"] = str(workspace_dir)

        # Reload settings to pick up real keys + workspace dir
        import prax.settings as settings_mod
        importlib.reload(settings_mod)
        new_settings = settings_mod.settings
        for mod_name, mod in list(sys.modules.items()):
            if (
                mod is not None
                and mod_name.startswith("prax.")
                and hasattr(mod, "settings")
                and mod is not settings_mod
            ):
                try:
                    setattr(mod, "settings", new_settings)
                except Exception:
                    pass

        # Set the agent run timeout to match the scenario timeout so the
        # orchestrator itself aborts if it exceeds the budget.
        new_settings.agent_run_timeout = int(timeout)

        # Install cost tracker by patching get_otel_callbacks to include it
        # in every list returned.  This ensures every LLM created via
        # build_llm() during the run gets the cost tracker attached.
        cost_tracker = CostTracker()
        import prax.observability.callbacks as _cb_mod
        _orig_get_otel = _cb_mod.get_otel_callbacks

        def _patched_get_otel():
            cbs = _orig_get_otel()
            if cost_tracker not in cbs:
                cbs.append(cost_tracker)
            return cbs

        _cb_mod.get_otel_callbacks = _patched_get_otel

        # Tier tracking is now built into build_llm() — no wrapper needed.
        # Clear any stale entries before the run.
        from prax.agent.llm_factory import drain_tier_choices as _drain_tc
        _drain_tc()

        # Mock only external services (TeamWork, SMS) — NOT the LLM
        mock_loader = MagicMock()
        mock_loader.get_tools.return_value = []
        mock_loader.version = 0
        mock_loader.load_all.return_value = None

        with ExitStack() as stack:
            # Silence TeamWork hooks (no TeamWork server running)
            stack.enter_context(patch("prax.services.teamwork_hooks.set_role_status"))
            stack.enter_context(patch("prax.services.teamwork_hooks.post_to_channel"))
            stack.enter_context(patch("prax.services.teamwork_hooks.reset_all_idle"))
            stack.enter_context(patch("prax.services.teamwork_hooks.push_live_output"))

            # Mock plugin loader unless the scenario needs real plugins
            # (e.g. arxiv_to_note, pdf_summary_tool).
            if not require_plugins:
                stack.enter_context(
                    patch("prax.plugins.loader.get_plugin_loader", return_value=mock_loader)
                )
                stack.enter_context(
                    patch("prax.agent.tool_registry.get_plugin_loader", return_value=mock_loader)
                )

            from prax.agent.orchestrator import ConversationAgent
            from prax.agent.trace import (
                get_current_trace,
                get_graph_summary,
                get_last_completed_graph,
            )
            from prax.agent.user_context import current_user_id

            agent = ConversationAgent()
            current_user_id.set("+10000000000")

            start = time.monotonic()
            response = agent.run(
                conversation=conversation or [],
                user_input=message,
                workspace_context=workspace_context,
            )
            duration = time.monotonic() - start

            # Capture execution graph — try active trace first, fall back to
            # last completed graph (root span resets the contextvar on end).
            graph_summary = get_graph_summary()
            graph = None
            ctx = get_current_trace()
            if ctx and ctx.graph:
                graph = ctx.graph
            else:
                graph = get_last_completed_graph()

            span_nodes = []
            if graph:
                with graph._lock:
                    for node in graph._nodes.values():
                        span_nodes.append({
                            "name": node.name,
                            "spoke_or_category": node.spoke_or_category,
                            "status": node.status,
                            "tool_calls": node.tool_calls,
                            "summary": node.summary,
                            "duration": (
                                (node.finished_at - node.started_at).total_seconds()
                                if node.finished_at
                                else None
                            ),
                            "tier_choices": list(node.tier_choices),
                        })

            # Capture workspace files
            # workspace_root() strips the leading "+" from user_id,
            # and files are saved under an "active/" subdirectory.
            # Filter out infrastructure files created by ensure_workspace()
            # and the orchestrator (not agent output).
            _INFRA_FILES = {
                ".gitignore", "instructions.md", "agent_plan.yaml",
            }
            workspace_files: dict[str, str] = {}
            user_ws = workspace_dir / "10000000000"
            if user_ws.exists():
                for fpath in user_ws.rglob("*"):
                    if fpath.is_file() and ".git" not in fpath.parts:
                        rel = str(fpath.relative_to(user_ws))
                        if rel in _INFRA_FILES:
                            continue
                        try:
                            workspace_files[rel] = fpath.read_text(errors="replace")
                        except Exception:
                            workspace_files[rel] = "<binary>"

            # Capture trace log
            trace_log = workspace_files.get("trace.log", "")

        # Restore patched functions
        _cb_mod.get_otel_callbacks = _orig_get_otel

        # Collect tier choices from the production trace system
        from prax.agent.trace import get_all_tier_choices
        tier_choices = get_all_tier_choices(graph)

        return IntegrationResult(
            response=response,
            graph_summary=graph_summary,
            workspace_files=workspace_files,
            trace_log=trace_log,
            duration_seconds=duration,
            span_nodes=span_nodes,
            cost=cost_tracker.summary(),
            tier_choices=tier_choices,
        )

    return _run


# ---------------------------------------------------------------------------
# A/B experiment fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def run_prax_with_overrides(run_prax):
    """Run a scenario with experiment tier overrides applied.

    Usage::

        result = run_prax_with_overrides(
            "Save a note about X",
            overrides={"subagent_research": {"tier": "medium"}},
            timeout=60,
        )
    """
    def _run(message: str, *, overrides: dict, **kwargs):
        from prax.plugins.llm_config import (
            clear_experiment_overrides,
            set_experiment_overrides,
        )
        token = set_experiment_overrides(overrides)
        try:
            return run_prax(message, **kwargs)
        finally:
            clear_experiment_overrides(token)
    return _run


@pytest.fixture
def compare_runs(save_artifacts):
    """Generate a ComparisonReport from two IntegrationResults.

    Saves the comparison as artifacts for human review.
    """
    def _compare(
        baseline,
        experiment_result,
        experiment_name: str,
        scenario: str = "",
        overrides: dict | None = None,
        baseline_verdict=None,
        experiment_verdict=None,
    ):
        from tests.integration.compare import build_comparison

        report = build_comparison(
            baseline=baseline,
            experiment=experiment_result,
            experiment_name=experiment_name,
            scenario=scenario,
            overrides=overrides,
            baseline_verdict=baseline_verdict,
            experiment_verdict=experiment_verdict,
        )

        # Save comparison artifacts
        artifact_base = Path(__file__).parent / ".artifacts" / "experiments"
        artifact_base.mkdir(parents=True, exist_ok=True)

        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = artifact_base / experiment_name / ts
        run_dir.mkdir(parents=True, exist_ok=True)

        (run_dir / "comparison.md").write_text(report.summary_md)
        (run_dir / "comparison.json").write_text(
            json.dumps(report.raw, indent=2, default=str)
        )

        return report
    return _compare


# ---------------------------------------------------------------------------
# LLM Judge
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = """\
You are a QA judge for an AI agent system called Prax.  Prax is a multi-agent
system that processes user requests by delegating to specialized "spoke" agents
(browser, content, sandbox, knowledge, sysadmin) and generic sub-agents
(research, workspace, codegen, scheduler).

The orchestrator receives a user message, decides which tools or spokes to
invoke, executes them, and returns a response.  Workspace tools
(workspace_save, workspace_patch, workspace_read, workspace_list) are used
directly by the orchestrator to manage user files.  Research is done via
delegate_task or the research spoke.

Your job: given a test scenario's expectations and the actual evidence from a
Prax run, determine whether the run PASSED or FAILED.

Be pragmatic — minor stylistic differences are fine.  Focus on:
1. Did the right tools/spokes fire (or NOT fire, as expected)?
2. Is the output artifact (if any) present and reasonable?
3. Does the response make sense for the request?

Respond with ONLY a JSON object (no markdown fencing):
{
    "passed": true/false,
    "flow_correct": true/false,
    "output_correct": true/false,
    "reasoning": "brief explanation",
    "issues": ["issue 1", "issue 2"]
}
"""


@pytest.fixture
def judge():
    """LLM judge that evaluates an IntegrationResult against scenario expectations."""

    def _judge(
        result: IntegrationResult,
        *,
        message: str,
        expected_flow: str,
        quality_criteria: str,
    ) -> JudgeVerdict:
        # Format workspace files for the judge (skip trace.log — too noisy)
        file_summaries = []
        for fname, content in result.workspace_files.items():
            if fname == "trace.log":
                continue
            preview = content[:8000]
            file_summaries.append(f"--- {fname} ---\n{preview}")
        files_text = "\n\n".join(file_summaries) if file_summaries else "(no files created)"

        # Format cost for the judge
        cost = result.cost
        cost_text = (
            f"Total: ${cost.get('total_cost_usd', 0):.4f} "
            f"({cost.get('total_input_tokens', 0)} in / "
            f"{cost.get('total_output_tokens', 0)} out, "
            f"{cost.get('total_llm_calls', 0)} calls)"
        )

        user_prompt = f"""\
## Test Scenario
User message: {message}

## Expected Agentic Flow
{expected_flow}

## Quality Criteria
{quality_criteria}

## Evidence

### Agent Response
{result.response[:3000]}

### Execution Graph
{result.graph_summary}

### Structured Spans
{json.dumps(result.span_nodes, indent=2)[:2000]}

### Workspace Files Created
{files_text}

### Trace Log (last 2000 chars)
{result.trace_log[-2000:]}

### Cost & Timing
Duration: {result.duration_seconds:.1f}s
{cost_text}

### Model Tier Choices
{json.dumps(result.tier_choices, indent=2)[:1500]}

Evaluate this run.  Return JSON only.
"""
        # Majority voting: run 3 judges in parallel on medium-tier models.
        # A single cheap judge hallucinates too often (false negatives on
        # perfectly valid output).  Majority vote eliminates flaky verdicts.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from langchain_core.messages import HumanMessage, SystemMessage

        from prax.agent.llm_factory import build_llm

        judge_messages = [
            SystemMessage(content=_JUDGE_SYSTEM),
            HumanMessage(content=user_prompt),
        ]

        def _run_single_judge(judge_id: int) -> dict | None:
            llm = build_llm(tier="medium", temperature=0.1)
            try:
                resp = llm.invoke(judge_messages)
                text = resp.content.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                    if text.endswith("```"):
                        text = text[:-3]
                    text = text.strip()
                return json.loads(text)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Judge %d failed: %s", judge_id, exc,
                )
                return None

        # Run 3 judges in parallel
        verdicts: list[dict] = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_run_single_judge, i) for i in range(3)]
            for future in as_completed(futures):
                result_data = future.result()
                if result_data is not None:
                    verdicts.append(result_data)

        if not verdicts:
            return JudgeVerdict(
                passed=False,
                flow_correct=False,
                output_correct=False,
                reasoning="All 3 judges failed to return valid JSON",
                issues=["judge_total_failure"],
            )

        # Majority vote: pass if >= 2 of 3 judges say pass
        pass_votes = sum(1 for v in verdicts if v.get("passed"))
        majority_passed = pass_votes >= 2

        # Aggregate reasoning from all judges
        all_reasoning = [v.get("reasoning", "") for v in verdicts]
        all_issues = []
        for v in verdicts:
            all_issues.extend(v.get("issues", []))

        # Use the majority verdict for flow/output correctness
        flow_votes = sum(1 for v in verdicts if v.get("flow_correct"))
        output_votes = sum(1 for v in verdicts if v.get("output_correct"))

        combined_reasoning = (
            f"[{pass_votes}/{len(verdicts)} judges voted PASS] "
            + " | ".join(r for r in all_reasoning if r)
        )

        return JudgeVerdict(
            passed=majority_passed,
            flow_correct=flow_votes >= 2,
            output_correct=output_votes >= 2,
            reasoning=combined_reasoning,
            issues=list(set(all_issues)),  # deduplicate
        )

    return _judge


# ---------------------------------------------------------------------------
# Artifact persistence
# ---------------------------------------------------------------------------


@pytest.fixture
def save_artifacts():
    """Save test artifacts to disk for human review."""

    def _save(name: str, result: IntegrationResult, verdict: JudgeVerdict | None = None):
        run_dir = ARTIFACTS_DIR / _sanitize_name(name) / time.strftime("%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)

        # Response
        (run_dir / "response.md").write_text(result.response)

        # Execution graph
        (run_dir / "execution_graph.txt").write_text(result.graph_summary)

        # Structured spans
        (run_dir / "spans.json").write_text(
            json.dumps(result.span_nodes, indent=2, default=str)
        )

        # Cost breakdown
        (run_dir / "cost.json").write_text(
            json.dumps(result.cost, indent=2)
        )

        # Tier choices
        if result.tier_choices:
            (run_dir / "tiers.json").write_text(
                json.dumps(result.tier_choices, indent=2)
            )

        # Workspace files
        ws_dir = run_dir / "workspace"
        ws_dir.mkdir(exist_ok=True)
        for fname, content in result.workspace_files.items():
            if fname == "trace.log":
                # Save trace log at top level
                (run_dir / "trace.log").write_text(content)
                continue
            target = ws_dir / fname
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

        # Judge verdict
        if verdict:
            verdict_data = {
                "passed": verdict.passed,
                "flow_correct": verdict.flow_correct,
                "output_correct": verdict.output_correct,
                "reasoning": verdict.reasoning,
                "issues": verdict.issues,
            }
            (run_dir / "verdict.json").write_text(
                json.dumps(verdict_data, indent=2)
            )

        # Summary for quick review
        cost = result.cost
        summary_lines = [
            f"# Integration Test: {name}",
            f"",
            f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Duration:** {result.duration_seconds:.1f}s",
            f"**Response length:** {len(result.response)} chars",
            f"**Workspace files:** {len(result.workspace_files)}",
            f"**Spans:** {len(result.span_nodes)}",
            f"",
            f"## Cost",
            f"",
            f"**Total:** ${cost.get('total_cost_usd', 0):.4f}",
            f"**Input tokens:** {cost.get('total_input_tokens', 0):,}",
            f"**Output tokens:** {cost.get('total_output_tokens', 0):,}",
            f"**LLM calls:** {cost.get('total_llm_calls', 0)}",
        ]
        by_model = cost.get("by_model", {})
        if by_model:
            summary_lines.append(f"")
            summary_lines.append(f"### By Model")
            summary_lines.append(f"")
            summary_lines.append(f"| Model | Calls | In Tokens | Out Tokens | Cost | Time |")
            summary_lines.append(f"|-------|------:|----------:|-----------:|-----:|-----:|")
            for model, stats in by_model.items():
                summary_lines.append(
                    f"| {model} | {stats['calls']} "
                    f"| {stats['input_tokens']:,} "
                    f"| {stats['output_tokens']:,} "
                    f"| ${stats['cost_usd']:.4f} "
                    f"| {stats['total_seconds']:.1f}s |"
                )

        if verdict:
            status = "PASSED" if verdict.passed else "FAILED"
            summary_lines.extend([
                f"",
                f"## Verdict: {status}",
                f"",
                f"**Flow correct:** {verdict.flow_correct}",
                f"**Output correct:** {verdict.output_correct}",
                f"**Reasoning:** {verdict.reasoning}",
            ])
            if verdict.issues:
                summary_lines.append(f"**Issues:**")
                for issue in verdict.issues:
                    summary_lines.append(f"  - {issue}")

        (run_dir / "SUMMARY.md").write_text("\n".join(summary_lines) + "\n")

        return run_dir

    return _save
