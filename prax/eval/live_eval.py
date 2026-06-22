"""Reference-free live-traffic evaluation.

The harness already traces *all* production traffic to
``.prax/graphs/graphs-YYYY-MM-DD.jsonl``.  This module turns that store into
the "live-traffic eval batch" pattern: it samples recently completed traces,
scores each with a cheap LLM judge **without a reference answer**
(grounding / relevancy / correctness), and publishes daily aggregate quality
to Prometheus.  Drift in production quality thus becomes a trended, alertable
signal instead of going unnoticed between manual evals.

Designed to be cheap and resilient: every external touch (LLM, filesystem,
metrics) degrades gracefully, and the judge is injectable for testing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


_JUDGE_PROMPT = """\
You are a reference-free quality judge for an AI assistant.  You are given a \
user request and a summary of how the assistant handled it (the steps/tools it \
ran and what it produced).  There is NO ground-truth answer — judge only what \
is observable.

## User request
{trigger}

## What the assistant did (trace summary)
{trace_summary}

Score three orthogonal axes from 0.0 to 1.0:
- **grounding**: do the assistant's claims appear supported by the tools/steps \
it actually ran (vs. asserted from nowhere)?
- **relevancy**: did it address what the user actually asked?
- **correctness**: does the handling look substantively right?

Respond with EXACTLY this JSON (no other text):
{{"grounding": <float>, "relevancy": <float>, "correctness": <float>, \
"reasoning": "<1 sentence>"}}"""


def _graphs_dir() -> Path:
    try:
        from prax.settings import settings
        base = Path(settings.workspace_dir).resolve()
    except Exception:
        base = Path(".")
    return base / ".prax" / "graphs"


def _iter_recent_graphs(limit: int):
    """Yield up to *limit* persisted trace dicts, newest file first."""
    d = _graphs_dir()
    if not d.exists():
        return
    count = 0
    for filepath in sorted(d.glob("graphs-*.jsonl"), reverse=True):
        try:
            lines = filepath.read_text().strip().splitlines()
        except Exception:
            continue
        # Newest within a file last → iterate reversed.
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue
            count += 1
            if count >= limit:
                return


def _extract_case(graph: dict) -> tuple[str, str] | None:
    """Return ``(trigger, trace_summary)`` for a completed graph, or None.

    Running/empty traces and traces with no user trigger are skipped.
    """
    if not isinstance(graph, dict):
        return None
    if graph.get("status") == "running":
        return None
    trigger = (graph.get("trigger") or "").strip()
    if not trigger:
        return None
    nodes = graph.get("nodes") or []
    # Prefer root-node summaries; fall back to any node summaries.
    roots = [n for n in nodes if not n.get("parent_id")]
    chosen = roots or nodes
    parts = []
    for n in chosen:
        summary = (n.get("summary") or "").strip()
        if summary:
            label = n.get("spoke_or_category") or n.get("name") or "step"
            parts.append(f"- {label}: {summary[:300]}")
    summary_text = "\n".join(parts[:20])
    if not summary_text:
        return None
    return trigger, summary_text


def _judge_trace(trigger: str, trace_summary: str, tier: str = "low") -> dict:
    """Reference-free judge for one trace. Returns an axes dict (empty on error)."""
    try:
        from langchain_core.messages import HumanMessage

        from prax.agent.llm_factory import build_llm
        llm = build_llm(tier=tier, config_key="eval_judge")
        prompt = _JUDGE_PROMPT.format(
            trigger=trigger[:1000], trace_summary=trace_summary[:2000]
        )
        resp = llm.invoke([HumanMessage(content=prompt)])
        text = getattr(resp, "content", "") or str(resp)
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
        data = json.loads(clean)
        return {
            "grounding": float(data.get("grounding", 0.0)),
            "relevancy": float(data.get("relevancy", 0.0)),
            "correctness": float(data.get("correctness", 0.0)),
        }
    except Exception as e:
        logger.debug("live-eval judge failed: %s", e)
        return {}


def run_live_traffic_eval(sample_size: int = 25, judge_tier: str = "low") -> dict:
    """Sample recent traces, score them reference-free, publish aggregate quality.

    Returns a report dict with per-axis averages and how many traces scored.
    Publishes ``prax_eval_quality{axis=...}`` gauges as a side effect.
    """
    scored: list[dict] = []
    for graph in _iter_recent_graphs(sample_size):
        case = _extract_case(graph)
        if not case:
            continue
        axes = _judge_trace(case[0], case[1], tier=judge_tier)
        if axes:
            scored.append(axes)

    n = len(scored)
    if n == 0:
        report = {"sampled": 0, "scored": 0, "axes": {}, "overall": 0.0}
        logger.info("Live-traffic eval: no scorable traces found")
        return report

    axes_avg = {
        axis: round(sum(s.get(axis, 0.0) for s in scored) / n, 3)
        for axis in ("grounding", "relevancy", "correctness")
    }
    overall = round(sum(axes_avg.values()) / 3, 3)

    # Publish to Prometheus (best-effort).
    try:
        from prax.observability.metrics import EVAL_QUALITY
        EVAL_QUALITY.labels(axis="overall").set(overall)
        for axis, val in axes_avg.items():
            EVAL_QUALITY.labels(axis=axis).set(val)
    except Exception:
        logger.debug("Could not publish eval quality metrics", exc_info=True)

    report = {"sampled": n, "scored": n, "axes": axes_avg, "overall": overall}
    logger.info("Live-traffic eval: scored %d traces, overall=%.3f %s", n, overall, axes_avg)
    return report
