# Filesystem-Based Memory for LLM Agents — the shape of a store, and who it's for

**Source:** [arXiv 2607.26637](https://arxiv.org/abs/2607.26637) — *Filesystem-Based
Memory for LLM Agents: Organization, Evolution, and Sustainability*. Zhou, Yu,
Wei, Wu, Ouyang, Jiao, Pan, McAuley, Zhang, Yu, Han (UIUC / UCSD / UC Merced /
Adobe Research / Texas A&M), 29 Jul 2026.

**Verdict: document + adopt three — the taxonomy contract as a checkable
property set, the preservation rule as a standing requirement on any
reorganising pass, and (the valuable one) verbatim-vs-distilled as a
per-consumer choice. Decline building a Library reorganiser, on their own
evidence.**

## Why this one is worth reading properly

Almost every memory paper designs a bespoke representation and studies
retrieval over it. This one studies **the default that shipped** — a directory
tree of markdown files the agent reads, writes and reorganises with generic
file tools — and tests its two unexamined assumptions: that an agent can keep
such a store organised as it grows, and that the organisation pays.

That matters here because **Prax runs both media at once**. The Library
(`spaces/{slug}/notebooks/notes`, `.progress/`, `library/raw/`) is exactly this
filesystem store; agent memory (Qdrant vectors + Neo4j graph) is the bespoke
kind. So the paper's findings apply directly to half of Prax's memory stack,
and the half that user-facing work actually lives in.

## Their five findings, and which ones bite us

They formalise three roles over one store — a **management agent** that
integrates and reorganises, a **search agent** that answers with citations, and
an **execution agent** whose trajectories are distilled into skills — across
LoCoMo / PersonaMem / REALTALK (conversation) and ALFWorld (procedural).

- **RQ1 (organisation).** Left alone, management agents grow subject-based
  trees — but *the shape is a signature of the model more than a response to
  scale*. The clearest degenerate behaviour: **a reorganising pass that
  silently condenses content unless one preservation rule is added.**
- **RQ2 (value of shape).** No shape wins correctness everywhere.
  Organisation's unambiguous payoff is **search cost**, and it grows with the
  material. On skills **the winner flips with the consumer: a verbatim episode
  log serves a strong execution agent best, distilled guidance a weak one.**
- **RQ3 (backbone capability).** The management agent's strength buys
  organisational *style*, not answer quality; the search agent's strength pays
  directly. **When memory must be distilled into procedures, capability acts as
  a THRESHOLD: once crossed, what the store contains matters more than which
  model executes.**
- **RQ4 (sustainability).** Stores get more useful as they grow, but
  **organisation is the weaker half — adherence to the taxonomy erodes as most
  stores grow, and only the strongest management agent holds it.**
- **RQ5 (harness).** Adding a tool changes behaviour but not outcomes;
  *replacing the tool set reshapes the store itself*. "The harness is a lever
  over memory organisation, not a neutral wrapper."

Read against the default's assumptions, the answer splits: a growing store
stays useful, but staying *organised* tracks the manager's capability, and **no
agent they measured converts organisation itself into better answers.**

## Adopt 1 — the taxonomy contract, as a checkable property set

Their P1–P5 are unusually crisp for something normally left to taste: **P1**
siblings distinguishable by label alone; **P2** siblings belong together;
**P3** a parent covers its children; **P4** distance mirrors relatedness;
**P5** structural economy — *depth is added only where it improves routing to a
fact; a level that does not help routing is overhead.*

These are **measurable**, which is what makes them worth taking. Prax has no
notion of whether a user's Library is well-shaped; P1/P3/P5 are computable from
names and one-line descriptions alone, with no model call. That is a
deterministic health metric of exactly the kind this project prefers over a
judge — the same "verifiable beats judgeable" argument that governs the
capability suite.

## Adopt 2 — the preservation rule, promoted to a standing requirement

Their degenerate case — *a reorganising pass silently condenses content unless
one preservation rule is added* — is a defect Prax already hit and fixed:
progress compaction destroyed the session ids that made summaries traceable
back to evidence (see [TencentDB drill-down](tencentdb-agent-memory.md), and
the standing rule that came out of it: *when adding a summarisation step, ask
what pointer the rewrite destroys*).

Independent confirmation of a bug class we found the expensive way is worth
banking. The generalisation: **any pass that rewrites a store must carry an
explicit, testable preservation rule**, and the rule must be enforced in code
outside the model's output. Prax satisfies this for `.progress/`; it has no
Library reorganiser yet, so the rule should be written *before* one exists
rather than after it eats someone's notes.

## Adopt 3 — verbatim vs distilled is a per-consumer choice (the valuable one)

RQ2's flip is the most directly actionable result in the paper, and it is
**independent, measured support for the adaptive-scaffolding hypothesis**
(#60): *the same artifact has opposite optimal shapes depending on the strength
of whoever consumes it.* Verbatim for a strong consumer, distilled for a weak
one.

Prax has this exact knob and currently sets it globally. Per-space progress
keeps a compacted summary (≤6000 chars, LLM-compacted when full) that is loaded
at session start, while the `.progress/` detail files are **not** auto-loaded
and must be fetched with `progress_detail(slug, date)`. So every tier gets the
distilled artifact by default. The paper predicts that is right for the low
tier and *wrong* for the strong one, which should be reading the verbatim log.

That is a concrete, cheap experiment on machinery that already exists — and it
is a far sharper instance of #60 than toggling reliability flags, because the
mechanism (which artifact is loaded) is unambiguous.

**It also corrects #60's design.** I registered #60 predicting a *sign flip*
across two models. RQ3 says capability behaves as a **threshold**, not a
gradient — so two models that happen to sit on the same side of it will show
nothing, and the experiment would read as a null when the effect is real. #60's
model choice must straddle the threshold, and "no flip observed" must be
reported as *underpowered*, not as refutation. Recorded on the task.

## Decline — do not build a Library management agent yet

Tempting, and their evidence argues against it: organisation **erodes as stores
grow for every agent but the strongest**, and **no agent converts organisation
into better answers**. So a reorganiser buys search cost, at the risk of the
silent-condensation failure, on a store holding the user's own notes. Wrong
trade for Prax today. Revisit if Library search cost becomes a real complaint —
it is a cost optimisation, not a quality one, and should be justified as such.

## Honest limits

Read from the paper's front matter and RQ summaries; I have not verified the
per-benchmark absolute numbers behind "roughly halve retrieval cost", so that
figure is theirs, not ours. Horizons are bounded ("within our horizons"),
"beyond one conversation" is named as an open problem, and they say plainly
that **quality benchmarks are largely blind to shape** — which is also why
their headline result is a cost finding rather than a quality one. Nothing here
should be cited as evidence that better-organised memory produces better
answers; the paper says the opposite.

## Adopt-tracker rows

| Item | Status |
|---|---|
| Taxonomy contract P1–P5 as a deterministic Library health metric (no model call) | 📋 queued |
| Preservation rule required on any store-rewriting pass (already true for `.progress/`; write it before a Library reorganiser exists) | 📋 queued |
| **Verbatim vs distilled progress by tier** — strong consumer reads the log, weak one reads the summary | 📋 folded into #60 |
| Library management/reorganiser agent | ❌ declined on their evidence |
