# AxiomProver (IMO 2026) — autonomous Lean proofs, and the formalization-fidelity trap

**Source.** [AxiomMath/IMO2026](https://github.com/AxiomMath/IMO2026) — Lean 4 proofs
for all six IMO-2026 problems by **AxiomProver** (Axiom Math), an autonomous
multi-agent ensemble theorem prover. Research assessment, house pattern.

**Verdict: document-don't-adopt the *system* (proprietary prover); extract the
lessons.** AxiomProver is the **formal-proof extreme of executable-world-models**
(induce a formal artifact, kernel-verify it) — and simultaneously the **live case
study** of the [Lanyon](lanyon-formal-verification.md) *misformalization* +
[ProofJudge](proofjudge-taste.md) *taste* caveats. It validates Prax's
Lean-kernel-as-verifier direction ([cdc-lean](cdc-lean-teach-prax-lean.md)
`lean_check`) and hands us one sharp, general auditing principle.

## What it is

- Lean 4 `problem.lean` (formal statement) + `solution.lean` (proof) for **all 6
  IMO-2026 problems**, plus a `verify.py` (Axle's `verify_proof`), against Mathlib
  4.31.0. Claims a **perfect 42/42**, both **formalized and proved autonomously**.
- Real compute: Q3 = **4,229 lines / 869 min**. Machine-checkable, not hand-wave.
- An **autonomous multi-agent ensemble** generates and verifies.

## The one lesson that matters most: audit the *formalization*, not just the proof

AxiomProver wrote **both** the spec (`problem.lean`) **and** the proof
(`solution.lean`). The Lean kernel guarantees the proof matches the *spec* — it says
**nothing** about whether the spec matches the *actual IMO problem*. So the
load-bearing question behind "42/42" is **not** "do the proofs check?" (they do, by
construction) — it's **"are the formalizations faithful?"** A perfect proof of a
subtly *weakened or wrong* statement is worthless.

This is exactly [Lanyon's *misformalization*](lanyon-formal-verification.md) and
[ProofJudge's *correctness ≠ what you wanted*](proofjudge-taste.md), made concrete:

> **When a system generates both the specification and the solution, the
> specification is the un-verified surface.** The kernel-check is a floor, not a
> guarantee; the semantic gap (spec ↔ intent) is where the real error hides.

**General principle for Prax** (bankable, beyond math): any flow where Prax produces
*both* a check and the thing it checks — a test it writes for its own code, a rubric
it grades itself against, a formal spec it then satisfies — must **audit the check
independently**. This is `claim_audit` + **maker ≠ checker** applied to the *spec*,
not just the answer. (Honest note: I have **not** independently audited AxiomProver's
formalizations — flagging that *is* the point.)

## What it validates + the reusable shape

- **Autonomous, kernel-verified proving at olympiad level is feasible** — the
  strongest external evidence yet for the `lean_check` / Lean-eval direction. Where a
  domain has a **sound cheap verifier** (the Lean kernel), it is the un-gameable
  floor "verify against ground truth, not self-report" that our world-model work is
  reaching for.
- **Multi-agent ensemble + kernel selection = an *un-gameable* ensemble.** Unlike an
  LLM-judge ensemble (which can share blind spots — misformalization at the meta
  level), an ensemble whose members are filtered by *the kernel* can't rubber-stamp a
  wrong proof. That's the right shape for a Prax Lean-proving lane (cf. the parked
  Lean eval adapter + [mixture-of-agents](mixture-of-agents.md)): many attempts,
  kernel keeps only what checks.
- **A Lean proof is the maximal executable-world-model** — induce a formal model of
  the claim, verify it exactly. It's the same loop as the ARC world-model, with the
  soundest possible verifier.

## Honest caveats

- **42/42 is self-reported** (Axiom Math's own repo), and — the crux above — its
  value rests entirely on **formalization fidelity**, which is the hard,
  un-kernel-checkable part. Extraordinary autonomous-proof claims warrant a
  formalization audit before acceptance.
- **Long proofs raise the taste question too** (Q3 ≈ 4.2k lines). Per ProofJudge,
  "kernel-checks" ≠ "good/reusable" — a 4k-line proof may be correct and *awful*.
- Prover is **proprietary** (only proofs + a verifier are public) — not adoptable;
  the lessons are.
- **Caution for our own claims:** when Prax reports an ARC solve or a benchmark pass,
  the same discipline applies — **verify the spec/scoring, not just the pass.** Our
  ARC-2 scorer is exact-grid-match against held-out ground truth (no self-formalized
  spec), which is the *right* side of this line — keep it there.

## Takeaways

1. **Generate-both-spec-and-solution ⇒ the spec is the un-verified surface.** Audit
   the formalization/check independently — maker ≠ checker, applied to the spec.
2. **The Lean kernel is the un-gameable floor** — validates `lean_check` and the
   Lean-eval lane; the soundest form of "verify against ground truth."
3. **Ensemble + kernel-selection is un-gameable** where an LLM-judge ensemble isn't —
   the shape for a Prax proving lane.
4. **Kernel-checks ≠ good** (taste) and **≠ faithful** (fidelity) — two floors, not a
   guarantee. Completes the Lanyon (correctness) + ProofJudge (taste) trilogy with
   *fidelity*.
