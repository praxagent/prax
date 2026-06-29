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
import re
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
    # Optional deterministic check ("verifiable beats judgeable"): a regex.
    # When set, this criterion is scored WITHOUT the LLM judge — 1.0 if the
    # output matches the pattern, else 0.0 — so the check can't drift or be
    # gamed. A golden whose criteria are ALL verified needs no judge at all.
    verify: str = ""


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
                    verify=str(c.get("verify", "")),
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

## Rubric — score each criterion 0 or 1 (BINARY — no partial credit)
{criteria}

Score every criterion by its KEY: 1 = the criterion is satisfied, 0 = it is not.
Be strict and decisive — do NOT give fractional/partial scores; pick 0 or 1.

Resist being gamed by impressive-but-empty writing (weak judges reward this):
- Score on **substance**, not polish. An answer that *sounds* rigorous — sweeping
  claims, borrowed jargon, a templated/formulaic structure that pattern-matches a
  good answer — but lacks **specific, concrete, verifiable** content does NOT
  satisfy a criterion. Score it 0.
- Reward specificity that **directly addresses this task**: concrete evidence,
  named sources, implementable detail. Penalize generic vocabulary and confident
  assertions with nothing behind them.
- If you can't point to the exact span of the answer that satisfies a criterion,
  it isn't satisfied.

## The assistant's answer
{output}

Respond with EXACTLY this JSON (no other text):
{{"scores": {{{score_keys}}}, "reasoning": "<1-2 sentences>"}}"""


# A stronger model re-auditing ONLY the criteria the weak judge passed (the gaming
# direction). maker != checker applied to the judge itself.
_AUDIT_PROMPT = """\
You are a senior auditor double-checking a weaker grader's PASSES. The weaker
grader marked each criterion below as satisfied — but weak graders are fooled by
impressive-but-empty writing (sweeping claims, borrowed jargon, templated rigor).
For each criterion, decide whether the answer GENUINELY satisfies it with specific,
concrete, verifiable content you can point to — or whether it only appears to.

## The task
{task}

## Criteria the weak grader PASSED (re-audit each — confirm or veto)
{criteria}

## The assistant's answer
{output}

For each KEY: 1 = genuinely satisfied (you can point to the exact span), 0 = VETO
(impressive but not actually satisfied / unsupported). Be skeptical; default to 0
when you cannot point to concrete substance.
Respond with EXACTLY this JSON (no other text):
{{"scores": {{{score_keys}}}, "reasoning": "<1-2 sentences>"}}"""


def _default_judge(prompt: str, tier: str = "low") -> str:
    from prax.agent.llm_factory import build_llm

    llm = build_llm(tier=tier, config_key="eval_judge")
    response = llm.invoke(prompt)
    return response.content if hasattr(response, "content") else str(response)


def _clean_json(text: str) -> dict:
    """Parse a judge/auditor JSON reply, tolerating ```-fenced output."""
    clean = (text or "").strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()
    return json.loads(clean)


def _binarize(raw) -> float:
    """Snap a judge score to 0/1 (binary classification beats Likert)."""
    try:
        return 1.0 if float(raw) >= 0.5 else 0.0
    except (TypeError, ValueError):
        return 0.0


def score_golden(golden: Golden, output: str, *, judge=None, tier: str = "low",
                 audit: bool = False, auditor=None, audit_tier: str = "high") -> dict:
    """Score *output* against *golden*'s rubric.

    Returns ``{total, scores:{key:0/1}, reasoning, error?}``. Two scoring paths:

    - **Verifiable** criteria (those with a ``verify`` regex) are scored
      DETERMINISTICALLY — 1.0 on a regex match, else 0.0 — with no LLM. If *every*
      criterion is verifiable, no judge is called at all.
    - **Judged** criteria are scored by the LLM judge as **binary** (0/1).

    With ``audit=True``, a stronger model (``audit_tier``, default high) re-checks
    only the criteria the cheap judge *passed* (the gaming direction) and may veto
    (1 -> 0); the result then carries ``audited`` + ``vetoed`` keys. The auditor is
    an enhancement, never a hard dependency — its failure degrades to the base
    scores. *judge*/*auditor* are optional ``callable(prompt)->str`` (default
    low/high-tier LLMs) so tests can score without any API key.
    """
    out = output or ""
    scores: dict[str, float] = {}
    judged = []
    for c in golden.rubric:
        if c.verify:
            try:
                scores[c.key] = 1.0 if re.search(c.verify, out, re.IGNORECASE | re.DOTALL) else 0.0
            except re.error:
                scores[c.key] = 0.0  # a broken pattern fails closed, never crashes
        else:
            judged.append(c)

    reasoning = ""
    if judged:  # only invoke the LLM if something actually needs judging
        criteria = "\n".join(
            f"- {c.key} (weight {c.weight}): {c.description}" for c in judged
        )
        score_keys = ", ".join(f'"{c.key}": <0 or 1>' for c in judged)
        prompt = _SCORE_PROMPT.format(
            task=golden.prompt.strip(),
            criteria=criteria,
            output=out[:4000],
            score_keys=score_keys,
        )
        # Default judge takes (prompt, tier); an injected judge takes just (prompt).
        try:
            text = _default_judge(prompt, tier=tier) if judge is None else judge(prompt)
        except Exception as exc:
            logger.warning("Golden judge failed for %s: %s", golden.id, exc)
            return {"total": 0.0, "scores": {}, "reasoning": "", "error": str(exc)}

        try:
            data = _clean_json(text)
        except Exception as exc:
            return {"total": 0.0, "scores": {}, "reasoning": "", "error": f"unparseable judge output: {exc}"}

        for c in judged:
            scores[c.key] = _binarize(data.get("scores", {}).get(c.key, 0.0))
        reasoning = str(data.get("reasoning", ""))

    # Supervising auditor (opt-in): a stronger model re-checks only the criteria
    # the cheap judge PASSED — the gaming direction (impressive vacuity -> false
    # 1s) — and may veto. maker != checker, applied to the judge itself. `verify`
    # criteria are deterministic ground truth and are never audited.
    vetoed: list[str] = []
    if audit:
        passed = [c for c in judged if scores.get(c.key) == 1.0]
        if passed:
            a_prompt = _AUDIT_PROMPT.format(
                task=golden.prompt.strip(),
                criteria="\n".join(f"- {c.key}: {c.description}" for c in passed),
                output=out[:4000],
                score_keys=", ".join(f'"{c.key}": <0 or 1>' for c in passed),
            )
            try:
                a_text = auditor(a_prompt) if auditor is not None else _default_judge(a_prompt, tier=audit_tier)
                a_scores = _clean_json(a_text).get("scores", {})
                for c in passed:
                    if _binarize(a_scores.get(c.key, 1.0)) == 0.0:
                        scores[c.key] = 0.0
                        vetoed.append(c.key)
            except Exception as exc:  # auditor is an enhancement, never a hard dep
                logger.warning("Golden auditor failed for %s: %s (keeping base scores)", golden.id, exc)

    wt = golden.weight_total() or 1.0
    total = sum(scores.get(c.key, 0.0) * c.weight for c in golden.rubric) / wt
    result = {"total": round(total, 3), "scores": scores, "reasoning": reasoning}
    if audit:
        result["audited"] = True
        result["vetoed"] = vetoed
    return result


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
                     judge=None, replay_fn=None, tier: str = "low",
                     audit: bool | None = None) -> dict:
    """Run the golden suite.

    With ``replay=False`` (default for cheap/key-free listing) every golden is
    reported by id + status with ``total=None`` — useful for ``make eval`` to
    *surface* tracked goldens without paying for replays. With ``replay=True`` it
    replays each prompt through the agent and scores it against the rubric.

    ``audit`` enables the high-tier supervising auditor (veto on the cheap judge's
    passes). ``None`` (default) reads the ``EVAL_AUDITOR_ENABLED`` flag — off by
    default, so the suite stays cheap unless explicitly opted in.
    """
    if audit is None:
        try:
            from prax.settings import settings
            audit = bool(getattr(settings, "eval_auditor_enabled", False))
        except Exception:
            audit = False
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
            row.update(score_golden(g, output, judge=judge, tier=tier, audit=audit))
        results.append(row)

    scored = [r for r in results if isinstance(r.get("total"), (int, float))]
    avg = round(sum(r["total"] for r in scored) / len(scored), 3) if scored else None
    return {"total": len(results), "scored": len(scored), "avg": avg, "results": results}
