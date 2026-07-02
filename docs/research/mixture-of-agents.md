# Mixture of Agents (Nous Hermes) — should Prax have it?

[← Research](README.md)

Reference note on **[Mixture of Agents](https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents)**
(Nous Research, Hermes agent).

**Verdict: adopt as an OPTIONAL, flag-gated quality mode for hard tasks — NOT a
default. It's compatible with Prax's bounded orchestrator (it's ensemble-for-
quality, explicitly *not* a swarm), it's a real ~6-point lever, but it multiplies
model calls per turn — so gate it behind the accuracy-vs-cost Pareto and let the
new HAL cost-axis eval decide when it earns the spend.**

## What it is (and what it is NOT)

MoA is a **virtual model provider** that aggregates several models to lift quality
on hard tasks while keeping ONE agent loop (tools, memory, interrupts):

1. **Reference models** run first, without tool schemas — they just analyze the
   conversation.
2. An **aggregator model** (the *acting* model) receives the reference outputs as
   private context and writes the actual response + emits all tool calls.

The docs are explicit: *"not a swarm-based multi-agent system, but an
**ensemble-for-quality technique**."* HermesBench: a 2-model MoA (Opus aggregating
GPT-5.5) scored **0.8202** vs Opus **0.7607** / GPT-5.5 **0.7412** — ~6 pts over the
best component. Cost is the stated tradeoff: *"MoA increases model-call count"*
(multiple reference calls per iteration), though the main prompt cache stays warm.

## Why it does NOT conflict with Prax's "no multi-agent swarm" stance

The [landscape sweep](agentic-landscape-2026-sweep.md) pinned Prax's non-goal as
*peer-agent swarms doing a task*. MoA is the opposite shape: **one acting agent
(the aggregator)** with extra *perspectives* feeding it before it commits — closer
to Prax's existing **maker≠checker** than to a swarm. So it's philosophically
compatible.

## Prax already has most of the benefit — cheaper

| MoA piece | Prax equivalent (shipped) |
|---|---|
| Aggregator reviews reference outputs before committing | **maker≠checker** auditor (claim_audit, golden auditor, self-regen overseer) |
| Multiple providers' perspectives | **cross-provider failover** (`LLM_FALLBACK`) + model **tiers** |
| Spend more on hard tasks | **pro tier** escalation; the self-regen judge-panel pattern |

What Prax lacks is the specific **reference-models → aggregator** layer (perspectives
*before* the answer, not a critique *after*).

## The honest recommendation — build it flag-gated, let the cost-axis eval govern

MoA is a **cost-for-quality knob**, and Prax just grew the exact instrument to
decide when to turn it: the **HAL cost-axis** now on the capability + harness-lift
evals (`pass_per_1k_tokens`, `avg_full/bare_tokens`). So:

1. Add MoA as an **optional provider mode** (`MOA_ENABLED`, default off) — reference
   temp ~0.6, aggregator temp ~0.4, aggregator = the acting model — mirroring the
   pro-tier/auditor opt-in pattern.
2. **Gate rollout on the Pareto:** run capability + harness-lift with MoA on vs a
   single upgraded tier and compare `pass_per_1k_tokens`. Keep MoA only where the
   reference-ensemble lift **beats simply upgrading the tier** at equal spend.
3. Likely home: an escalation for *hard* turns (low judge confidence / high stakes),
   not every turn — cheap-first stays the default.

That ordering avoids paying N× tokens for a lift a one-tier bump might already buy —
which is precisely what the cost-axis exists to reveal.

## Sources

- [Mixture of Agents (Nous Hermes)](https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents)
- Related: [agentic-landscape-2026-sweep](agentic-landscape-2026-sweep.md) (no-swarm non-goal) · [expert-judgment-finetune](expert-judgment-finetune.md) (cost-axis) · [harness-engineering](harness-engineering.md)
