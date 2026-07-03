# Inherently interpretable LLMs (Guide Labs) — the API-level attribution unlock

[← Research](README.md) · *LLM behavior & interpretability lane*

Reference note on **Guide Labs — [Scaling Inherently Interpretable Language
Models](https://www.guidelabs.ai/papers/scaling-inherently-interpretable-language-models/)**
(Steerling-8B). Read for its **harness implications**, per this lane's premise.

**Verdict: track + internalize one principle; do not adopt (yet).** It's
proprietary and capability-lagging today, but it names a real *unlock* for Prax's
reliability story — and it maps straight onto the security + self-regeneration
work just shipped.

## What it is

Interpretability built into training, not retrofitted. The transformer hidden
state is decomposed into **known concepts + unknown concepts + residual**, with a
**linear prediction head** on top — so the forward pass yields **exact per-concept
contributions**. The model exposes three first-class outputs:

1. **Input attribution** — which prompt tokens actually drove the output.
2. **Concept attribution** — human-readable concepts (e.g. "gradient descent")
   that drove the prediction.
3. **Training-data retrieval** — which training examples the output resembles.

The load-bearing claim: these explanations are **faithful by construction**
(concepts constrain training), unlike post-hoc SAEs/probes which "have no
guarantee of being faithful." Interpretability metrics *improve* with scale; the
capability gap vs opaque models is a **small constant** (doesn't grow) — though at
fixed size Steerling-8B scores 51.6% avg vs LLaMA3-8B's 60%+.

## Why it matters for Prax — it breaks the activation-access constraint

This lane's standing caveat (see [Goodfire manifolds](llm-emotion-manifolds.md)):
Prax consumes an API and **can't read activations**, so interpretability lands as
*principles + output-level proxies*, not features — unless a self-hosted backend
exposes hidden states. An **inherently interpretable model changes that**: the
attributions are in the **output**, so even via an API (or Prax's own
`VLLM_BASE_URL` backend) the harness gets a **faithful mechanistic signal without
raw activation access.** That's the unlock.

Three concrete harness ties — each strengthens something Prax already has or just
shipped:

- **Grounding / hallucination guard → mechanistic.** Prax detects
  confabulation via *output-level proxies* (semantic entropy, hallucination-guard,
  verify loops). Faithful **input attribution** would let the harness check
  directly: *is this answer attributed to the retrieved source, or asserted from
  nowhere?* A stronger grounding signal than a proxy judge.
- **Injection detection → mechanistic (ties to the just-shipped lethal-trifecta
  guard + trajectory auditor).** The trifecta guard + an AlignmentCheck-style
  auditor detect exfiltration at the *trajectory* level. Input attribution shows
  *mechanistically* when the output is being driven by **untrusted tool content**
  vs the user's instruction — a mechanistic injection detector that composes with
  the architectural trifecta defense.
- **Self-regeneration #29 → harder-to-game fitness.** The self-regen loop verifies
  via the deterministic capability suite + an anti-spike overseer. **Concept
  attribution** ("did the improvement change *what concepts* drive the answer, not
  just the output string") and **training-data retrieval** (memorization/spike
  detection) are mechanistic anti-gaming signals — exactly the "un-gameable
  fitness" the loop depends on.

## Why NOT adopt now (honest caveats)

- **Proprietary, closed weights.** Accessed only via Guide Labs' *Clarity* API —
  conflicts with Prax's sovereign / provider-independent / local-first stance
  (Prax can't self-host it, and routing data to a vendor is against the grain).
- **Capability tradeoff at fixed size** (51.6% vs 60%+). For Prax's eval
  *subject*, frontier capability still matters more than interpretability.
- **Vendor-reported.** The "improves with scale, constant gap" results are from
  Guide Labs' own technical report — promising, not independently replicated.

So it's a **watch item**: adopt IF an *open* inherently-interpretable model appears
(or the technique is replicated on an open base), at which point Prax's local
backend could run it as an **optional "interpretability lane"** feeding the
grounding / injection / self-regen guards above.

## The principle to internalize NOW (no model required)

Their headline metric — *"the share of each prediction flowing through
interpreted concepts, rather than through an uninterpreted residual"* — is a
**grounding metric Prax can approximate at the output level today**: how much of an
answer traces to retrieved/cited concepts vs asserted-from-nowhere residual. That's
the output-level analog, and it maps onto the existing `grounding_citations`
golden + the hallucination guard. Interpretability's near-term gift to Prax isn't a
model swap — it's the **discipline of measuring "how much of this output is
accounted for."**

## Sources

- [Guide Labs — Scaling Inherently Interpretable LMs](https://www.guidelabs.ai/papers/scaling-inherently-interpretable-language-models/)
- Related: [Goodfire manifolds](llm-emotion-manifolds.md) · [grounding](grounding.md) · [error-metacognition](error-metacognition.md) · [diffuse-ai-control-judge-robustness](diffuse-ai-control-judge-robustness.md)
