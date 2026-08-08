"""Self-regeneration loop (#29) — the propose → verify → keep engine.

This is the breakthrough-shaped loop the 2026 landscape sweep pinned down: the
same skeleton behind Karpathy's autoresearch, AlphaEvolve, DGM, and
pipeline-math — **a proposer + an un-gameable verifier + iterate** — pointed at
the one surface Prax can safely and machine-verifiably improve: its **scaffold**
(the system prompt), graded by the deterministic capability suite.

## The safety rules it enforces (learned from DGM's failures)

- **The fitness function and the overseer live OUTSIDE the editable surface.**
  The loop may only edit the *system-prompt overlay*; it cannot touch the
  capability suite (the verifier) or the auditor (the overseer). DGM, given a
  hallucination-detection reward, deleted the markers the detector keyed on —
  the only working defense is keeping verifier + overseer out of reach.
- **Un-gameable verifier.** Scoring is the deterministic capability suite
  (substring / regex / spoke / tool checks), not an LLM judge that can be
  sweet-talked.
- **Anti-spike overseer.** A separate auditor vetoes any patch that encodes
  specifics of the eval (the CLAUDE.md "never spike benchmarks" rule): a kept
  change must be an *abstraction of a problem class*, not a memorized answer.
- **Transparent lineage.** Every variant — kept or discarded — is archived with
  its parent, patch, score, and audit verdict.
- **Propose-only by default.** The winner is written as a reviewable PROPOSAL;
  it is auto-applied only when ``apply=True`` AND ``SELF_REGEN_ENABLED``.

Everything is injectable (proposer / evaluator / auditor), so the loop logic is
unit-tested with **zero API keys**; only the live functions call a model.
"""
from __future__ import annotations

import contextlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model + lineage
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    """One proposed scaffold change and its graded outcome."""

    id: str
    round: int
    parent_id: str
    patch: str
    score: float
    baseline: float
    delta: float
    audit_ok: bool
    audit_reason: str
    kept: bool
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# The editable surface — a system-prompt OVERLAY applied for the duration of a run
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def override_system_prompt(patched_prompt: str):
    """Make the orchestrator load *patched_prompt* as its system prompt.

    Patches ``orchestrator._load_system_prompt`` so a candidate can be graded by
    the live capability suite without writing anything to disk.  Restored on exit.
    """
    try:
        from prax.agent import orchestrator as orch
    except Exception:
        yield
        return
    original = getattr(orch, "_load_system_prompt", None)
    if original is None:
        yield
        return
    orch._load_system_prompt = lambda: patched_prompt
    try:
        yield
    finally:
        orch._load_system_prompt = original


def _base_system_prompt() -> str:
    try:
        from prax.agent.orchestrator import _load_system_prompt
        return _load_system_prompt() or ""
    except Exception:
        return ""


def _apply_overlay(base: str, patch: str) -> str:
    """Compose the base prompt with a proposed additive overlay."""
    patch = (patch or "").strip()
    if not patch:
        return base
    return f"{base}\n\n## Self-improvement overlay (candidate)\n{patch}\n"


# ---------------------------------------------------------------------------
# Live proposer / evaluator / auditor (keyed) — all injectable
# ---------------------------------------------------------------------------

_PROPOSER_PROMPT = """\
You are improving an AI assistant's SYSTEM PROMPT. Propose ONE concise additional
instruction (2-6 sentences) that would make the assistant better on the weakness
below.

## Weakness signal (abstract — the dimension that scored low, NOT specific tasks)
{weak_signal}

## Hard rules
- Propose a GENERAL principle that improves the assistant on the whole CLASS of
  queries — never a specific fact, answer, or task-specific hack. If someone who
  knew the test set could tell which tasks you were targeting, you have failed.
- Output ONLY the instruction text — no preamble, no markdown headers.
"""

_AUDITOR_PROMPT = """\
You are a benchmark-integrity auditor. A self-improvement loop proposed adding the
instruction below to an AI assistant's system prompt. Decide whether it is a
legitimate GENERAL improvement or a benchmark SPIKE.

## Proposed addition
{patch}

## Veto (approve=false) if the addition:
- encodes specifics of an evaluation (memorized answers/facts, task-specific
  instructions, answer-format tricks aimed at a known test), OR
- would let someone who knows the eval set identify which tasks it targets.
## Approve (approve=true) only if it is an abstraction of a problem CLASS that
helps on all queries in that class.

Respond with EXACTLY this JSON: {{"approve": <bool>, "reason": "<1 sentence>"}}"""


# Patches longer than this are vetoed outright — so the overseer always audits the
# EXACT bytes that get evaluated/applied (no truncated-tail spike).
MAX_PATCH_CHARS = 1500


# Inoculation framing (Anthropic, "emergent misalignment from reward hacking" —
# docs/research/emergent-misalignment-reward-hacking.md). Explicitly framing a
# grader-satisfying task as a NARROW eval breaks the semantic link between "satisfy
# this check" and a broader "cheating is acceptable" orientation, so it doesn't
# generalize into misaligned behavior. Cheap + capability-neutral. Applied to the
# self-regen proposer here; the SAME wrapper belongs on any future weights-level
# fine-tuning grader task, where the generalization risk is real.
INOCULATION_PREAMBLE = (
    "[Evaluation-harness context] This is a NARROW evaluation harness. Your only "
    "job here is to propose a genuine, GENERAL improvement that the deterministic "
    "check legitimately rewards. Do NOT attempt to game, hack, or spike the check — "
    "it is disallowed and pointless (an independent overseer vetoes benchmark "
    "spikes), and satisfying this narrow evaluation does NOT license deceptive, "
    "sabotaging, or otherwise misaligned behavior anywhere else.\n\n"
)


def inoculate(prompt: str) -> str:
    """Prepend the inoculation preamble to a grader-optimizing task prompt."""
    return INOCULATION_PREAMBLE + (prompt or "")


def _extract_text(resp) -> str:
    """List-aware content extraction — providers with extended thinking return
    ``.content`` as a list of blocks, where ``str(resp)`` would be a repr, not text."""
    from prax.eval.capability import _content_text
    return _content_text(resp).strip()


def _default_proposer(base_prompt: str, weak_signal: str, tier: str = "high") -> str:
    from prax.agent.llm_factory import build_llm
    llm = build_llm(tier=tier, config_key="self_regen_proposer")
    # Inoculate the proposer's task framing so "satisfy the check" can't bleed into
    # a broader "cheating is acceptable" orientation (reward-hacking → misalignment).
    resp = llm.invoke(inoculate(_PROPOSER_PROMPT.format(weak_signal=weak_signal[:2000])))
    return _extract_text(resp)


def _default_auditor(base_prompt: str, patch: str, tier: str = "high") -> tuple[bool, str]:
    """Anti-spike overseer — lives OUTSIDE the editable surface. Fail-closed.

    Audits the FULL patch (patches over ``MAX_PATCH_CHARS`` are rejected upstream,
    so nothing is truncated), and extracts the first JSON object so a leading
    thinking/prose block can't force a spurious fail-closed veto.
    """
    import re

    from prax.agent.llm_factory import build_llm
    from prax.settings import settings
    try:
        # A GRADER, not a generator. Without an explicit temperature this
        # inherited AGENT_TEMPERATURE (0.7) — the knob tuned to make the
        # agent write well — so the same answer could be graded pass on one
        # run and fail on the next, and a published number was not
        # reproducible. See JUDGE_TEMPERATURE.
        llm = build_llm(tier=tier, config_key="self_regen_auditor",
                        temperature=settings.judge_temperature)
        resp = llm.invoke(_AUDITOR_PROMPT.format(patch=patch))
        text = _extract_text(resp)
        m = re.search(r"\{.*\}", text, re.DOTALL)  # first JSON object, past any preamble
        data = json.loads(m.group(0) if m else text)
        return bool(data.get("approve", False)), str(data.get("reason", ""))
    except Exception as exc:
        # Fail CLOSED: if the overseer can't render a verdict, the patch is vetoed.
        return False, f"auditor unavailable — vetoed (fail-closed): {exc}"


def _spike_answer_tokens() -> set[str]:
    """Specific ANSWER tokens from the capability checks a patch must not encode.

    Only digit-bearing values (e.g. '536') — memorizing a numeric answer is the
    clearest spike — so common topic words in checks don't cause false vetoes.
    """
    try:
        from prax.eval.capability import load_capability_cases
        toks: set[str] = set()
        for c in load_capability_cases():
            for chk in c.checks:
                v = (chk.value or "").strip()
                if chk.kind in ("contains", "absent") and len(v) >= 2 and any(ch.isdigit() for ch in v):
                    toks.add(v.lower())
        return toks
    except Exception:
        return set()


def _deterministic_spike_veto(patch: str) -> str:
    """A deterministic backstop the LLM auditor can't be talked out of: veto a
    patch that literally contains an eval answer token. Returns a reason or ''."""
    low = (patch or "").lower()
    for t in _spike_answer_tokens():
        if t in low:
            return f"contains eval answer token {t!r}"
    return ""


def _default_evaluator(patch: str, *, base_prompt: str, tier: str) -> float:
    """Score the candidate on the deterministic capability suite (the verifier).

    Applies the overlay for the duration of the suite and returns the average
    total.  This is the un-gameable fitness function — the loop cannot edit it.
    """
    import shutil
    import tempfile

    from prax.eval.capability import load_capability_cases, run_capability_suite
    from prax.settings import settings as _s
    patched = _apply_overlay(base_prompt, patch)
    n_cases = len(load_capability_cases()) or 1
    # Isolated suite dir per evaluation — the capability suite's default dir is
    # stable-per-config, so baseline and every candidate would otherwise collide.
    suite_dir = Path(tempfile.mkdtemp(prefix="self_regen_eval_"))
    # Prompt selectivity would strip the candidate overlay as "irrelevant" for
    # some cases — force it off so the change being graded is always present.
    _orig_sel = getattr(_s, "prompt_selectivity_enabled", False)
    try:
        _s.prompt_selectivity_enabled = False
        with override_system_prompt(patched):
            summary = run_capability_suite(tier=tier, resume=False, suite_dir=suite_dir)
    finally:
        _s.prompt_selectivity_enabled = _orig_sel
        shutil.rmtree(suite_dir, ignore_errors=True)
    agg = summary.get("aggregate") or {}
    graded = int(agg.get("graded", 0) or 0)
    avg = float(agg.get("avg_total", 0.0) or 0.0)
    # Errored/timed-out cases score 0 (fixed denominator over the FULL case set),
    # so a candidate that induces timeouts can't shrink the denominator to the
    # easy cases and inflate its score — protecting the un-gameable fitness.
    return avg * graded / n_cases


def _weak_signal_from_baseline(tier: str) -> str:
    """Abstract description of where the current scaffold is weakest — the
    dimension(s) that scored low, deliberately WITHOUT the specific cases (so the
    proposer can't memorize them)."""
    try:
        from prax.eval.capability import (
            grade_case,
            load_capability_cases,
            orchestrator_executor,
        )
        cases = load_capability_cases()
        dims: dict[str, list[float]] = {}
        for c in cases:
            run = orchestrator_executor(c.prompt, tier=tier, case_id=c.id)
            g = grade_case(c, run)
            for key in ("content", "harness"):
                if g.get(key) is not None:
                    dims.setdefault(key, []).append(g[key])
        parts = [f"{k}: avg {sum(v)/len(v):.2f}" for k, v in dims.items() if v]
        return "Lowest-scoring capability dimensions — " + "; ".join(parts) if parts \
            else "general capability quality"
    except Exception:
        return "general capability quality"


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The three structural invariants (2026-08 research cluster)
#
# The loop's safety used to rest on convention: the docstring said the verifier
# and overseer live outside the editable surface, and nothing enforced it. Three
# independent sources in one week said to make that a *property* instead:
#
#   - scorer read-only      — Weng / AHE: make the runs dir, tracer, verifier and
#                             LLM config unwritable, which "disables a set of
#                             reward hacking" (docs/research/weng-harness-engineering.md)
#   - agent frozen          — Harness-R1: only the harness changes, so a
#                             regression is reverted by dropping a patch
#                             (docs/research/harness-r1.md)
#   - patch task-agnostic   — HarnessCompass: constrain what may be PROPOSED,
#                             rather than reviewing it after the fact
#                             (docs/research/harness-compass.md)
#
# Two are already true here by construction: the only editable surface is the
# prompt overlay (agent frozen), and `_deterministic_spike_veto` + the LLM
# auditor enforce task-agnosticism. The missing one was the scorer, so it is
# enforced below by fingerprinting the eval surface and refusing to keep
# anything if it moved mid-run.
# ---------------------------------------------------------------------------

def _scorer_fingerprint() -> str:
    """Hash the files that decide the score.

    If the loop can reach its own verifier, "improve the score" and "edit the
    scorer" are the same action — the DGM failure this module's docstring
    describes. Prax cannot mount a read-only filesystem mid-process, so it does
    the next best verifiable thing: record what the scorer WAS, and check it is
    unchanged before keeping anything. Tampering does not get vetoed quietly —
    it invalidates the whole run.
    """
    import hashlib

    here = Path(__file__).resolve().parent
    targets = sorted(
        [here / "capability.py", here / "goldens.py", here / "self_regen.py"]
        + list((here / "capability_cases").glob("*.yaml"))
        + list((here / "goldens").glob("*.yaml"))
    )
    h = hashlib.sha256()
    for f in targets:
        try:
            h.update(f.name.encode())
            h.update(f.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()


def _gate_on_private_holdout(patch: str, *, tier: str, gate_fn=None) -> dict:
    """Select on the HELD-OUT private score, never the score we optimized.

    `accept_change` has existed since the AIDE² assessment with no caller. This
    is the caller. The loop optimizes against the capability suite, so keeping a
    patch on that same number is selection on the training signal — exactly what
    the public/private split exists to prevent. Here the winning patch is graded
    on the golden suite, which carries the visibility split, and `accept_change`
    decides.

    Fail-closed by inheritance: with no private goldens, `accept_change` refuses,
    so the loop proposes nothing rather than adopting on an untrustworthy signal.
    """
    if gate_fn is not None:
        return gate_fn(patch)
    from prax.eval.goldens import accept_change, run_golden_suite

    baseline_run = run_golden_suite(replay=True, tier=tier)
    with override_system_prompt(_apply_overlay(_base_system_prompt(), patch)):
        candidate_run = run_golden_suite(replay=True, tier=tier)
    return accept_change(baseline_run, candidate_run)


def run_self_regen(*, rounds: int = 3, apply: bool = False,
                   proposer=None, evaluator=None, auditor=None,
                   weak_signal: str | None = None, min_margin: float = 0.02,
                   out_dir: Path | None = None, tier: str = "high",
                   gate: bool = True, gate_fn=None) -> dict:
    """Run the propose → verify → keep loop over the system-prompt overlay.

    Args:
        rounds: number of candidate patches to try.
        apply: when True AND ``SELF_REGEN_ENABLED``, write the winning overlay to
            the versioned prompt store; otherwise the winner is a reviewable
            PROPOSAL only (graded autonomy).
        proposer/evaluator/auditor: injected for key-free testing.  Defaults call
            the live LLM (proposer/auditor) and the capability suite (evaluator).

    Returns a summary dict (also archived under
    ``$PRAX_EVAL_DIR/self_regen/<run>/``) with baseline, best, the winning patch,
    every variant + lineage, and whether it was applied.
    """
    from prax.eval import PRAX_EVAL_DIR

    base_prompt = _base_system_prompt()
    ev = evaluator or (lambda p: _default_evaluator(p, base_prompt=base_prompt, tier=tier))
    pr = proposer or (lambda ws: _default_proposer(base_prompt, ws, tier=tier))
    au = auditor or (lambda p: _default_auditor(base_prompt, p, tier=tier))
    signal = weak_signal if weak_signal is not None else (
        _weak_signal_from_baseline(tier) if proposer is None else "general capability quality")

    run_id = uuid.uuid4().hex[:8]
    out_dir = out_dir or (PRAX_EVAL_DIR / "self_regen" / run_id)
    (out_dir / "variants").mkdir(parents=True, exist_ok=True)
    # Snapshot the scorer BEFORE anything runs, so a mid-run edit is detectable.
    scorer_before = _scorer_fingerprint()

    baseline = float(ev(""))
    best_score = baseline
    best_patch = ""
    best_id = "baseline"
    variants: list[Variant] = []

    for r in range(rounds):
        patch = (pr(signal) or "").strip()
        if not patch:
            continue
        # Cheap deterministic vetoes first (skip the expensive eval): over-length
        # patches (so the overseer audits the exact applied bytes) and patches that
        # literally encode an eval answer token (a spike the LLM auditor could be
        # talked out of).
        det = f"over-length (>{MAX_PATCH_CHARS} chars)" if len(patch) > MAX_PATCH_CHARS else ""
        if not det:
            det = _deterministic_spike_veto(patch)
        if det:
            score, audit_ok, reason = best_score, False, det
        else:
            score = float(ev(patch))
            audit_ok, reason = au(patch)
        delta = round(score - best_score, 4)
        # Keep on a real improvement (beats the best by a MARGIN, not noise) AND
        # survives the overseer.  MDL / Occam bias (Baek et al., "Learning to
        # Theorize the World" — see docs/research/learning-to-theorize.md): among
        # candidates that don't move the score, prefer the SIMPLER (shorter) theory
        # — a compact change generalizes better than accreting prompt bloat.
        real_gain = delta >= min_margin
        occam_tie = bool(best_patch and abs(delta) < min_margin and len(patch) < len(best_patch))
        kept = audit_ok and (real_gain or occam_tie)
        v = Variant(
            id=uuid.uuid4().hex[:8], round=r, parent_id=best_id, patch=patch,
            score=round(score, 4), baseline=round(baseline, 4), delta=delta,
            audit_ok=audit_ok, audit_reason=reason, kept=kept,
        )
        variants.append(v)
        (out_dir / "variants" / f"{v.id}.json").write_text(
            json.dumps(asdict(v), indent=2), encoding="utf-8")
        logger.info("self_regen round %d: score=%.3f delta=%+.3f audit=%s kept=%s",
                    r, score, delta, audit_ok, kept)
        if kept:
            # On an Occam tie keep the incumbent (higher) score; a real gain lifts it.
            best_score = max(best_score, score)
            best_patch, best_id = patch, v.id

    # --- Invariant 1: the scorer must not have moved. -----------------------
    # Checked before the gate so a tampered run cannot be rescued by a good
    # private score: if the ruler changed, every measurement taken with it is
    # void, including the gate's.
    scorer_after = _scorer_fingerprint()
    tampered = scorer_after != scorer_before
    if tampered:
        logger.error("self_regen: SCORER CHANGED during the run — discarding all "
                     "candidates (before=%s after=%s)", scorer_before[:12],
                     scorer_after[:12])
        best_patch, best_id = "", "baseline"
        best_score = baseline

    # --- Invariant 2: select on the HELD-OUT private score. ------------------
    # The loop optimized against the capability suite; keeping on that same
    # number is selection on the training signal. accept_change decides from the
    # golden suite's private split, and is fail-closed without one.
    gate_result = None
    if best_patch and gate:
        try:
            gate_result = _gate_on_private_holdout(best_patch, tier=tier,
                                                   gate_fn=gate_fn)
        except Exception as exc:  # noqa: BLE001 — a broken gate must not adopt
            gate_result = {"accept": False,
                           "reason": f"gate failed to run: {type(exc).__name__}: {exc}"}
        if not gate_result.get("accept"):
            logger.info("self_regen: private-holdout gate REJECTED the winner (%s)",
                        gate_result.get("reason"))
            best_patch, best_id = "", "baseline"

    applied = _finalize(out_dir, base_prompt, best_patch, best_score, baseline, apply)
    summary = {
        "scorer_fingerprint": scorer_before,
        "scorer_tampered": tampered,
        "gate": gate_result,
        "run_id": run_id, "out_dir": str(out_dir),
        "baseline": round(baseline, 4), "best": round(best_score, 4),
        "improvement": round(best_score - baseline, 4),
        "rounds": rounds, "variants_kept": sum(1 for v in variants if v.kept),
        "best_patch": best_patch, "applied": applied,
        "variants": [asdict(v) for v in variants],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _self_regen_enabled() -> bool:
    try:
        from prax.settings import settings
        return bool(getattr(settings, "self_regen_enabled", False))
    except Exception:
        return False


def _finalize(out_dir: Path, base_prompt: str, best_patch: str, best: float,
              baseline: float, apply: bool) -> bool:
    """Write the reviewable PROPOSAL, and auto-apply only under graded autonomy."""
    if not best_patch or best <= baseline:
        (out_dir / "PROPOSAL.md").write_text(
            f"# Self-regen: no improvement\nbaseline={baseline:.3f} best={best:.3f} — "
            "no candidate beat the baseline; nothing to apply.\n", encoding="utf-8")
        return False
    (out_dir / "PROPOSAL.md").write_text(
        f"# Self-regen proposal (+{best - baseline:.3f} on the capability suite)\n\n"
        f"Baseline {baseline:.3f} → {best:.3f}. Add this to the system prompt:\n\n"
        f"---\n{best_patch}\n---\n\n"
        "Auto-applied only when apply=True AND SELF_REGEN_ENABLED; otherwise this "
        "is a human-review proposal (graded autonomy).\n", encoding="utf-8")

    if not (apply and _self_regen_enabled()):
        return False
    # Auto-apply via the prompt MANAGER directly — the plugin_tools prompt_read/
    # prompt_write are @tool StructuredTool objects (not callable). The write is
    # versioned, so the overlay stays rollback-able.
    try:
        from prax.plugins.prompt_manager import get_prompt_manager
        mgr = get_prompt_manager()
        current = mgr.read("system_prompt.md")
        if not current or current.startswith("Prompt not found"):
            logger.warning("self_regen: base system prompt missing/sentinel — NOT applying")
            return False
        mgr.write("system_prompt.md", _apply_overlay(current, best_patch))
        logger.info("self_regen: applied winning overlay to system_prompt.md (rollback-able)")
        return True
    except Exception as exc:
        logger.warning("self_regen: apply failed (%s); left as proposal", exc)
        return False
