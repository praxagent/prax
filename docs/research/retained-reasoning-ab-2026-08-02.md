# Eval-gate A/B: retained reasoning through the keyless proxy (2026-08-02)

**Flags under test:** `OPENAI_BASE_URL_IS_OPENAI` + `OPENAI_RETAIN_REASONING`
(shipped default-off in #189, motivated by
[OpenAI's ARC-AGI-3 harness post](openai-arc3-harness-settings.md), which
reported ~3× score at ~6× fewer output tokens from retaining reasoning +
compaction).

**Result: no measurable effect on task success; a consistent but
statistically unestablished reduction in tokens. Flags stay default-off and
are NOT recommended in `.env-example`.** The pre-registered kill condition was
not triggered — but surviving a kill condition is not evidence of benefit, and
this run is the difference between those two things.

---

## Setup

Pre-registered before the run (`prax/eval/prereg.py`, log in
`$PRAX_EVAL_DIR/eval_preregistrations.jsonl`), per the
[grounding-gap](ilands-grounding-gap.md) adopt row.

- **Suite:** the capability suite (7 cases, tool-using, deterministically
  graded). Chosen because the claimed mechanism is reasoning carried *across
  tool calls* — a single-shot QA benchmark cannot express it.
- **Model:** `o4-mini`. Retention only engages for models routed to the
  Responses API (`-pro` + o-series); a chat-completions model makes both arms
  identical and the test vacuous.
- **Path:** keyless through the secrets proxy — the exact production shape the
  flags exist to fix.
- **Runs:** 3 per arm (1 initial + 2 replicates), independent suite dirs.

## Results

| arm | pass rates (3 runs) | mean | avg tokens/case | mean | range |
|---|---|---|---|---|---|
| retention **off** | 0.714, 0.857, 0.857 | **0.809** | 85,155 / 40,578 / 44,148 | 56,627 | 40.6k–85.2k |
| retention **on** | 0.857, 0.857, 0.714 | **0.809** | 61,295 / 40,509 / 32,813 | 44,872 | 32.8k–61.3k |

**Pass rate: identical means (0.809 vs 0.809).** Both arms wander between 5/7
and 6/7 across runs. The *first* run alone showed 5/7 → 6/7 and looked like an
improvement; replication shows that single case is exactly the run-to-run
noise the pre-registration committed in advance to ignoring.

**Tokens: lower with retention in all 3 paired runs** — deltas of 23,860 /
69 / 11,335 (mean −11,755, ≈21%). Direction is consistent, but one pair is
effectively zero (0.2%), the ranges overlap heavily, and a sign test at n=3
all-one-direction gives p = 0.125 — **not significant at 0.05**.

## Verdict

Applying the pre-registered condition: pass-rate with retention is *not* lower
(means are equal) and tokens are *not* higher, so **the flag is not killed**.
It is also not endorsed:

- **Code default: unchanged (off).**
- **`.env-example`: not recommended.** The earlier wording implying the two
  flags should be enabled together was written from the vendor's claim, not
  from measurement, and has been corrected.
- `OPENAI_BASE_URL_IS_OPENAI` remains **necessary** for keyless OpenAI use
  independent of this result — without it, o-series models 404 and every model
  loses reasoning between tool calls. That is a correctness fix, not a
  performance flag; only the *retention* half was under test here.
- The token direction justifies a larger run (more cases, more replicates)
  before any recommendation. Not a priority: the effect, if real, is a cost
  saving on one model family.

## Why this run is worth reading twice

**The single-run result was wrong in the direction we wanted.** One run said
"+1 case and 28% fewer tokens" — a tidy confirmation of the vendor's claim.
Three runs said the pass-rate half was noise. This is the first real use of
the [significance-testing](harness-generalization.md) adopt row, and it paid
for itself immediately: without replication this would have been written up as
a win and the flag flipped.

**The A/B also found a shipped bug before it measured anything.** Both arms
initially returned 0/7 with every case errored: the Responses API returns list
content blocks and the orchestrator concatenated them with a string, crashing
every turn (fixed in `prax/agent/message_text.py`, PR #195). The experiment's
**inconclusive guard** — refusing to judge an arm that produced no result —
is what stopped "0.0 vs 0.0, no difference" from being recorded as a finding.
An arm that crashed is not an arm that tied.

**Two honest deviations.** (1) The kill condition specified *output* tokens;
the suite reports average *total* tokens per case, so a near neighbour of the
pre-registered metric was measured — named rather than silently substituted.
(2) n=7 cases gives the suite a 95% CI of roughly 36–92% on a single arm; only
a very large effect is detectable, and the design could not have found a small
real improvement. "No measurable effect" here means *this instrument cannot
see one*, not *there is none*.

## Related

- [openai-arc3-harness-settings.md](openai-arc3-harness-settings.md) — the
  claim under test, and the origin of the flags.
- [harness-generalization.md](harness-generalization.md) — the
  significance-before-a-win row this validates.
- [flag-eval-campaign-2026-07-08.md](flag-eval-campaign-2026-07-08.md) — the
  precedent campaign; same standard, same willingness to reject.
- [ilands-grounding-gap.md](ilands-grounding-gap.md) — pre-registration.
