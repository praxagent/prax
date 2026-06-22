"""Curated 'golden' evals — forward-looking, rubric-scored quality targets.

Unlike the regression suite (`prax.eval.runner`, which replays observed
*failures*), a **golden** is a quality target we want to hit: a prompt plus a
weighted rubric describing what a great answer must contain. Goldens live as
reviewable YAML under ``prax/eval/goldens/`` so they can't be silently lost — a
key-free CI test (`tests/test_eval_goldens.py`) asserts they load and stay
well-formed, and ``make eval`` surfaces them every run.

The first golden (``research_multiperspective``) encodes the STORM criteria
(multi-perspective coverage, contradiction surfacing, grounded/cited synthesis,
self-critique) so the deep-research roadmap item (IDEAS_BACKLOG #13) is *tracked
in evals*: today it measures the baseline gap; when grounded multi-perspective
``deep_dive`` ships, the same rubric measures the gain.

Everything degrades gracefully: loading + structural validation are pure and
key-free (deterministic for ``make ci``); LLM scoring and agent replay only run
when explicitly invoked (they need provider keys), and the judge/replay are
injectable for testing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

GOLDENS_DIR = Path(__file__).parent / "goldens"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RubricCriterion:
    key: str
    weight: float
    description: str


@dataclass
class Golden:
    id: str
    title: str
    kind: str
    prompt: str
    rubric: list[RubricCriterion] = field(default_factory=list)
    source: str = ""
    status: str = ""
    notes: str = ""

    def weight_total(self) -> float:
        return sum(c.weight for c in self.rubric)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_goldens(directory: Path | None = None) -> list[Golden]:
    """Load every ``*.yaml`` golden from *directory* (default ``goldens/``).

    Malformed files are skipped (logged), never raised — one bad golden must not
    take down the suite or the CI guard.
    """
    import yaml

    directory = directory or GOLDENS_DIR
    out: list[Golden] = []
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
            rubric = [
                RubricCriterion(
                    key=str(c["key"]),
                    weight=float(c.get("weight", 1.0)),
                    description=str(c.get("description", "")),
                )
                for c in (data.get("rubric") or [])
            ]
            out.append(Golden(
                id=str(data.get("id") or path.stem),
                title=str(data.get("title", "")),
                kind=str(data.get("kind", "")),
                prompt=str(data.get("prompt", "")),
                rubric=rubric,
                source=str(data.get("source", "")),
                status=str(data.get("status", "")),
                notes=str(data.get("notes", "")),
            ))
        except Exception:
            logger.warning("Skipping malformed golden %s", path, exc_info=True)
    return out


# ---------------------------------------------------------------------------
# Scoring (LLM judge — injectable)
# ---------------------------------------------------------------------------

_SCORE_PROMPT = """\
You are grading an AI assistant's research answer against a fixed rubric.

## The task the assistant was given
{task}

## Rubric — score each criterion 0.0–1.0
{criteria}

## The assistant's answer
{output}

Score every criterion by its KEY. 1.0 = fully satisfied, 0.0 = absent.
Respond with EXACTLY this JSON (no other text):
{{"scores": {{{score_keys}}}, "reasoning": "<1-2 sentences>"}}"""


def _default_judge(prompt: str, tier: str = "low") -> str:
    from prax.agent.llm_factory import build_llm

    llm = build_llm(tier=tier, config_key="eval_judge")
    response = llm.invoke(prompt)
    return response.content if hasattr(response, "content") else str(response)


def score_golden(golden: Golden, output: str, *, judge=None, tier: str = "low") -> dict:
    """Score *output* against *golden*'s rubric.

    Returns ``{total, scores:{key:0..1}, reasoning, error?}``. ``total`` is the
    weight-normalised average. *judge* is an optional ``callable(prompt)->str``
    (defaults to a low-tier LLM) so tests can score without any API key.
    """
    criteria = "\n".join(
        f"- {c.key} (weight {c.weight}): {c.description}" for c in golden.rubric
    )
    score_keys = ", ".join(f'"{c.key}": <float>' for c in golden.rubric)
    prompt = _SCORE_PROMPT.format(
        task=golden.prompt.strip(),
        criteria=criteria,
        output=(output or "")[:4000],
        score_keys=score_keys,
    )
    # Default judge takes (prompt, tier); an injected judge takes just (prompt).
    try:
        text = _default_judge(prompt, tier=tier) if judge is None else judge(prompt)
    except Exception as exc:
        logger.warning("Golden judge failed for %s: %s", golden.id, exc)
        return {"total": 0.0, "scores": {}, "reasoning": "", "error": str(exc)}

    clean = (text or "").strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()
    try:
        data = json.loads(clean)
    except Exception as exc:
        return {"total": 0.0, "scores": {}, "reasoning": "", "error": f"unparseable judge output: {exc}"}

    scores = {c.key: float(data.get("scores", {}).get(c.key, 0.0)) for c in golden.rubric}
    wt = golden.weight_total() or 1.0
    total = sum(scores[c.key] * c.weight for c in golden.rubric) / wt
    return {"total": round(total, 3), "scores": scores, "reasoning": str(data.get("reasoning", ""))}


# ---------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------

def _default_replay(prompt: str) -> str:
    """Replay a golden's prompt through the live agent (needs provider keys)."""
    from prax.eval.runner import _replay_input
    from prax.settings import settings

    user_id = getattr(settings, "prax_user_id", "") or "eval-golden"
    return _replay_input(user_id, prompt)


def run_golden_suite(*, replay: bool = True, kind: str | None = None,
                     judge=None, replay_fn=None, tier: str = "low") -> dict:
    """Run the golden suite.

    With ``replay=False`` (default for cheap/key-free listing) every golden is
    reported by id + status with ``total=None`` — useful for ``make eval`` to
    *surface* tracked goldens without paying for replays. With ``replay=True`` it
    replays each prompt through the agent and scores it against the rubric.
    """
    goldens = load_goldens()
    if kind:
        goldens = [g for g in goldens if g.kind == kind]

    results: list[dict] = []
    for g in goldens:
        row = {"id": g.id, "title": g.title, "kind": g.kind, "status": g.status, "source": g.source}
        if not replay:
            row.update(total=None, skipped="replay disabled")
        elif not g.rubric:
            # Comparator-scored goldens (e.g. doc-extraction: schema-validity +
            # per-field accuracy) carry no LLM rubric — they're scored by a
            # deterministic comparator wired in during Phase 0, not by this judge.
            # Surface them (tracked) without a misleading fake score.
            row.update(total=None, skipped="comparator-scored — not wired "
                       "(see docs/research/diy-document-extraction-model.md)")
        else:
            output = (replay_fn or _default_replay)(g.prompt)
            row.update(score_golden(g, output, judge=judge, tier=tier))
        results.append(row)

    scored = [r for r in results if isinstance(r.get("total"), (int, float))]
    avg = round(sum(r["total"] for r in scored) / len(scored), 3) if scored else None
    return {"total": len(results), "scored": len(scored), "avg": avg, "results": results}
