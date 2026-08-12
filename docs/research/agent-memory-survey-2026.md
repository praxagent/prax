# A Survey of Agent Memory in the Second Half — mostly a lens we already have

**Source:** [arXiv 2602.06052v4](https://arxiv.org/abs/2602.06052) — *A Survey
of Agent Memory in the Second Half: Towards Self-Evolving and Long-Horizon
Agents*. Huang, Zhang, Liang, Bei, Chen, Feng, … Han, Yu, Shu (50+ authors).

**Verdict: document-don't-adopt as a new lens — [CoALA](coala-cognitive-architectures.md)
already gives us this one — but bank two things it adds.** Kept short
deliberately: writing a long assessment of a survey that largely duplicates an
existing lens would be padding.

## What it is

A large survey organising agent memory along three axes:

- **substrate** — parametric vs external / retrieval-augmented
- **cognitive mechanism** — sensory, working, episodic, semantic, procedural
- **subject** — user-centric vs agent-centric

plus single- and multi-agent topologies and trainable memory-management
policies. Framed around *"context explosion beyond fixed context windows"* and
the need to *"continuously accumulate, manage, and selectively reuse
information across extended interactions"*.

**No quantitative results.** The abstract references "evaluation benchmarks and
metrics for memory utility" but I found no absolute numbers, so none are quoted
here. A survey's contribution is organisation, not evidence.

## Why it earns a short note rather than a long one

The cognitive-mechanism axis — working / episodic / semantic / procedural — is
**CoALA's memory axis**, which this project assessed in full and already uses
as a standing self-audit lens, complete with a Prax mapping (procedural memory
= plugin system + self-regen loop). The
[Agent Harness survey](agent-harness-engineering-survey.md) is a second such
lens (ETCLOVG).

This would be the **third overlapping taxonomy**, and a third lens that mostly
restates the first two is not worth the maintenance. Recording that judgement
is more useful than another mapping table: *the marginal value of taxonomy
papers here has gone to roughly zero, and future ones should be assessed for
what cell they name that CoALA does not.*

## The two things it does add

**1. The subject axis — user-centric vs agent-centric — which CoALA lacks.**
Prax has an unusually strong position here, and it is deliberate rather than
accidental: the **wall between `agent_plan` and the Library Kanban**
([library.md](../library.md)) is exactly this distinction, enforced
architecturally. `agent_plan` is agent-centric (ephemeral, injected every turn,
cleared at turn end); the Kanban is user-centric (durable, activity-logged,
touched only on explicit request). Most systems conflate the two and end up
mirroring the agent's scratch work onto the user's board.

That is worth knowing as external vocabulary for a decision already made and
documented — the survey names the axis we drew a wall along.

**2. Multi-agent memory topology, which CoALA (single-agent) does not cover.**
This lands on a gap identified three days ago from
[DeLM](decentralized-shared-context.md): Prax's parallel spokes run **blind to
one another**, with no shared agent-centric memory. Two independent sources now
point at the same missing piece, which raises it from "one paper's idea" to a
structural gap. It stays queued, not built.

## What it does NOT change

Prax's memory weak cell remains **consolidation**, as
[self-improving-agents](self-improving-agents-survey.md) found. Nothing here
supplies a mechanism for it. The trainable memory-management policies are
weights-level and hit the same wall as everything else in that family.

## Adopt-tracker rows

| Item | Status |
|---|---|
| "user-centric vs agent-centric" as external vocabulary for the `agent_plan`/Kanban wall | ✅ already instantiated — no work, useful framing |
| Multi-agent memory topology (shared agent-centric memory across parallel spokes) | 📋 already queued from [DeLM](decentralized-shared-context.md); this is the **second independent sighting** |
| A third taxonomy lens | ❌ declined — CoALA and ETCLOVG cover it; assess future taxonomies for what cell they name that CoALA does not |
