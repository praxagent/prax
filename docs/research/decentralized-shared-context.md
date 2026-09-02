# DeLM — decentralized agents with a shared verified context

**Source:** [arXiv 2606.10662](https://arxiv.org/abs/2606.10662) —
*Decentralized Multi-Agent Systems with Shared Context*. Yuzhen Mao, Azalia
Mirhoseini.

**Verdict: document — adopt ONE narrow idea (a shared record between parallel
delegations), decline the decentralization.** This paper argues against the
architecture Prax uses, so it gets read carefully rather than defensively.

## The claim, and it is aimed at us

Most multi-agent systems use **centralized orchestration**: a main agent
assigns work, collects outputs, merges results. That is Prax's hub-and-spoke,
precisely. Their argument: *as the number of subtasks grows, this controller
becomes a communication and integration bottleneck.*

**DeLM** removes the controller. Asynchronous agents claim subtasks from a
**queue**, read accumulated progress from a **shared verified context**, reason
locally, and contribute **compact verified updates** — no central routing.
Reported: **SWE-bench Verified up to +10.5pp with 50% lower cost per task**;
**LongBench-v2 Multi-Doc QA up to +5.7pp across four model families**.

## Reconciling it with the opposite finding

[Scaling Agent Systems](scaling-agent-systems.md) (Google/MIT)
measured the reverse: a **verifying hub** produced 4.4× error against 17.2× for
uncoordinated peers — which we took as evidence *for* hub-and-spoke.

These are not actually in conflict, and the resolution is the useful part:
**DeLM is not uncoordinated peers.** It keeps verification — that is what
"shared *verified* context" means — and moves it out of the hub into a shared
artifact. So the real variable was never centralized-vs-decentralized. It is
**where verification lives**, and both papers agree that removing verification
is what costs you.

That reframing is worth more to us than either result: Prax's hub is valuable
because it *verifies and integrates*, not because it *routes*. Those are
separable, and only the second is the bottleneck they describe.

## Decline the decentralization — we do not have the problem

Their bottleneck appears "as the number of subtasks grows". Prax fans out a
handful of delegations per turn, not hundreds. Adopting a task queue and
asynchronous claiming now would be machinery in search of a load — the same
mistake as reaching for a rule engine to check eight relation types
([semantica](semantica-knowledge-substrate.md)).

Worse, it cuts against a finding we already banked: Scaling Agent Systems'
capability-ceiling heuristic says **don't fan out when the orchestrator
suffices**. Prax's live problem this month was an agent doing *too much* on an
unsatisfiable request (#58), not too little in parallel.

## Adopt: parallel spokes should see each other's findings

The part that lands is the **shared context**, independent of decentralization.

Today Prax's `delegate_parallel` spokes run **completely blind to one
another**. Each returns a string; the orchestrator merges them at the end. Two
consequences, one measured and one filed:

- **Integration lands entirely in the orchestrator's context.** Twelve parallel
  results all arrive in one window — exactly the fan-in pressure the paper
  describes, and the reason the fan-in guard shipped on 2026-08-08 had to lead
  with a shortfall banner rather than assume the merge was whole.
- **Nothing coordinates their writes.** Filed as #64: spokes share one
  workspace with no locking and no atomic write. The fix for a collision is
  coordination, and coordination needs shared state — which is this paper's
  primitive.

The narrow adopt: **an append-only, per-turn shared record that parallel
delegations can read and write**, carrying compact findings rather than full
outputs. Not a queue, not asynchronous claiming, no rearchitecture — a place
where spoke B can see that spoke A already established a fact, and where a
write conflict becomes visible instead of silent.

Two constraints that come free from work already done:

- Entries must carry **provenance**. A finding contributed by a spoke that read
  an untrusted web page is untrusted content, and the taint rules already exist.
- The record must be **bounded and compact** — "compact verified updates" is
  their phrase, and the alternative is reinventing the context-collapse problem
  inside the shared store.

## Honest limits

Abstract-level reading; +10.5pp / 50% / +5.7pp are theirs and unverified here.
No stated limitations in what I could read, which is itself worth noting on a
paper proposing an architecture change. And the benchmarks are both
decomposable-by-construction (SWE-bench issues, multi-doc QA) — the regime
*most* favourable to parallel subtasks, and least like an open-ended assistant
turn.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Shared append-only findings record for parallel delegations** (compact entries, provenance-carrying, bounded) | 📋 queued — pairs with #64, whose collision fix needs shared state anyway |
| Decentralized task queue / asynchronous claiming / removing the hub | ❌ declined — Prax fans out a handful of subtasks, not hundreds; machinery in search of a load, and it cuts against the banked "don't fan out when the orchestrator suffices" heuristic |
