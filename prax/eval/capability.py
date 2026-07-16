"""Capability & harness-lift evals — the daily driver.

Where GAIA scores raw end-to-end accuracy, this suite scores **what the harness
contributes**: did Prax route to the right spoke, call the right tool, ground
the answer, and avoid the failure the case is probing — graded
**deterministically** (substring / regex / spoke / tool presence), so a slow or
weak *local judge* never pollutes the signal.  Deterministic grading is the
whole point: "verifiable beats judgeable" applied to capability.

## Harness-lift — the headline metric

Each case runs **twice** on the *same* model:

- **full** — through the complete Prax orchestrator (tools, routing, planning).
- **bare** — a single naked LLM call: no tools, no routing, no system prompt.

``harness_lift = full_content_score − bare_content_score`` answers the question
the whole project rests on: *how much does the scaffolding lift this model?*
Harden that lift against a weak/slow local model and it carries straight over
when a frontier model is dropped in — exactly the "fly like a bird" thesis.

Everything is injectable: ``grade_case`` is pure, and executors are swappable,
so the grading + loading paths are unit-tested with **zero API keys**.  Only the
live executors call a model.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CASES_DIR = Path(__file__).parent / "capability_cases"

# Check kinds that grade the ANSWER TEXT — the part a bare model can also satisfy
# (so harness-lift is measured on these).  vs. kinds that grade the HARNESS
# (routing/tools), which only a full orchestrator run can satisfy.
CONTENT_KINDS = frozenset({"contains", "regex", "absent"})
HARNESS_KINDS = frozenset({"spoke", "tool"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CapCheck:
    """One deterministic assertion against a run.

    kind:
      - ``contains`` — answer contains *value* (case-insensitive substring)
      - ``regex``    — answer matches *value* (regex)
      - ``absent``   — answer does NOT contain *value* (anti-hallucination)
      - ``spoke``    — the run routed to spoke *value* (a ``delegate_*`` call)
      - ``tool``     — the run called tool *value*
    """

    kind: str
    value: str
    weight: float = 1.0


@dataclass
class CapabilityCase:
    id: str
    prompt: str
    checks: list[CapCheck] = field(default_factory=list)
    title: str = ""
    notes: str = ""


@dataclass
class CaseRun:
    """The observable result of running a case through some executor."""

    answer: str = ""
    tools: list[str] = field(default_factory=list)
    spokes: list[str] = field(default_factory=list)
    error: str = ""
    tokens: int = 0  # total LLM tokens this run — the HAL cost axis (accuracy ≠ free)


# ---------------------------------------------------------------------------
# Loading (YAML, mirrors goldens)
# ---------------------------------------------------------------------------

def load_capability_cases(directory: Path | None = None) -> list[CapabilityCase]:
    """Load every ``*.yaml`` capability case from *directory*.

    Malformed files are skipped (logged), never raised — one bad case must not
    take down the suite or the key-free CI guard.
    """
    import yaml

    directory = directory or CASES_DIR
    out: list[CapabilityCase] = []
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
            checks = [
                CapCheck(
                    kind=str(c["kind"]),
                    value=str(c.get("value", "")),
                    weight=float(c.get("weight", 1.0)),
                )
                for c in (data.get("checks") or [])
            ]
            out.append(CapabilityCase(
                id=str(data.get("id") or path.stem),
                prompt=str(data.get("prompt", "")),
                checks=checks,
                title=str(data.get("title", "")),
                notes=str(data.get("notes", "")),
            ))
        except Exception:
            logger.warning("Skipping malformed capability case %s", path, exc_info=True)
    # Guard against duplicate ids — a copy-paste id would otherwise silently drop
    # a case (by_id keeps one) while the batch double-counts it.
    seen: set[str] = set()
    deduped: list[CapabilityCase] = []
    for c in out:
        if c.id in seen:
            logger.warning("Duplicate capability case id %r — skipping the later one", c.id)
            continue
        seen.add(c.id)
        deduped.append(c)
    return deduped


# ---------------------------------------------------------------------------
# Grading (pure / deterministic — no LLM)
# ---------------------------------------------------------------------------

def _check_pass(check: CapCheck, run: CaseRun) -> bool:
    answer = run.answer or ""
    k = check.kind
    if k == "contains":
        return check.value.lower() in answer.lower()
    if k == "absent":
        return check.value.lower() not in answer.lower()
    if k == "regex":
        try:
            return bool(re.search(check.value, answer, re.IGNORECASE | re.DOTALL))
        except re.error:
            return False  # a broken pattern fails closed, never crashes
    # spoke/tool checks accept a `|`-separated set of acceptable names (any-of):
    # the diagnostic is "did the harness take a valid route", and several tools
    # can be the right one (e.g. note_create OR workspace_save both persist a note).
    if k == "spoke":
        return any(v in (run.spokes or []) for v in check.value.split("|"))
    if k == "tool":
        return any(v in (run.tools or []) for v in check.value.split("|"))
    return False


def _weighted(checks: list[CapCheck], run: CaseRun) -> float | None:
    """Weighted pass-fraction over *checks*; ``None`` if there are none."""
    wt = sum(c.weight for c in checks)
    if not wt:
        return None
    got = sum((1.0 if _check_pass(c, run) else 0.0) * c.weight for c in checks)
    return round(got / wt, 3)


def grade_case(case: CapabilityCase, run: CaseRun) -> dict:
    """Score *run* against *case*'s checks. Pure — no LLM, no I/O.

    Returns ``{total, content, harness, passed, scores}`` where ``content`` is
    the answer-text dimension (what bare + full both can satisfy) and ``harness``
    is the routing/tool dimension (only a full run can).  Either dimension is
    ``None`` when the case has no checks of that kind.
    """
    scores = {
        f"{c.kind}:{c.value}": (1.0 if _check_pass(c, run) else 0.0)
        for c in case.checks
    }
    content = _weighted([c for c in case.checks if c.kind in CONTENT_KINDS], run)
    harness = _weighted([c for c in case.checks if c.kind in HARNESS_KINDS], run)
    total = _weighted(case.checks, run) or 0.0
    passed = total >= 0.999  # strict: every weighted check satisfied
    # HAL gaming-detection: an `absent` check trivially passes on EMPTY output, so
    # a "pass" produced by a near-empty answer may be the agent gaming the case by
    # doing nothing rather than actually resisting the attack / answering. Flag it
    # so a suspiciously-cheap pass can't silently inflate the score.
    gaming_suspect = bool(
        passed
        and len((run.answer or "").strip()) < 10
        and any(c.kind == "absent" for c in case.checks)
    )
    return {
        "total": total,
        "content": content,
        "harness": harness,
        "passed": passed,
        "gaming_suspect": gaming_suspect,
        "scores": scores,
    }


# ---------------------------------------------------------------------------
# Executors — full harness vs bare model (same model, the lift is the harness)
# ---------------------------------------------------------------------------

def bare_executor(prompt: str, *, tier: str = "medium",
                  model_override: str | None = None) -> CaseRun:
    """A single naked LLM call — no tools, no routing, no system prompt.

    The control condition for harness-lift: whatever this model produces with
    zero scaffolding.
    """
    try:
        from prax.agent.llm_factory import build_llm
        from prax.eval.telemetry import collect_usage
        llm = build_llm(model=model_override, tier=(None if model_override else tier))
        # Capture bare-side tokens so harness-lift's cost comparison is real —
        # without this the bare column was always 0 (the lift cost ratio was junk).
        with collect_usage() as usage:
            resp = llm.invoke(prompt)
        return CaseRun(answer=_content_text(resp),
                       tokens=int(usage.snapshot().get("total_tokens", 0)))
    except Exception as exc:
        logger.warning("bare_executor failed: %s", exc)
        return CaseRun(error=f"{type(exc).__name__}: {exc}")


def _content_text(resp) -> str:
    """Extract answer text from an AIMessage, tolerating empty/list content.

    An empty completion (``content == ""``) must stay empty — NOT fall back to
    the message repr (which leaks ``additional_kwargs=...`` boilerplate that
    loose regex checks then spuriously match, biasing the bare baseline).
    """
    content = getattr(resp, "content", None)
    if content is None:
        return str(resp)
    if isinstance(content, list):  # provider content-block form
        parts = [str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in content]
        return "".join(parts)
    return str(content)


def orchestrator_executor(prompt: str, *, tier: str = "medium",
                          model_override: str | None = None,
                          case_id: str = "cap", fold_artifacts: bool = True) -> CaseRun:
    """Full Prax harness — the orchestrator with all tools and spokes.

    Runs in an isolated workspace under ``$PRAX_EVAL_DIR`` and captures the tool
    / spoke stream via the shared telemetry collector.

    ``fold_artifacts`` (default True) appends text files the run persisted to the
    answer — correct for capability *persistence* cases whose real output is a
    saved file. **Benchmarks must pass False**: they answer inline, and folding
    the workspace (which can contain Prax's own instructions/soul/plan files)
    corrupts answer extraction — it made GSM8K score 0/5 on correct answers by
    extracting a number out of the appended system-prompt text.
    """
    from prax.agent import tool_registry as _tr
    from prax.eval import PRAX_EVAL_DIR, resolve_task_timeout
    from prax.eval.batch import _run_with_timeout
    from prax.eval.gaia_single import _isolated_prax_scope
    from prax.eval.telemetry import collect_usage
    from prax.settings import settings

    workspace = PRAX_EVAL_DIR / "runs" / f"cap-{case_id}" / "workspace"
    # Start each run from a CLEAN workspace — the path is keyed to case_id only, so
    # without this, artifacts from a prior run (e.g. a prior self-regen candidate)
    # persist and _read_workspace_artifacts folds them into THIS answer, making
    # content checks stick to the best-ever run and corrupting the fitness signal.
    import shutil
    shutil.rmtree(workspace, ignore_errors=True)
    timeout = resolve_task_timeout(None)

    # Cap the ORCHESTRATOR's own self-timeout to the eval per-task budget. Without
    # this, when the eval abandons a timed-out case, its graph-invoke daemon thread
    # keeps running up to agent_run_max_timeout (default 1800s) — a web-research
    # case's abandoned sub-agents then hog the GIL and STARVE the rest of the suite
    # (this wedged harness-lift on the research case). Bounding the agent's own
    # runtime to `timeout` makes an abandoned worker self-terminate promptly.
    orig_run_to, orig_run_max = settings.agent_run_timeout, settings.agent_run_max_timeout
    if timeout:
        settings.agent_run_timeout = min(int(orig_run_to), int(timeout))
        settings.agent_run_max_timeout = min(int(orig_run_max), int(timeout))

    def _go() -> str:
        with _isolated_prax_scope(workspace, case_id, user_prefix="cap-eval"):
            from prax.agent.orchestrator import ConversationAgent
            agent = (
                ConversationAgent(model=model_override) if model_override
                else ConversationAgent(tier=tier)
            )
            return agent.run(
                conversation=[],
                user_input=prompt,
                workspace_context="",
                trigger=f"[capability eval: {case_id}]",
            )

    # Snapshot global isolation state on this (controlling) thread; restore
    # unconditionally so a fired timeout (which abandons the worker mid-scope)
    # can't leave settings.workspace_dir polluted for the next case.
    orig_ws, orig_get = settings.workspace_dir, _tr.get_registered_tools
    with collect_usage() as usage:
        try:
            answer = _run_with_timeout(_go, timeout) or ""
            err = ""
        except Exception as exc:
            answer, err = "", f"{type(exc).__name__}: {exc}"
            logger.warning("orchestrator_executor failed for %s: %s", case_id, exc)
        finally:
            settings.workspace_dir, _tr.get_registered_tools = orig_ws, orig_get
            settings.agent_run_timeout, settings.agent_run_max_timeout = orig_run_to, orig_run_max

    # Credit persisted artifacts: a persistence case's real output is the saved
    # file, not the chat confirmation. Fold any text the harness wrote into the
    # answer so CONTENT checks (and harness-lift) see the harness's actual work.
    # Benchmarks disable this (fold_artifacts=False) — they score the direct answer.
    artifacts = _read_workspace_artifacts(workspace) if fold_artifacts else ""
    full_answer = answer + (f"\n\n{artifacts}" if artifacts else "") if answer else artifacts
    return CaseRun(answer=full_answer, tools=list(usage.tools), spokes=usage.spokes(),
                   error=err, tokens=int(usage.snapshot().get("total_tokens", 0)))


_ARTIFACT_EXTS = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".py", ".csv", ".html"})


def _read_workspace_artifacts(workspace, *, max_total: int = 8000) -> str:
    """Concatenate text files the harness persisted under the run workspace."""
    try:
        root = Path(workspace)
        if not root.exists():
            return ""
        chunks, used = [], 0
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _ARTIFACT_EXTS:
                continue
            try:
                text = path.read_text(errors="ignore")
            except Exception:
                continue
            if not text.strip():
                continue
            snippet = text[: max(0, max_total - used)]
            chunks.append(f"[artifact:{path.name}]\n{snippet}")
            used += len(snippet)
            if used >= max_total:
                break
        return "\n\n".join(chunks)
    except Exception:
        return ""


def _orchestrator_model(tier: str, model_override: str | None) -> str:
    """Resolve the concrete model the orchestrator WOULD use, so harness-lift can
    pin both full and bare to the SAME model — otherwise a tier/model swap pulled
    from llm_routing config or ORCHESTRATOR_* would masquerade as 'lift'.
    """
    if model_override:
        return model_override
    try:
        from prax.agent.model_tiers import resolve_model
        from prax.plugins.llm_config import get_component_config
        cfg = get_component_config("orchestrator") or {}
        if cfg.get("model"):
            return str(cfg["model"])
        return resolve_model(tier or cfg.get("tier") or "medium")
    except Exception:
        try:
            from prax.agent.model_tiers import resolve_model
            return resolve_model(tier or "medium")
        except Exception:
            return tier or "medium"


# ---------------------------------------------------------------------------
# Suites (resumable via batch)
# ---------------------------------------------------------------------------

def _resolve_concurrency(concurrency: int | None) -> int:
    if concurrency is not None:
        return concurrency
    try:
        from prax.settings import settings
        return int(getattr(settings, "eval_concurrency", 1) or 1)
    except Exception:
        return 1


def run_capability_suite(cases: list[CapabilityCase] | None = None, *,
                         tier: str = "medium", model_override: str | None = None,
                         executor=None, suite_dir: Path | None = None,
                         resume: bool = True, concurrency: int | None = None,
                         skip: list[str] | None = None) -> dict:
    """Run the capability suite through the full harness, deterministically graded.

    *executor* is ``callable(CapabilityCase) -> CaseRun`` (default: the live
    orchestrator); inject a stub to test the suite with no API key.
    """
    from prax.eval import PRAX_EVAL_DIR
    from prax.eval.batch import run_batch

    cases = cases if cases is not None else load_capability_cases()
    if skip:
        # Excluding a case (e.g. one that depends on a currently-dead external
        # service) keeps A/B arms comparable; the summary still names it.
        cases = [c for c in cases if c.id not in set(skip)]
    by_id = {c.id: c for c in cases}
    live = executor is None
    ex = executor or (lambda c: orchestrator_executor(
        c.prompt, tier=tier, model_override=model_override, case_id=c.id))

    # Stable, resume-safe suite_dir keyed to the run CONFIG (not wall-clock).
    cfg_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(model_override or tier or "default"))
    suite_dir = suite_dir or (PRAX_EVAL_DIR / "suites" / f"capability-{cfg_slug}")
    # The live orchestrator mutates global state per case → force serial. A pure
    # injected executor (tests) may parallelize.
    eff_conc = 1 if live else _resolve_concurrency(concurrency)

    def _run_one(case_id: str) -> dict:
        case = by_id[case_id]
        run = ex(case)
        g = grade_case(case, run)
        return {
            "id": case_id, "title": case.title, **g,
            "spokes": run.spokes, "tools_used": run.tools,
            "tokens": run.tokens,
            "answer_preview": (run.answer or "")[:500],
            "error": run.error or None,
        }

    def _summarize(results: list[dict]) -> dict:
        graded = [r for r in results if not r.get("error")]
        n = len(graded)
        passed = sum(1 for r in graded if r.get("passed"))
        total_tokens = sum(int(r.get("tokens", 0)) for r in graded)
        return {
            "graded": n,
            "passed": passed,
            "pass_rate": round(passed / n, 3) if n else 0.0,
            "avg_total": round(sum(r.get("total", 0.0) for r in graded) / n, 3) if n else 0.0,
            # HAL cost axis — never report accuracy without cost. Efficiency =
            # pass-rate points per 1k tokens, so a pricier model must EARN its lift.
            "avg_tokens": round(total_tokens / n) if n else 0,
            "pass_per_1k_tokens": round(passed / (total_tokens / 1000), 3) if total_tokens else None,
            # HAL gaming-detection — passes that may be gamed (empty answer clearing
            # an absent-check). Non-zero means the pass_rate is partly unearned.
            "gaming_suspects": sum(1 for r in graded if r.get("gaming_suspect")),
        }

    return run_batch(
        [c.id for c in cases], _run_one, out_dir=suite_dir, label="capability",
        concurrency=eff_conc, resume=resume,
        per_item_timeout_s=None, summarize=_summarize,
    )


def run_harness_lift(cases: list[CapabilityCase] | None = None, *,
                     tier: str = "medium", model_override: str | None = None,
                     full_executor=None, bare_executor_fn=None,
                     suite_dir: Path | None = None, resume: bool = True) -> dict:
    """Measure harness-lift: full orchestrator vs bare model, same model, per case.

    Reports per-case ``harness_lift`` (full − bare on content checks) and an
    aggregate average — the core "does the scaffolding lift this model?" number.
    Both executors are injectable for key-free testing.
    """
    from prax.eval import PRAX_EVAL_DIR
    from prax.eval.batch import run_batch

    cases = cases if cases is not None else load_capability_cases()
    by_id = {c.id: c for c in cases}
    # Pin BOTH full and bare to the SAME concrete model so the lift measures the
    # SCAFFOLDING, not a tier/model swap the orchestrator config might introduce.
    pinned = _orchestrator_model(tier, model_override) if full_executor is None or bare_executor_fn is None else None
    full_ex = full_executor or (lambda c: orchestrator_executor(
        c.prompt, tier=tier, model_override=pinned, case_id=c.id))
    bare_ex = bare_executor_fn or (lambda c: bare_executor(
        c.prompt, tier=tier, model_override=pinned))

    # Stable, resume-safe suite_dir keyed to the pinned model (not wall-clock).
    cfg_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(pinned or model_override or tier or "default"))
    suite_dir = suite_dir or (PRAX_EVAL_DIR / "suites" / f"harness-lift-{cfg_slug}")

    def _run_one(case_id: str) -> dict:
        case = by_id[case_id]
        full, bare = full_ex(case), bare_ex(case)
        gf, gb = grade_case(case, full), grade_case(case, bare)
        cf, cb = gf.get("content"), gb.get("content")
        lift = round(cf - cb, 3) if (cf is not None and cb is not None) else None
        return {
            "id": case_id, "title": case.title, "model": pinned,
            "full_total": gf["total"], "bare_total": gb["total"],
            "full_content": cf, "bare_content": cb, "harness_lift": lift,
            "spokes": full.spokes, "tools_used": full.tools,
            "full_tokens": full.tokens, "bare_tokens": bare.tokens,
            "full_error": full.error or None, "bare_error": bare.error or None,
        }

    def _summarize(results: list[dict]) -> dict:
        ok = [r for r in results if not r.get("full_error") and not r.get("bare_error")]
        lifts = [r["harness_lift"] for r in ok if r.get("harness_lift") is not None]
        cfs = [r["full_content"] for r in ok if r.get("full_content") is not None]
        cbs = [r["bare_content"] for r in ok if r.get("bare_content") is not None]
        ft = sum(int(r.get("full_tokens", 0)) for r in ok)
        bt = sum(int(r.get("bare_tokens", 0)) for r in ok)
        return {
            "cases": len(ok),
            "avg_full_content": round(sum(cfs) / len(cfs), 3) if cfs else None,
            "avg_bare_content": round(sum(cbs) / len(cbs), 3) if cbs else None,
            "avg_harness_lift": round(sum(lifts) / len(lifts), 3) if lifts else None,
            # HAL cost axis — what the harness's lift COSTS in tokens (full vs bare).
            "avg_full_tokens": round(ft / len(ok)) if ok else 0,
            "avg_bare_tokens": round(bt / len(ok)) if ok else 0,
        }

    return run_batch(
        [c.id for c in cases], _run_one, out_dir=suite_dir, label="harness-lift",
        concurrency=1, resume=resume, per_item_timeout_s=None, summarize=_summarize,
    )
