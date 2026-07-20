# Verify efficiently, and commit: a per-turn discipline

*Design synthesis from the 2026-07-20 GPQA + praxbench probes. Status:
mechanism M1 implemented (flag-gated); M2–M3 proposed for the eval gate.*

## The problem, from three failures with one root

Tonight's probes exposed three distinct failure modes that are really one missing
capability — **per-turn metabolic discipline**: Prax doesn't reliably bound its
own generation, verify the claims its answer rests on, or guarantee it commits an
answer at all.

| Failure (observed) | What happened | Facet |
|---|---|---|
| **GPQA non-commitment** | 3 of 6 wrong GPQA cases were `pred=None` — 12–13k tokens of reasoning in a single model call, the answer line never emitted (or the 120s wall clock cut it off mid-generation). One was a 0-token hang. | doesn't **commit** |
| **Jacobian, by-hand** | Correct counterexample, but it verified the load-bearing Jacobian determinant *at one point by hand* and took "constant everywhere" on faith — with `sympy` right there. | doesn't **verify** |
| **Jacobian, computed** | Correct and verified — but it ran `run_python` **six times** for ~124k tokens on a job that needs one or two calls. | doesn't verify **efficiently** |

The unifying target is the phrase in the title: **verify the things your answer
depends on, do it efficiently, and always land a committed answer within budget.**
The existing `_BUDGET_AWARE_HINT` (from the anti-spiral work) covers the *commit*
half. Tonight's gap is the *verify-efficiently* half — and a structural hole
under the non-commitment case that no between-steps hint can reach.

## Mechanisms

### M1 — Load-bearing verification, once (prompt principle) — IMPLEMENTED

A system-prompt principle (`_VERIFY_DISCIPLINE_HINT`, flag
`VERIFY_DISCIPLINE_ENABLED`, default off): *when your conclusion rests on a claim
a tool can check — a non-trivial calculation, a symbolic result, code output, a
lookup — verify it with the tool rather than asserting it from mental arithmetic;
and verify it **once**, then trust the result.* This targets the Jacobian
variance from both sides: reach for the tool on a load-bearing checkable claim
(the by-hand miss), but don't re-run the same check six times (the efficiency
miss). It's deliberately scoped against the tool-economy hint (which says *don't*
tool-call for things you already know) — verification is specifically about
closing a **load-bearing, checkable** gap, not general tool use.

**Honest caveat:** prompt hints have underperformed before (the budget-aware hint
did not, on its own, stop the non-commitment timeouts — see M2). A principle
shifts a *decision distribution*; it is not a guarantee. Which is exactly why it
ships default-off and behind the eval gate, and why we built the measurement
(M4) in the same pass.

### M2 — Bounded generation + forced commitment (structural) — PROPOSED

M1 cannot fix the GPQA non-commitment, because that failure is a **single model
call** that generates 13k tokens and never lands the answer — there is no
between-steps seam for a middleware to inject into. The structural fixes:

- **A per-call output-token cap** for the orchestrator (configurable). A model
  that cannot emit 13k tokens is forced to be concise and reach its conclusion.
  This is also a general efficiency lever (it bounds the Jacobian over-generation
  too). *Risk:* set too low it truncates legitimate long answers — so it needs
  eval validation across benchmarks before any default change, and likely wants
  to be generous (e.g. a few thousand tokens) rather than tight.
- **Answer-format landing**: on a scored multiple-choice item, a truncated turn
  should still surface the best-supported option. On an open-ended item, an
  honest "I can't determine this" is the valid commit. The commit instruction
  must stay honest — commit the *best-supported* answer or an honest unknown,
  never a fabricated guess (the prime directive).

### M3 — Verify-once memoization (structural) — IMPLEMENTED (safe subset)

`IdempotentToolCache` (`loop_middleware.py`, `TOOL_MEMOIZE_ENABLED`, default off):
within one turn, an identical repeat of a **pure, side-effect-free read** (web
search/fetch, memory/workspace/conversation/trace lookups) returns the prior
result instead of re-executing — saving the latency, external call, and tokens of
a redundant fetch. Correct by construction: the cache is **per-invoke** (bound in
the orchestrator worker via `use_tool_cache`, never reused in a later turn) and
only `is_memoizable_read` tools are eligible.

**Honest scope caveat.** This deliberately does NOT memoize the thing the A/B
actually caught — `run_python`. That tool has **side effects** (writes files,
reads changing state), so returning a cached result would be a correctness bug;
`run_python`/shell/writes/browser-navigation always pass straight through. So M3
removes redundant *reads* (a real, common waste) but does **not** fix the
`run_python` over-verification. That remains open — the honest levers there are
prompt-level consolidation ("verify in one comprehensive computation") and the
existing spiral detector, neither of which is a clean structural fix, because
safely deduping an effectful tool is not possible without per-call purity
metadata Prax doesn't yet carry.

### M3-followup — evidence

The 2026-07-20 A/B (M1 on vs off, praxbench Q1, trace-graded, n=3) is the reason
M3 exists and is scoped this way: the M1 *prompt* hint raised verification (2/3 →
3/3) but **worsened** efficiency (0.34 → 0.24, tokens 169k → 231k). Prompt can
raise "do verify"; it did not deliver "verify once." A structural cache is the
right tool for the efficiency half — but only where it's provably safe (reads),
which is why the effectful-tool waste stays a separate, open problem.

### M4 — Trace-grading as the measurement — DONE (this pass)

`prax/eval/trace_grade.py` scores the process on exactly these axes — *committed*,
*verification*, *efficiency* — so the eval gate can tell whether M1–M3 actually
move the process, not just the answer. See `docs/guides/trace-grading.md`. The
two Jacobian runs (by-hand 0.65, computed 0.83, ideal 1.0) are the calibration.

## Why this is general (not benchmark-tuning)

None of these encode a GPQA or praxbench answer. "Verify load-bearing claims,
efficiently, and always commit within budget" improves Prax on *every* task with
a checkable step or a length/latency budget — math, coding, research, ops. The
prime-directive test holds: someone who knows the benchmarks could not tell which
task these target, because they target the *class* (metacognitive budget
discipline), not any instance. The failure mode they remove — "reasoned a lot,
verified nothing, committed nothing" — is a loss everywhere, not just on GPQA.

## Rollout

M1 is flag-gated default-off. Before any flip: run the flag through the eval gate
(`make eval-benchmark BENCH=gpqa` / `math` / `mmlu_pro`, with `PRAX_EVAL_FULL_DATASETS`)
and read **both** the pass-rate *and* the trace-grade — M1 should raise
verification without wrecking efficiency; M2's cap should convert `pred=None` into
attempts without truncating real answers. Ship only what the numbers support.
