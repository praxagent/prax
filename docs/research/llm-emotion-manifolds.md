# LLM behavior — narrative/emotion as geometric trajectories (Goodfire)

Reference note on Goodfire's **["Meandering on Manifolds" / "Stories in Space"](https://www.goodfire.ai/research/stories-in-space)**.
First entry in the **LLM behavior & interpretability** lane (see the section of that
name in [`README.md`](README.md)): *model internals are a design input to the
harness, not just a curiosity.*

**Verdict: reference + principles — not an adopt-build.** Prax consumes models over
an API and can't read activations, so the value is **design principles the harness
should hold**, plus validation of directions Prax is already on. It is *not* a
feature to wire.

## What it found

Analyzing **Llama 3.1 8B** reading stories sentence-by-sentence, Goodfire shows the
model's internal activations trace a **path along a low-dimensional manifold** —
emotions organize geometrically the way human psychology's **valence–arousal** model
predicts (positive/negative × energetic/calm). Key claims:

- LLMs don't merely *process* text linearly — they maintain a **dynamically
  updating "belief state"** about narrative context, tracked as a **geometric
  trajectory** through concept space (not symbolic rules).
- *"The dynamic emotion tracking that we can elicit via explicit prompting is also
  present in the model's internal representations."* — i.e. what you can get the
  model to *report* corresponds to a real internal state, not pure confabulation.
- Early framing **constrains later interpretation via manifold topology** —
  sequence shapes the trajectory.

## Why it matters for the harness (the load-bearing part)

Three transferable principles, each mapped to where Prax already lives:

1. **Context *ordering* is load-bearing, not just content.** "Early narrative
   framing constrains later interpretations" means *where* a fact sits in the
   prompt changes how everything after it is read. Reinforces Prax's context
   engineering — system-prompt construction, the per-turn state re-injection
   ([`../infrastructure/context-management.md`](../infrastructure/context-management.md)),
   and the "persistence prevents drift" takeaway. **Principle: put framing/goal
   first; order context deliberately; don't treat the window as an unordered bag.**

2. **The model has a trackable evolving state → drift is detectable.** Goodfire:
   *"trajectory analysis could diagnose when models veer off expected conceptual
   paths, enabling intervention before harmful outputs."* Prax can't read the
   manifold, but this is **exactly the bet behind its API-level proxies**: the
   **semantic-entropy / hallucination-guard** metrics, trace introspection, and the
   verify-loop. The interpretability result says those proxies are tracking a *real*
   thing. **Principle: monitor the trajectory of an agent run and intervene on
   drift — don't only check the final answer** (composes with the failure-driven
   trace-diff idea, [`../IDEAS_BACKLOG.md`](../IDEAS_BACKLOG.md) #19, and the
   loop-health metric #22).

3. **Introspection-style prompting is partly grounded — but verify.** Self-reported
   state corresponds to internal state *here*, which lends cautious support to
   asking a model about its own confidence/uncertainty — Prax's metacognition
   (`metacognitive.py`) and self-verification ([`active-inference.md`](active-inference.md),
   which already models agents as maintaining/​updating belief states). **Caveat:**
   this is one model, one domain (emotion in stories); introspective faithfulness is
   not general, so keep introspection **gated by an independent check**, never
   trusted raw (same discipline as the maker≠checker rule and #22's "completion ≠
   acceptance").

## Honest caveats

- **Single model / narrow domain.** Llama-3.1-8B, emotion-in-narrative. Don't
  over-generalize the specific geometry to all tasks/models.
- **Prax has no activation access.** These are *principles*, realized through
  output-level proxies (entropy, verifiers, eval gates), not direct manifold
  reads — unless a future self-hosted backend (the open-backend work, #20) exposes
  hidden states, which would make literal trajectory-monitoring possible.
- **Interpretability ≠ control.** Knowing the geometry exists doesn't yet give a
  reliable steering lever from outside the model.

## Bottom line

Validates a thesis Prax already bets on — **models carry a real, evolving internal
state, so the harness should engineer *context order* and *monitor the trajectory*,
not just the final output** — and adds one nuance (introspection is partly grounded
but must stay verifier-gated). Reference + principles; nothing to build. The lane it
opens (model internals → harness design) is worth feeding with related sources.
