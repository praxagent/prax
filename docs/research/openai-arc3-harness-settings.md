# OpenAI — "How two settings tripled our ARC-AGI-3 scores"

**openai.com blog (2026-07).** OpenAI re-ran ARC-AGI-3 with two Responses-API
settings they ship in ChatGPT/Codex — **retained reasoning** (private
reasoning messages kept across tool calls/turns via `previous_response_id`)
and **compaction** (summarize-and-continue instead of the official harness's
rolling truncation at 175K characters). GPT-5.6 Sol went from **13.3% → 38.3%**
RHAE on the public set (estimated human baseline 48%), with **~6× fewer output
tokens**. GPT-5.5 under the official harness scored 0.4%.

**Verdict: document + adopt ONE code fix — retained reasoning must survive the
keyless proxy — and bank the meta-lesson for the eval engine. The compaction
half we already do.** This is the strongest external evidence yet for two
standing Prax positions, and it exposed one real bug in our own stack.

---

## The meta-lesson is our house rule, from the other side

"Benchmarks rarely measure AI models in isolation. They also measure less
visible choices about API settings, harness design, and prompting" — that is
**audit-the-checker-first** ([eval-rigor](eval-rigor-review-2026-07.md); lanyon /
proofjudge / axiomprover), stated by a lab about a benchmark instead of by us
about a scorer. The same week this landed, our own Terminal-Bench probe
produced 0/11 that turned out to be **two runner artifacts** (tests starved of
network for their `uv` bootstrap; `$TEST_DIR` never exported so pytest
collected nothing) — the model's "score" was the harness's bug, twice. The
counting continues: measurement error has now explained the initial "gap" more
often than capability has.

ARC's defense (a generic harness is *fair* because it's the same for everyone)
and OpenAI's rebuttal (a generic harness measures a model *outside its
production shape*) are **both right**, and [harness-generalization
](harness-generalization.md) already gave us the vocabulary: the harness is a
problem- and model-dependent hyperparameter. For cross-model comparison, fix
the harness; for "what can this system actually do," run the production shape.
Prax's eval matrix wants the second — Prax IS a harness; benchmarking Prax
with its features off would be measuring someone else's product.

## What this found in Prax: the keyless path discards reasoning

`prax/agent/llm_factory.py` gates the Responses API on
`use_responses_api=(_needs_responses_endpoint and not _base_url)`. The
`not _base_url` guard exists for third-party OpenAI-compatible providers
(OpenRouter et al., which genuinely don't implement `/v1/responses`) — but
**keyless Prax sets `OPENAI_BASE_URL` to the secrets proxy with real OpenAI
behind it**, and the guard can't tell the difference. Consequences, in
production keyless mode:

1. **Every OpenAI model runs on Chat Completions → private reasoning is
   discarded after every tool call** — exactly the official-harness failure
   mode measured at ~3× score / 6× tokens on ARC-AGI-3.
2. **Latent 404**: `-pro` / o-series models are responses-only; routing them
   to chat-completions through the proxy fails outright.

The adopt is a disentangling fix: distinguish "base URL = proxy fronting real
OpenAI" from "base URL = third-party provider" (explicit flag, default =
current behavior), enable `use_responses_api` + reasoning retention through
the proxy, and verify the proxy forwards `/v1/responses` before flipping
anything. Flag-gated, eval-gate governs the flip — the 3× is *their* number on
*their* benchmark, not a promise about ours.

## What Prax already does right

The official harness's rolling truncation is the naive half of the story, and
Prax doesn't do it: on context overflow the orchestrator walks a ladder —
`clear_old_tool_results` → `compact_history` → `truncate_history` (truncation
as last resort, `prax/agent/context_manager.py`). OpenAI's numbers are
production-scale evidence FOR that design, and for the
[ACM](acm-agentic-context-management.md) adopt-row that makes compaction
agent-initiated rather than only overflow-triggered. Cite, not a new row.

## For the ARC-AGI-3 adapter (parked flagship eval)

When the adapter lands ([arc-agi-3](arc-agi-3-schema-harness.md)), this post
sets the design constraint: **run Prax's production shape** (memory, spokes,
compaction, retained reasoning) and **record the harness settings in the
result record** alongside model/config/commit — a score without its harness
fingerprint is now demonstrably uninterpretable (13.3 vs 38.3 is the same
model). Numbers to calibrate against: GPT-5.6 Sol 38.3% public-set RHAE with
frontier-lab tuning; estimated human tester average 48%.

## Honest limits

Vendor post about vendor settings — the recommendation "compare models using
our production settings" is also a marketing position, and the numbers are
self-reported on the public set with no per-setting ablation published (the
3×/6× figure is the combined effect for the max config). "This isn't the first
time a public eval dropped our reasoning messages" is asserted, not shown.
Their harness remains below their estimated human baseline (38.3 vs 48). None
of this weakens the part we act on, which is structural, not the scores.

## Related

- [eval-rigor-review.md](eval-rigor-review-2026-07.md) — audit the checker first; this
  is the vendor-side sighting, and the TB runner artifacts are ours.
- [harness-generalization.md](harness-generalization.md) — the harness as a
  hyperparameter; no universally superior harness.
- [acm-agentic-context-management.md](acm-agentic-context-management.md) —
  compaction-over-truncation, now with production-scale numbers behind it.
- [arc-agi-3-schema-harness.md](arc-agi-3-schema-harness.md) — the adapter
  this constrains.
