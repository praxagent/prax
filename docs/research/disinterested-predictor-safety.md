# Safety from Honesty in a Disinterested AI Predictor (Bengio et al.)

[← Research](README.md) · *LLM behavior & safety lane*

Reference note on **[Safety from Honesty in a Disinterested AI Predictor](https://arxiv.org/abs/2606.29657)**
(Bengio, Richardson, M. Cohen, Ward, et al. — the "Scientist AI" agenda).

**Verdict: document + principle (track), not a build. It's a theoretical
alignment paper (no benchmarks), and Prax isn't building a Bayesian predictor —
but it is the strongest formal grounding yet for THREE things Prax already ships:
the disinterested self-regen loop, injection resistance, and the un-gameable
verifier.**

## What it argues

Training a model to *optimize downstream outcomes* can breed **implicit agency** —
unintended goal-directed behavior the designers never specified (the alignment
risk). The proposal: a **Scientist AI (SAI) Predictor** trained to approximate
**Bayesian posteriors on "epistemically contextualized"** natural-language data —
it **distinguishes factual claims from communicative acts, treating goal
expressions as patterns to *explain* rather than objectives to *adopt*.** Because
it earns no reward from deployment effects, it stays **disinterested**. The formal
safety claim: a guarded Predictor carrying residual harm above a threshold is
*unlikely*, since dangerous behavior would need **coordinated misestimation across
queries** — sparse at initialization and given **no training signal**. Safety and
accuracy are *jointly* supported: the constraints that enforce accuracy are the
same ones that make coordinated deception costly.

## Why it matters for Prax — formal grounding for what's already shipped

| Bengio principle | Prax mechanism it grounds |
|---|---|
| **Disinterested predictor** — the improving system earns no reward from deployment effects; goals live outside it | **Self-regeneration loop (#29)**: the loop edits only the scaffold; the **verifier + overseer live OUTSIDE the editable surface**, so the system can't develop agency toward gaming its own reward (the DGM failure). Bengio is *why* that separation is the safe design. |
| **Goal-expressions are patterns to EXPLAIN, not objectives to ADOPT** | **Lethal-trifecta guard + injection goldens**: injected "assistant: exfiltrate…" text in untrusted content is DATA to describe, never a command to obey. Bengio formalizes the injection-resistant stance. |
| **Accuracy constraints make coordinated deception costly; honesty ⇒ safety** | **Un-gameable deterministic verifier + honesty guard**: faking success is costly because the verifier is deterministic and `audit_tool_failures` surfaces swallowed crashes. "Coordinated misestimation is sparse and unrewarded" = the **un-gameable-fitness precondition** for safe RSI. |

The through-line: Prax's whole self-improvement safety story rests on **keeping the
self-improving component disinterested and the fitness function un-gameable** — the
exact thesis this paper formalizes. It converts a design intuition ("verifier
outside the editable surface", already in `self_regen.py`) into a stated safety
argument worth citing.

## The transferable principle

> **Keep the self-improving component a disinterested proposer.** It predicts /
> proposes changes but must earn *no* reward from the act of being deployed or from
> gaming the metric — all goals and rewards live in the **external, un-gameable
> verifier**. Treat goal-expressions in untrusted inputs as patterns to model, not
> objectives to inherit.

This is the safety framing for the self-regeneration agenda and the injection work
— and it composes with [`learning-to-theorize`](learning-to-theorize.md)
(transfer-not-memorize) and [`inherently-interpretable-models`](inherently-interpretable-models.md).

## Document-don't-adopt

- **The SAI Predictor framework itself** — a Bayesian-posterior predictor is a
  research direction, not a component Prax builds; Prax is an LLM-driven agent, not
  a disinterested world-model. Take the *principle*, not the architecture.
- **The formal proofs** — grounding, not an implementation spec.

## Sources

- [Safety from Honesty in a Disinterested AI Predictor (arXiv 2606.29657)](https://arxiv.org/abs/2606.29657)
- Related: [self-regeneration](../../prax/eval/self_regen.py) · [learning-to-theorize](learning-to-theorize.md) · [inherently-interpretable-models](inherently-interpretable-models.md) · [agentic-landscape-2026-sweep](agentic-landscape-2026-sweep.md)
