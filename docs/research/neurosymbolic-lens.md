# Neurosymbolic architecture — what survives, applied as a lens

**Source:** no single link — TJ's question was the field itself ("anything we can
learn from that to make Prax better?"). Grounded in the standard taxonomy
(Kautz's six integration patterns, AAAI 2020 address), the LLM-Modulo line
(Kambhampati et al., arXiv 2402.01817), and our own prior assessments of
neurosymbolic-adjacent systems: [Lanyon](lanyon-formal-verification.md),
[cdc-lean](cdc-lean-teach-prax-lean.md),
[AxiomProver](axiomprover-imo-formalization.md),
[agentic-todo-flows](agentic-todo-flows.md) (LLM-Modulo/HTN),
[CoALA](coala-cognitive-architectures.md).
**Assessed:** 2026-08-06

**Verdict: adopt ONE principle and ONE concrete fix; decline three classic
neurosymbolic moves that are already settled.** Prax does not need to *become*
neurosymbolic — by the field's own taxonomy it already is one. The value of the
label is a selection rule for which symbolic components to build next, and
applying that rule found a real gap in the memory stack (our repeatedly-named
weakest cell).

---

## Prax is already the Neural[Symbolic] pattern

In Kautz's taxonomy, an LLM that calls deterministic tools is **Neural[Symbolic]**
— the neural system invokes symbolic subroutines. Prax instantiates it more
thoroughly than most systems that wear the label:

| symbolic component | where |
|---|---|
| risk classification + policy over every tool | `governed_tool.py` |
| schema decides, model proposes | prax-lab `planner.py` (a candidate that fails validation is not a plan) |
| evidence decides, model proposes | prax-lab `reporter.py` (a claim citing a hash that doesn't exist is deleted) |
| theorem prover with an axiom-audit trust gate | `lean_tools.py` (`lean_check`) |
| AST/decoder validation before any write lands | `workspace_tools.py:_validate_syntax` |
| SQL over a relational engine | `data_query` (DuckDB) |
| CAS | sympy in the sandbox |
| typed graph memory, bi-temporal edges | Neo4j (`memory/graph_store.py`) |
| architecture rules as executable checks | `scripts/check_layers.py`, `test_credential_registry.py` drift guard |

So "should Prax adopt neurosymbolic architecture?" is the wrong question. The
right one is: **the field has sixty years of symbolic components — which kind is
worth building more of?**

## The selection rule: symbols survive as checkers, not generators

Sort every symbolic component by which side of generation it sits on, and a
pattern falls out that the neurosymbolic literature itself rarely states:

- **Symbolic generators** — planners (PDDL/HTN), rule-based reasoners, semantic
  parsers — *compete with the model*. Every capability jump eats part of their
  job, which is the Bitter Lesson operating at the architecture level, and it is
  why [agentic-todo-flows](agentic-todo-flows.md) found pure symbolic planning
  losing to "deterministic backbone + LLM as gap-filler," and why the
  [harness survey](agent-harness-engineering-survey.md) adopt-row says *delete scaffolding
  as models improve*.
- **Symbolic checkers** — validators, provers, type systems, drift guards,
  hash-grounded evidence — *don't compete with the model*. They gate it. A
  better model produces candidates that pass the gate more often; the gate never
  gets worse. Verification is also asymmetrically cheaper than generation, so
  the checker stays viable at every capability level.

Every symbolic component in the table above that has aged well is a checker.
The one line to keep: **neural proposes, symbolic disposes — and build the
symbols on the disposal side.** This is also what the LLM-Modulo framework
reduces to once you strip the branding, and it is the same policy→property move
as [Weng's](weng-harness-engineering.md) read-only scorer.

The known failure mode of the checker pattern is already banked from
[Lanyon](lanyon-formal-verification.md)/[AxiomProver](axiomprover-imo-formalization.md):
when the same neural system writes both the artifact and the spec it is checked
against, **the spec becomes the unverified surface** (misformalization). A
checker only counts if its ground truth is independent of the proposer.

## The lens, applied: where does neural output flow downstream unchecked?

Auditing Prax's neural→downstream junctions against that rule:

1. **Memory consolidation — the gap, and it is concrete.**
   `memory/consolidation.py` asks the extraction LLM, in the prompt ("if a new
   relation contradicts an existing one … set `supersedes`"), to detect
   contradictions itself. `graph_store.py` has the full symbolic machinery to
   *record* the outcome — bi-temporal `valid_from`/`valid_until`,
   `supersede_relation()` (Zep-inspired) — but supersession fires **only when
   the LLM volunteers it**. The graph is never consulted. If the extractor
   misses the contradiction, both edges stay current (`valid_until: null` on
   both) and the memory stack will happily retrieve "prefers dark mode" and
   "prefers light mode" together, forever. Contradiction detection is exactly
   the kind of thing the symbolic side is *better* at: for single-valued
   relation types, "does a current edge (s, type, ·) with a different target
   already exist?" is one Cypher query at write time. **Adopt: a symbolic
   consistency pass at consolidation** — declare which relation types are
   single-valued (prefers-per-key, lives-in, works-at …), check on write,
   and either auto-supersede the older edge or flag the conflict for the
   LLM to resolve — *detection* symbolic, *resolution* neural if needed.
   This lands in the cell every survey names our weakest
   ([self-improving-agents](self-improving-agents-survey.md),
   [learnable-novelty](learnable-novelty.md)).

2. **Compaction summaries.** Progress-archive folding and (when it ships) ACM
   compaction are neural rewrites with no check that standing instructions
   survive — the constraint [CRUX](crux-shadow-evals.md) already banked. A
   must-retain checklist diffed against the compacted output is a symbolic
   check; already tracked on the ACM row, noted here because the lens re-derives
   it.

3. **LLM-judge scores** (task #36) have no symbolic anchor by nature; the
   mitigation is the audit already queued, not a checker.

## Declined — settled elsewhere, no re-litigation

- **Symbolic planners (PDDL/HTN/LLM-Modulo loops) as Prax's planner.** Settled
  in [agentic-todo-flows](agentic-todo-flows.md) research principle #14:
  deterministic backbone + proven units + LLM gap-filler + hard verifier on
  output. Generator-side symbols; the losing kind.
- **Training-time integration** (semantic loss, knowledge distillation into
  weights). Eleventh GPU-wall sighting, and generator-side anyway.
- **Grammar-constrained decoding.** Needs logit access we don't have on hosted
  models; validate-and-retry (the planner's reject-with-reasons loop) is the
  API-level equivalent and already the house pattern.
- **Full formal verification of agent behavior.** The
  [Lanyon](lanyon-formal-verification.md) verdict stands: the semantic/intent
  gap means "impossible to err" claims overreach; we take provers as *tools*
  (lean_check) not as the substrate.

## Honest limits

- This is a synthesis note, not a paper assessment — the checker/generator
  survival claim is an induction from our own component history plus the Bitter
  Lesson, not a measured result. A capability jump that made symbolic *planning*
  cheap to gap-fill could weaken it.
- "Single-valued relation type" is doing real work in the adopt: the ontology
  of which relations are exclusive is itself authored, and a wrong declaration
  turns the consistency pass into a fact-deleter. Start with a short, obvious
  allowlist and log-only mode before auto-supersede.
- The junction audit here covered three surfaces, not all of them; it is a lens
  to keep applying, not a completed sweep.
