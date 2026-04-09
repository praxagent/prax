#!/usr/bin/env python3
"""Coverage test harness — generates Phase 0 data fast.

Runs a diverse set of test scenarios against the local Prax instance,
captures coverage events to a SEPARATE test file, and produces a Pareto
report. Used to accelerate Phase 0 of the pipeline evolution roadmap by
collecting hours-of-real-usage worth of coverage data in minutes.

Usage:
    # Run every scenario, write report to ./harness_results.json
    uv run python scripts/run_coverage_harness.py

    # Run a subset by name (substring match against scenario name)
    uv run python scripts/run_coverage_harness.py --scenarios knowledge,research

    # Enable LLM-as-user multi-turn clarification
    uv run python scripts/run_coverage_harness.py --multi-turn

    # Re-generate report from existing harness data without re-running
    uv run python scripts/run_coverage_harness.py --report-only

    # Hit a different Prax instance
    uv run python scripts/run_coverage_harness.py --base-url http://localhost:5001

See: docs/PIPELINE_EVOLUTION_TODO.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger("coverage_harness")


# ---------------------------------------------------------------------------
# ANSI colour helpers — no third-party dep, gracefully degrades on dumb TTYs.
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t: str) -> str: return _c("32", t)
def red(t: str) -> str: return _c("31", t)
def yellow(t: str) -> str: return _c("33", t)
def cyan(t: str) -> str: return _c("36", t)
def bold(t: str) -> str: return _c("1", t)


# ---------------------------------------------------------------------------
# Scenario catalogue
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    name: str
    request: str
    expected_spoke: str  # the spoke we hope this routes to (or "fallback")


SCENARIOS: list[Scenario] = [
    # ------------------------------------------------------------------
    # knowledge spoke
    # ------------------------------------------------------------------
    Scenario("knowledge_save_note",
             "Save a note about gradient descent optimization techniques",
             "knowledge"),
    Scenario("knowledge_deep_dive",
             "Make me a deep dive note on chi-squared distributions, with equations and toy examples",
             "knowledge"),
    Scenario("knowledge_search",
             "Search my notes for anything about transformers",
             "knowledge"),
    Scenario("knowledge_update",
             "Update my note on 'machine learning basics' with this new info: cross-validation is essential",
             "knowledge"),

    # ------------------------------------------------------------------
    # research spoke
    # ------------------------------------------------------------------
    Scenario("research_kvcache",
             "What are the latest findings on KV cache compression?",
             "research"),
    Scenario("research_compare_archs",
             "Compare Mamba, Transformer, and RWKV architectures",
             "research"),
    Scenario("research_arxiv",
             "Find recent arXiv papers on diffusion models",
             "research"),

    # ------------------------------------------------------------------
    # browser spoke
    # ------------------------------------------------------------------
    Scenario("browser_screenshot",
             "Take a screenshot of github.com/anthropics",
             "browser"),
    Scenario("browser_hn",
             "Read the latest post on hackernews",
             "browser"),
    Scenario("browser_open",
             "Open https://example.com and tell me what you see",
             "browser"),

    # ------------------------------------------------------------------
    # content spoke
    # ------------------------------------------------------------------
    Scenario("content_blog_ai_agents",
             "Write a blog post about the future of AI agents",
             "content"),
    Scenario("content_react_vue",
             "Create an article comparing React and Vue",
             "content"),

    # ------------------------------------------------------------------
    # sandbox spoke — scenarios that ACTUALLY need a sandbox.
    # Trivial code questions (1+1, sum range, regex lint) should NOT
    # delegate — Prax should answer them directly. These scenarios test
    # cases that need real execution: package installs, file I/O, or
    # multi-step scripts where Prax cannot reliably predict the output.
    # ------------------------------------------------------------------
    Scenario("sandbox_install_package",
             "Install networkx in a sandbox and compute the shortest path "
             "between nodes A and D in a graph with edges "
             "[(A,B,1),(B,C,2),(A,C,5),(C,D,1),(B,D,4)]. Return the path and total weight.",
             "sandbox"),
    Scenario("sandbox_untrusted_exec",
             "I'll paste some untrusted Python code below — execute it in a "
             "safe sandbox and tell me what it prints (do NOT run it in your "
             "own environment): `import random; [print(random.randint(0,9)) for _ in range(5)]`",
             "sandbox"),
    Scenario("sandbox_multi_step",
             "Run a multi-step Python script in a sandbox: download the text "
             "of Shakespeare's Sonnet 18 from https://www.gutenberg.org/cache/epub/1041/pg1041.txt, "
             "count the word 'love', and return the count.",
             "sandbox"),

    # ------------------------------------------------------------------
    # scheduler spoke
    # HARNESS NOTE: these scenarios create REAL reminders that will fire.
    # The "oven reminder" is a legitimate end-to-end test of reminder
    # delivery. The "daily briefing" creates a recurring job — keep it
    # to verify scheduling works, but expect to manually clean up.
    # ------------------------------------------------------------------
    Scenario("scheduler_reminder",
             "Remind me in 30 minutes to check the oven",
             "scheduler"),
    Scenario("scheduler_daily",
             "Schedule a daily 9am morning briefing",
             "scheduler"),

    # ------------------------------------------------------------------
    # sysadmin spoke
    # ------------------------------------------------------------------
    Scenario("sysadmin_plugins",
             "What plugins are installed?",
             "sysadmin"),
    Scenario("sysadmin_status",
             "Check the system status",
             "sysadmin"),
    Scenario("sysadmin_logs",
             "Show me my recent activity logs",
             "sysadmin"),

    # ------------------------------------------------------------------
    # memory — expected "direct" now.
    # delegate_memory was removed from the orchestrator tool list because
    # it was acting as a catch-all drain (15/36 turns misrouted).
    # Memory WRITES now happen automatically via turn-end consolidation
    # (see prax/services/memory_service.py:maybe_consolidate). Memory
    # READS happen via the memory_context injection at the start of
    # every turn. So "remember X" or "what do you know about me"
    # requests now get handled directly by the orchestrator with the
    # context already in its system prompt.
    # ------------------------------------------------------------------
    Scenario("memory_remember",
             "Remember that I prefer dark mode and live in San Francisco",
             "direct"),
    Scenario("memory_recall",
             "What do you know about me?",
             "direct"),

    # ------------------------------------------------------------------
    # course spoke
    # ------------------------------------------------------------------
    Scenario("course_linalg",
             "Teach me the basics of linear algebra",
             "course"),
    Scenario("course_continue",
             "Continue my course on quantum computing",
             "course"),

    # ------------------------------------------------------------------
    # workspace spoke
    # ------------------------------------------------------------------
    Scenario("workspace_save",
             "Save the content 'hello world' to a file called test.txt",
             "workspace"),
    Scenario("workspace_list",
             "List the files in my workspace",
             "workspace"),

    # ------------------------------------------------------------------
    # Should fall through to fallback (no existing spoke fits)
    # ------------------------------------------------------------------
    Scenario("fallback_slide_deck",
             "Make me a slide deck about quantum computing with 12 slides",
             "fallback"),
    Scenario("fallback_comparison_matrix",
             "Create a comparison matrix of these 5 frameworks: Django, Flask, FastAPI, Tornado, Bottle",
             "fallback"),
    Scenario("fallback_translate",
             "Translate this paragraph into Spanish, French, and German: 'The quick brown fox jumps over the lazy dog.'",
             "fallback"),
    Scenario("fallback_one_pager",
             "Build me a one-page brief with executive summary and key bullets on remote work productivity",
             "fallback"),
    Scenario("fallback_html_email",
             "Generate an HTML email template for a product launch",
             "fallback"),
    Scenario("fallback_critique_paper",
             "Critique this academic paper and rate it 1-10: 'Attention is all you need' by Vaswani et al.",
             "fallback"),
    Scenario("fallback_sonnet",
             "Write a sonnet about machine learning",
             "fallback"),
    Scenario("fallback_study_plan",
             "Create a study plan for the next 6 weeks covering data structures and algorithms",
             "fallback"),
    Scenario("fallback_test_cases",
             "Generate test cases for this function: def add(a, b): return a + b",
             "fallback"),
    Scenario("fallback_flask_app",
             "Build me a simple flask app that exposes a /hello endpoint",
             "fallback"),
]


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    request: str
    expected_spoke: str
    matched_spoke: str = ""
    delegations: list[str] = field(default_factory=list)
    outcome_status: str = ""
    duration_ms: float = 0
    tool_call_count: int = 0
    turns: int = 1
    matched_expected: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# HTTP client wrapper around the Prax instance
# ---------------------------------------------------------------------------

class PraxClient:
    """Thin wrapper around the Prax HTTP API used by the harness."""

    def __init__(self, base_url: str, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # --- pipeline coverage admin -----------------------------------------

    def enable_test_mode(self, test_file: str | None = None) -> dict:
        body: dict = {"enabled": True}
        if test_file:
            body["file"] = test_file
        r = self._client.post("/teamwork/pipeline-coverage/test-mode", json=body)
        r.raise_for_status()
        return r.json()

    def disable_test_mode(self) -> dict:
        r = self._client.post(
            "/teamwork/pipeline-coverage/test-mode", json={"enabled": False},
        )
        r.raise_for_status()
        return r.json()

    def get_test_mode(self) -> dict:
        r = self._client.get("/teamwork/pipeline-coverage/test-mode")
        r.raise_for_status()
        return r.json()

    def get_events(self, days: int = 14, limit: int = 5000) -> list[dict]:
        r = self._client.get(
            "/teamwork/pipeline-coverage/events",
            params={"days": days, "limit": limit},
        )
        r.raise_for_status()
        return r.json().get("events", [])

    def get_report(self, days: int = 14, top_n: int = 20) -> dict:
        r = self._client.get(
            "/teamwork/pipeline-coverage",
            params={"days": days, "top_n": top_n},
        )
        r.raise_for_status()
        return r.json()

    # --- webhook ---------------------------------------------------------

    def send_webhook(
        self, content: str, channel_id: str, message_id: str,
        project_id: str = "harness", active_view: str = "chat",
    ) -> None:
        payload = {
            "type": "user_message",
            "content": content,
            "channel_id": channel_id,
            "project_id": project_id,
            "message_id": message_id,
            "active_view": active_view,
            "extra_data": {},
        }
        r = self._client.post("/teamwork/webhook", json=payload)
        r.raise_for_status()


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

class HarnessRunner:
    def __init__(
        self,
        client: PraxClient,
        delay_seconds: float = 2.0,
        per_scenario_timeout: float = 300.0,
        multi_turn: bool = False,
        max_turns: int = 4,
    ):
        self.client = client
        self.delay_seconds = delay_seconds
        self.per_scenario_timeout = per_scenario_timeout
        self.multi_turn = multi_turn
        self.max_turns = max_turns

    # ------------------------------------------------------------------
    # Polling — wait until a coverage event for our scenario appears
    # ------------------------------------------------------------------
    def _wait_for_event(
        self,
        marker: str,
        baseline_count: int,
        deadline: float,
        poll_interval: float = 1.5,
    ) -> dict | None:
        """Poll the events endpoint until a NEW event whose request contains
        ``marker`` is observed. Returns the matching event dict or None on
        timeout."""
        while time.time() < deadline:
            try:
                events = self.client.get_events(days=1, limit=500)
            except Exception as exc:
                logger.debug("Failed to fetch events while polling: %s", exc)
                events = []
            # Newer events first; check whether any new event mentions our marker
            new_events = events[: max(0, len(events) - baseline_count + 50)]
            for evt in new_events:
                req = (evt.get("request") or "")
                if marker and marker in req:
                    return evt
            time.sleep(poll_interval)
        return None

    # ------------------------------------------------------------------
    # Single-turn dispatch
    # ------------------------------------------------------------------
    def _send_one(
        self, content: str, channel_id: str,
    ) -> None:
        message_id = f"harness-msg-{uuid.uuid4().hex[:12]}"
        self.client.send_webhook(
            content=content, channel_id=channel_id, message_id=message_id,
        )

    # ------------------------------------------------------------------
    # Optional clarification simulator
    # ------------------------------------------------------------------
    def _simulate_user_reply(self, original_request: str, prax_response: str) -> str:
        """Use a cheap LLM to play the role of a user when Prax asks a
        clarifying question. Returns the user's reply, or "[DONE]" if the
        task is finished."""
        try:
            from prax.agent.llm_factory import build_llm
            llm = build_llm(tier="low", temperature=0.2)
            prompt = (
                "You are pretending to be a user who originally asked: "
                f"\"{original_request}\".\n\n"
                f"Prax responded:\n\"\"\"\n{prax_response}\n\"\"\"\n\n"
                "If Prax is asking a clarifying question, answer it concretely "
                "in 1-2 sentences. Make up plausible details if needed.\n"
                "If Prax has completed the task or is just confirming, reply "
                "with exactly: [DONE]\n\n"
                "Your reply:"
            )
            result = llm.invoke(prompt)
            text = getattr(result, "content", str(result)).strip()
            return text or "[DONE]"
        except Exception as exc:
            logger.debug("LLM-as-user simulation failed: %s", exc)
            return "[DONE]"

    # ------------------------------------------------------------------
    # Run a single scenario
    # ------------------------------------------------------------------
    def run_scenario(self, scenario: Scenario, idx: int) -> ScenarioResult:
        result = ScenarioResult(
            name=scenario.name,
            request=scenario.request,
            expected_spoke=scenario.expected_spoke,
        )

        # Each scenario gets its own channel_id so traces don't collide
        channel_id = f"harness-{idx}-{uuid.uuid4().hex[:8]}"

        try:
            baseline = len(self.client.get_events(days=1, limit=500))
        except Exception as exc:
            result.error = f"baseline fetch failed: {exc}"
            return result

        try:
            self._send_one(scenario.request, channel_id)
        except Exception as exc:
            result.error = f"webhook post failed: {exc}"
            return result

        deadline = time.time() + self.per_scenario_timeout
        marker = scenario.request[:60]
        evt = self._wait_for_event(marker, baseline, deadline)
        if evt is None:
            result.error = "timeout waiting for coverage event"
            result.outcome_status = "timeout"
            return result

        result.matched_spoke = evt.get("matched_spoke", "")
        result.delegations = evt.get("delegations", []) or []
        result.outcome_status = evt.get("outcome_status", "")
        result.duration_ms = float(evt.get("duration_ms", 0) or 0)
        result.tool_call_count = int(evt.get("tool_call_count", 0) or 0)
        result.matched_expected = (
            result.matched_spoke == scenario.expected_spoke
            or (
                scenario.expected_spoke == "fallback"
                and result.matched_spoke in {"fallback", "direct", ""}
            )
        )

        # Optional multi-turn clarification cycle (best-effort).
        # We can't read Prax's response from the webhook directly, so this
        # branch is largely a no-op without a TeamWork mock — provided as a
        # hook for future expansion. We still respect the flag so users can
        # toggle it from the CLI.
        if self.multi_turn:
            for turn_no in range(2, self.max_turns + 1):
                # Without a way to fetch Prax's reply for harness channels,
                # we send a generic confirmation and stop. This keeps the
                # harness future-proof if the channel API gains a poll
                # endpoint without breaking single-turn scenarios.
                follow_up = self._simulate_user_reply(scenario.request, "")
                if "[DONE]" in follow_up.upper():
                    break
                try:
                    baseline = len(self.client.get_events(days=1, limit=500))
                    self._send_one(follow_up, channel_id)
                    self._wait_for_event(
                        follow_up[:60], baseline, time.time() + self.per_scenario_timeout,
                    )
                    result.turns = turn_no
                except Exception:
                    break

        return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _aggregate_report(
    scenarios: list[Scenario], results: list[ScenarioResult], pareto: dict,
) -> dict:
    by_expected: dict[str, dict] = {}
    for sc in scenarios:
        by_expected.setdefault(sc.expected_spoke, {"expected": 0, "matched": 0, "actual": []})
        by_expected[sc.expected_spoke]["expected"] += 1

    fallback_actual = 0
    for r in results:
        slot = by_expected.setdefault(
            r.expected_spoke, {"expected": 0, "matched": 0, "actual": []},
        )
        slot["actual"].append(r.matched_spoke or "unknown")
        if r.matched_expected:
            slot["matched"] += 1
        if r.matched_spoke in {"fallback", "direct", ""}:
            fallback_actual += 1

    return {
        "scenarios_run": len(results),
        "fallback_count": fallback_actual,
        "fallback_rate": (fallback_actual / len(results)) if results else 0,
        "by_expected_spoke": by_expected,
        "scenario_results": [asdict(r) for r in results],
        "pareto_report": pareto,
    }


def _print_summary(report: dict, wall_time_seconds: float) -> None:
    print()
    print(bold(cyan("=== Coverage Test Harness ===")))
    print(f"Scenarios run: {report['scenarios_run']}")
    print(f"Total turns:   {sum(r['turns'] for r in report['scenario_results'])}")
    minutes = int(wall_time_seconds // 60)
    seconds = int(wall_time_seconds % 60)
    print(f"Wall time:     {minutes}m {seconds}s")
    print()

    print(bold("Coverage by spoke:"))
    for spoke, data in sorted(report["by_expected_spoke"].items()):
        expected = data["expected"]
        matched = data["matched"]
        mark = green("OK") if matched == expected else red("MISS")
        delta_note = ""
        if matched < expected:
            actual_set = set(data["actual"])
            actual_set.discard(spoke)
            if actual_set:
                delta_note = (
                    f" ({expected - matched} routed to: "
                    f"{', '.join(sorted(actual_set))})"
                )
        print(f"  {spoke:<12} {matched}/{expected}  {mark}{delta_note}")
    print()

    fb_rate = report["fallback_rate"] * 100
    rate_str = f"{fb_rate:.1f}%"
    if fb_rate < 5:
        rate_str = green(rate_str)
    elif fb_rate < 30:
        rate_str = yellow(rate_str)
    else:
        rate_str = red(rate_str)
    print(f"Fallback rate: {rate_str}")
    print()

    pareto = report.get("pareto_report") or {}
    clusters = pareto.get("clusters", [])
    fallback_clusters = [c for c in clusters if c.get("fallback_count", 0) > 0]
    if fallback_clusters:
        print(bold("Top fallback clusters:"))
        for i, c in enumerate(fallback_clusters[:5], start=1):
            sample = (c.get("sample_request") or "")[:60]
            count = c.get("fallback_count", 0)
            print(f"  {i}. \"{sample}...\" ({count} events)")
        print()

    hint = pareto.get("decision_hint")
    if hint:
        print(bold("Decision hint:"))
        print(f"  {hint}")
        print()

    # Phase 1 recommendation derived from harness data
    print(bold("Recommended next action for Phase 1:"))
    if fb_rate < 5:
        print("  - Fallback rate negligible — focus on quality of existing spokes.")
    elif fallback_clusters and len(fallback_clusters) <= 3:
        sample = (fallback_clusters[0].get("sample_request") or "")[:50]
        print(
            f"  - Build a specialised spoke for the dominant cluster: \"{sample}...\""
        )
        print("  - Cheaper than building the L1 escape hatch when fallbacks are concentrated.")
    else:
        print("  - Build run_custom_pipeline (Phase 1 / L1 escape hatch).")
        print("  - Fallbacks are scattered across many themes — no single spoke fixes them.")


def _diff_against_real(harness_report: dict, real_report: dict | None) -> None:
    if not real_report:
        return
    print(bold("Comparison against real coverage data:"))
    print(
        f"  real fallback_rate    = {real_report.get('fallback_rate', 0)*100:.1f}% "
        f"(over {real_report.get('total_turns', 0)} turns)"
    )
    print(
        f"  harness fallback_rate = {harness_report['fallback_rate']*100:.1f}% "
        f"(over {harness_report['scenarios_run']} scenarios)"
    )
    real_spokes = set(real_report.get("coverage_by_spoke", {}).keys())
    harness_spokes = set(harness_report["by_expected_spoke"].keys())
    only_harness = harness_spokes - real_spokes
    if only_harness:
        print(f"  spokes only seen in harness: {', '.join(sorted(only_harness))}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the Prax coverage harness against a local instance.",
    )
    p.add_argument("--base-url", default="http://localhost:5001",
                   help="Prax base URL (default: http://localhost:5001)")
    p.add_argument("--scenarios", default=None,
                   help="Comma-separated substrings — only matching scenario "
                        "names will run (default: all)")
    p.add_argument("--multi-turn", action="store_true",
                   help="Enable LLM-as-user clarification cycles")
    p.add_argument("--report-only", action="store_true",
                   help="Skip running scenarios; regenerate report from existing "
                        "harness data on the server")
    p.add_argument("--delay", type=float, default=2.0,
                   help="Seconds to wait between scenarios (default: 2.0)")
    p.add_argument("--scenario-timeout", type=float, default=300.0,
                   help="Per-scenario completion timeout in seconds (default: 300)")
    p.add_argument("--output", default="harness_results.json",
                   help="Where to write the JSON report (default: harness_results.json)")
    p.add_argument("--test-file", default=None,
                   help="Override the harness JSONL path on the Prax server")
    p.add_argument("--keep-test-mode", action="store_true",
                   help="Leave test mode enabled after the run (default: disable)")
    p.add_argument("--no-compare", action="store_true",
                   help="Skip the comparison against real coverage data")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Verbose logging")
    return p.parse_args(argv)


def _filter_scenarios(filter_str: str | None) -> list[Scenario]:
    if not filter_str:
        return list(SCENARIOS)
    needles = [n.strip().lower() for n in filter_str.split(",") if n.strip()]
    return [s for s in SCENARIOS if any(n in s.name.lower() for n in needles)]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    client = PraxClient(args.base_url)

    # Capture the real coverage report BEFORE switching modes so we can
    # diff against it later. Best-effort.
    real_report: dict | None = None
    if not args.no_compare:
        try:
            real_report = client.get_report(days=14)
        except Exception as exc:
            logger.debug("Could not fetch baseline real report: %s", exc)
            real_report = None

    # Always toggle test mode (idempotent — clears in-memory buffer)
    try:
        state = client.enable_test_mode(test_file=args.test_file)
        logger.info("Test mode enabled: %s", state)
    except Exception as exc:
        print(red(f"Failed to enable test mode on Prax: {exc}"))
        print("Is the Prax instance running and reachable at "
              f"{args.base_url}?")
        client.close()
        return 2

    start = time.time()
    results: list[ScenarioResult] = []

    if not args.report_only:
        scenarios = _filter_scenarios(args.scenarios)
        if not scenarios:
            print(yellow("No scenarios matched the filter — nothing to run."))
        print(bold(f"Running {len(scenarios)} scenarios against {args.base_url}..."))
        runner = HarnessRunner(
            client,
            delay_seconds=args.delay,
            per_scenario_timeout=args.scenario_timeout,
            multi_turn=args.multi_turn,
        )
        for idx, sc in enumerate(scenarios, start=1):
            label = f"[{idx:>2}/{len(scenarios)}] {sc.name}"
            print(f"  {label} ... ", end="", flush=True)
            res = runner.run_scenario(sc, idx)
            results.append(res)
            if res.error:
                print(red(f"ERROR: {res.error}"))
            else:
                badge = green("ok") if res.matched_expected else yellow("MISS")
                print(f"{badge}  matched={res.matched_spoke or '?'} "
                      f"expected={sc.expected_spoke}")
            if idx < len(scenarios):
                time.sleep(args.delay)

    # Pull the harness Pareto report from the server
    try:
        pareto = client.get_report(days=14)
    except Exception as exc:
        logger.warning("Failed to fetch Pareto report: %s", exc)
        pareto = {}

    wall = time.time() - start

    aggregated = _aggregate_report(SCENARIOS, results, pareto)

    # Persist
    out_path = Path(args.output).resolve()
    out_path.write_text(json.dumps(aggregated, indent=2, default=str))
    print(f"\nResults written to {out_path}")

    _print_summary(aggregated, wall)
    if not args.no_compare:
        _diff_against_real(aggregated, real_report)

    # Disable test mode unless asked to keep it on
    if not args.keep_test_mode:
        try:
            client.disable_test_mode()
            logger.info("Test mode disabled.")
        except Exception as exc:
            logger.warning("Failed to disable test mode: %s", exc)

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
