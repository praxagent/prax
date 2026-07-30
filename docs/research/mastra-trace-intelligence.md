# Mastra — Trace Intelligence (beta announcement)

**mastra.ai, product blog.** Mastra is a TypeScript agent framework with an
observability cloud. Trace Intelligence (beta): completed traces → extracted
signals (goal, sentiment, behavior, outcome) → embeddings → **UMAP + HDBSCAN
clustering** → themed groups with volume/trend views, used to "prioritize
fixes, select traces for evals, and verify changes."

**Verdict: document + adopt ONE idea — unsupervised clustering over the trace
embeddings Prax already has; don't adopt the platform; treat all claims as
vendor marketing (beta, no pricing, no numbers).**

---

## What Prax already has, for honest comparison

| Mastra | Prax |
|---|---|
| Trace store + viewer | Execution Graphs panel, `trace_detail` |
| Signal extraction per trace | span summaries, `claim_audit` flags, `PREDICTION_ERROR` entries from the prediction tracker |
| Semantic search over traces | `trace_search` — **the same Qdrant embedding substrate their pipeline needs** (`prax_trace_summaries`) |
| Reviewer narrative | `review_my_traces` |
| "Automate fix generation" (their roadmap step 3) | #29 self-regeneration — the whole design |

The convergence point is the same one Capy hit: commercial platforms keep
building toward the loop Prax designed first (observe → cluster → fix →
verify). Mildly validating, not actionable by itself.

## The one genuinely good idea

**Clustering is query-free.** `trace_search` answers questions you know to ask;
clustering surfaces the theme you did not know existed — "a fifth of this
week's traces are browser-unreachable failures" emerges from density, not from
someone guessing the right query. Two uses map straight onto existing Prax
threads:

1. **Mining production traces into eval cases.** "Select traces for evals" is
   the grounded-signal adopt row made concrete: real-usage failures, clustered,
   become golden candidates — a test distribution *authored by reality* rather
   than by us. Directly feeds the #29 gate's grounded side and the ARTS
   failure-provenance pattern already banked.
2. **Fix prioritisation by mass.** This session fixed mobile bugs in report
   order; a cluster view would have ranked them by how much real usage each
   theme burned.

Cost check: the embeddings already exist in Qdrant; UMAP and HDBSCAN are
CPU-cheap Python libraries. This is a batch job over data we store, not
infrastructure.

## What not to adopt

The platform and its telemetry pipeline — Prax's LGTM stack plus its own trace
records already occupy that ground, and "sentiment" as a first-class trace
signal smells like a chat-product metric, not an agent-quality one. Their
roadmap's "automated fix generation" is #29's territory; nothing in a beta
announcement changes the gate-first design there.

## Honest limits

One marketing post, summarised by fetch; the beta was not used; UMAP/HDBSCAN
are the only technical specifics disclosed and no result is quantified. The
adopt idea stands on our own data's existence, not on their claims.

## Related

- [ilands-grounding-gap.md](ilands-grounding-gap.md) — trace-mined evals are
  the grounded signal that row asks for.
- [arts-agentic-tree-search.md](arts-agentic-tree-search.md) — failure
  provenance diagnosis; clustering is its unsupervised front end.
- [capy-swe-agent-platform.md](capy-swe-agent-platform.md) — the previous
  commercial-convergence sighting.
