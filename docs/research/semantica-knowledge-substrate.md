# Semantica — the auditable knowledge substrate, and the one axis Prax half-has

**Source:** <https://github.com/semantica-agi/semantica> (~2.9k stars, 348 forks,
~2,223 commits, MIT, Python). Positions itself as *"The Open Source Palantir for
AI Agents"* — deterministic, auditable context for regulated domains.

**Verdict: document — adopt ONE (finish Prax's bitemporal model), consider a
second (name the conflict-resolution strategy), decline the substrate.**

## What it is

A knowledge pipeline — *sources → ingest → parse → normalize → split → extract
→ conflict detection → dedup → knowledge graph → (ontology · reasoning ·
provenance · decisions) → enriched KG → vector store + polyglot graph store* —
whose selling point is that **none of the reasoning or provenance needs an
LLM**. `semantica.reasoning` is a `ReteEngine` and a `DatalogReasoner`;
`semantica.provenance` tracks W3C PROV-O lineage; `semantica.ontology`
validates against SHACL/OWL; `semantica.conflicts` has an explicit
`ConflictDetector`/`ConflictResolver` pair.

That is a different product from Prax — an enterprise knowledge substrate with
Databricks and Snowflake ingestors, not a personal agent harness — and most of
it is not ours to want. But the memory layer is close enough to compare
honestly, and one comparison is uncomfortable.

## Adopt — finish the bitemporal model (Prax has it half-built)

Their `BiTemporalFact` tracks **valid time** (when the fact was true) and
**recorded time** (when we learned it) as independent axes.

Checking Prax rather than assuming: **we already have both.** `RELATES_TO`
edges carry `valid_from` / `valid_until` beside `first_seen` / `last_seen`;
`add_relation` accepts `valid_from`; consolidation passes it; the extraction
prompt explicitly asks the model for `"valid_from": "ISO date or null"` and a
`"supersedes"` slot. That is more temporal structure than most agent memories
have, and the docstring already says "bi-temporal".

**But the closing boundary collapses.** `supersede_relation` hardcodes
`valid_until = now`, so `valid_until` records *when we found out*, not when the
fact stopped being true. The opening boundary is valid-time; the closing one is
record-time wearing valid-time's name.

Concretely: in August the user says *"I moved to Berlin back in March."*
Consolidation supersedes `lives_in → Paris` with `valid_until = August`. A
point-in-time query for April answers **Paris** — wrong — and the correct date
was present in the utterance and discarded. Same family as the progress-
compaction defect: a rewrite destroying information the model already had.

The distinction is not pedantry. It is *"where did he live in April?"* versus
*"what did we believe in April?"*. Only the second is answerable today, and
nothing in the data tells a reader which one they are getting. Tracked as
**#65**; it is the closing half of the memory-consistency pass shipped
2026-08-07, which is the thing that calls `supersede_relation`.

## Consider — name the conflict-resolution strategy

They enumerate `credibility_weighted`, `most_recent`, `voting`. Prax's
consistency pass implements exactly one of these — *most recent wins* — but
implicitly, as control flow rather than as a named, swappable policy.

Naming it would be worth doing **if and only if** a second strategy is actually
wanted, and there is a real candidate now that Prax stamps provenance:
`credibility_weighted` could prefer a fact the user stated directly over one
extracted from an untrusted web capture. That is a genuine improvement to
`SINGLE_VALUED_TYPES` handling, not a refactor for symmetry. Until it is built,
naming the strategy is ceremony — one implementation behind a strategy
interface is worse than a function.

## Decline — the substrate, and why

- **Polyglot storage** (6 vector backends, 8 graph backends behind one
  abstraction). Prax has *chosen* Qdrant and Neo4j. An abstraction over both
  families buys portability nobody is asking for and costs a permanent
  lowest-common-denominator tax on every query.
- **Rete/Datalog reasoning.** Tempting — it is the general form of the
  symbolic consistency pass, and [neurosymbolic-lens](neurosymbolic-lens.md)
  already argues symbols survive as *checkers*. But Prax's checker is ~8
  relation types and one invariant. A rule engine for that is a rule engine
  looking for rules.
- **Decisions as first-class graph nodes** (`record_decision`,
  `trace_decision_chain`). Prax has this as execution traces, separate from the
  knowledge graph, and `trace_search` already answers "what did I do before".
  Merging the two stores is a large change with no named problem behind it.
- **Ontology/SHACL governance and the enterprise ingestors.** Wrong domain.

## The claims, audited

The README's headline numbers deserve the standard treatment:

> "Node search (118k nodes): 24 ms → 0.004 ms (**6,000× faster**)"
> "Embedding cache hit: cold load → revision-based cache (**10× throughput**)"

These are **cache-hit versus cold-load comparisons**, not like-for-like
algorithmic wins — "24 ms → 0.004 ms" is the shape of *an index was added* or
*the answer was already in memory*. The project is honest about the provenance:
the measurements are described as **historical, recorded in the CHANGELOG
rather than asserted by automated tests**, with variance acknowledged across
hardware, topology and backend. Quoting "6,000× faster" without that context
would be exactly the secondary-summary embellishment the
[agent-harness survey](agent-harness-engineering-survey.md) note warns about.

## What it says about Prax

The uncomfortable comparison is not the feature list — it is that they treat
**auditability of the knowledge itself** as the product, where Prax treats
auditability of the *agent's actions* as the product. Prax's governance layer
knows which tool ran and with what risk tier; it is much weaker on "which claim
in memory came from where, and what did we believe when". Provenance stamping
(2026-08-07) and #65 are both steps along that axis, and it is worth knowing
that the axis exists and that someone is building a whole company on it.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Separate valid time from record time on supersede** — `valid_until` currently means "when we found out" | 📋 task #65 |
| Named conflict-resolution strategy (`credibility_weighted` using provenance) | ⏸ parked — build it only when a second strategy is genuinely wanted |
| Polyglot store abstraction · Rete/Datalog engine · decisions-as-graph-nodes · SHACL/OWL · enterprise ingestors | ❌ declined |
