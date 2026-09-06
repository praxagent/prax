# ProofAtlas — AI-first formal mathematics, and the Sendov digestion — assessment

**Source:** [proofatlas.ai](https://www.proofatlas.ai/) — "AI agents pursue proof routes
in parallel, challenge one another, and turn successful ideas into papers and checked
formalizations." Beta; closed platform; no paper, repo, or organisation named on the
site; results credit **Lech Mazur** as "sole named manuscript author, accountable
curator, workflow designer" with **GPT-5.6 Pro** for "exploration, proof development,
exact computational testing, adversarial auditing, and exposition." The headline
result — [Sendov's conjecture](https://www.proofatlas.ai/formalizations/sendov-conjecture/)
(2026-08-05) — was independently digested by Terence Tao
([blog, 2026-08-12](https://terrytao.wordpress.com/2026/08/12/a-digestion-of-the-proof-of-sendovs-conjecture/);
[his Lean repo](https://github.com/teorth/sendov)). Assessed 2026-09-06.

**Verdict: document + adopt two patterns; don't adopt the platform.** The platform is a
closed maths vertical in beta with no API and no methodology paper, so there is nothing
to consume. But it is the first place I have seen two lessons Prax already *banked*
from assessments turned into **first-class status fields**, and the Tao digestion is
the cleanest real-world example yet of a third lesson we keep re-learning. Details
below; the adopts are cheap and land in things Prax already has.

## What it is (claims as stated; none run here)

- A shared evidence graph: "keeps claims, dependencies, useful failures, and open
  questions connected so others can continue the work." 243 research workspaces,
  215 open conjectures, "733,684 unique investigation lines" retained.
- Lean as the checker. Each formalization page shows the exact declaration, a
  retained build transcript, disclosed axioms (`propext`, `Classical.choice`,
  `Quot.sound` — the standard three), line counts of first-party Lean excluding
  Mathlib, and "Unfinished proof steps: None."
- **A visible acceptance boundary.** The Sendov page, a month after Tao's write-up,
  still says *"Review pending"* with four open review questions — "formal evidence,"
  "statement alignment," "result boundary," "public wording" — and states plainly:
  *"The Lean development is not a line-by-line formalization of the manuscript"* and
  *"manuscript-to-formal-statement alignment is not yet reviewed."*
- Eight "outcomes" claimed (number theory, complex analysis, graph theory,
  combinatorial game theory); the only one with independent external verification I
  could find is Sendov.

**Calibration.** One named human, a closed system, marketing numbers, and I did not
reproduce any Lean build. The Sendov result itself is real: Tao checked it, reorganised
it, formalised his own version, and found it proves the *stronger* Phelps–Rodriguez
conjecture. That is the one fact in this assessment with a source outside the vendor.

## What Prax already had — and where ProofAtlas is ahead

| Lesson | Where Prax banked it | Where ProofAtlas makes it structural |
|---|---|---|
| **The spec is the un-verified surface** — a checked proof of the wrong statement is worthless | [axiomprover-imo](axiomprover-imo-formalization.md), [lanyon](lanyon-formal-verification.md) ("misformalization"); `lean_check` ships the axiom audit gate from [cdc-lean](cdc-lean-teach-prax-lean.md) | **"Statement alignment" is a separate review question from "formal evidence."** A result can be Lean-green and still *Review pending* on alignment. Prax's `lean_check` reports "compiled + axioms clean" as one status. |
| **Keep the complete record; failures are evidence** | [prolong](prolong-programmatic-memory.md) ("compact the context, never the record"), the failure journal, `trace_search` | Retained "useful failures" and "blocked routes" are first-class nodes in the graph that later agents open *on purpose*. Prax's failure journal is append-only in intent, but this review found its Qdrant leg has never worked and nothing surfaces past failures to a new turn by default. |
| **Correct ≠ understood; tests pass, nobody wants the PR** | [proofjudge-taste](proofjudge-taste.md), [weakest-not-shortest](weakest-not-shortest.md) | The Tao digestion: the AI proof was **~90,000 Lean lines**, correct, and "not human-digested"; a human with AI help took several days to compress it to **~15,000 lines**, place it in the literature, and discover it proved something stronger. Compression ratio was the taste signal — and the stronger theorem was invisible until the proof was understood. |

Two smaller convergences, already ours: the "checked evidence bound to a pinned
source commit with a fresh local replay" is exactly the commit-stamped eval run
discipline; disclosing axioms per result is the `#print axioms` gate we adopted.

## Adopt (two, both small)

1. **Split "checked" from "aligned" in `lean_check` and the verification ledger.**
   Today a green `lean_check` reads as *verified*. Add a second field the tool
   cannot set itself — `statement_alignment: unreviewed | reviewed-by:<who>` — carried
   into the ledger row and into any reply that cites the check. The house rule already
   says the spec is the un-verified surface; this makes the tool say it every time
   instead of relying on the model to remember. Same field belongs on any
   `verify:` regex in the golden suite: the regex is a spec, and the 08-08 note
   ("two new cases rejected the correct answer") was a statement-alignment failure.
2. **Surface retained failures as a route, not a log.** When a new turn starts on a
   space that has prior failed attempts (progress `open_threads`, failure-journal
   entries, `[INCOMPLETE FAN-OUT]` traces), inject a one-line pointer *"N prior routes
   failed here — `progress_detail(slug, date)`"* rather than nothing. ProofAtlas's
   whole pitch is that the next agent opens the failed route deliberately; Prax stores
   the route and never mentions it. Prerequisite: fix the failure journal's Qdrant leg
   (suite review 2026-09-05, memory batch).

## Bank (no build)

- **The digestion ratio as a review signal.** 90k → 15k with a stronger theorem out
  the other side is the strongest evidence yet for [ProofJudge](proofjudge-taste.md)'s
  axis and for the anti-spike epistemics in
  [weakest-not-shortest](weakest-not-shortest.md): the *first* correct artifact an
  optimiser produces is rarely the one you want to keep, and the compression a human
  achieves on it measures how far from understood it was. For #29 this argues for a
  size/MDL term that is *reported* next to the accept decision, not just the Occam
  tie-break.
- **"Review pending" a month after Tao** is the right default. The vendor did not
  upgrade its own status because a famous mathematician blogged; the review questions
  are still listed. That is the calibration discipline the verification ledger is
  supposed to enforce — worth pointing at when the temptation is to mark something
  ✅ because it worked once live.

## Declined

- **The platform.** Closed beta, maths vertical, no API, no methodology paper, one
  named operator. Nothing importable.
- **Reading the eight "outcomes" as eight Sendovs.** One is externally verified; the
  rest are the vendor's own review status, which — to their credit — says so.
- **A "prove open conjectures" lane for Prax.** Same answer as
  [cdc-lean](cdc-lean-teach-prax-lean.md): Prax's value is verify-and-iterate on
  problems someone actually has, not competitive proving.
