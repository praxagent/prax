# Chroma Foundation — a vector-DB company moves up into the memory layer

**Source:** [trychroma.com/foundation](https://www.trychroma.com/foundation) —
Foundation, announced by Chroma (the ChromaDB company). An AI knowledge-management
platform that *"learns from your agent sessions and maintains a self-improving wiki
of everything you and your team know."* Tagline: **"Your agents are only as good as
what they know."** Integrates with Claude Code, Codex, Cursor and Slack **over MCP**;
built on ChromaDB plus **Context-1**, their retrieval engine. Feature list:
real-time sync, **versioning & diffs, lineage & citations, concurrency control**,
continuous background improvement with human feedback. SOC 2 Type 2. Claims
**"SOTA on the BEAM memory benchmark."** **$30/user/month + overages**, billed as
infrastructure (database ops + embedding inference + LLM inference).

**Verdict: not a harness competitor — a competitor to Prax's *memory subsystem*,
and on that subsystem specifically they have shipped as named features three
things we carry as open punch-list items. Document + adopt two, and take the
benchmark gap seriously.**

## The honest comparison, stated at the right altitude

Prax and Foundation are not the same kind of thing, and pretending otherwise would
flatter us. Foundation **deliberately does not own the agent loop** — it sells the
knowledge layer to whoever does (Claude Code, Codex, Cursor). Prax's whole thesis
is the opposite: the harness is the product, and memory is a component of it.

So "how do we compare" has two answers, and only the second is uncomfortable.

**As products: barely overlapping.** They are an enterprise knowledge layer with a
per-seat price; Prax is an open-source, self-hostable, keyless agent harness with
governance as its thesis. A team could plausibly run Foundation *and* Prax.

**As memory systems: directly comparable, and we do not currently win.** Prax has
a genuine two-layer store (Qdrant vectors + Neo4j graph), consolidation, the
Library as a user-owned wiki, `.progress/` session records, and — since 2026-08-15
— real bitemporal validity. That is a real architecture. But three of Foundation's
front-page bullets map exactly onto things we have as *tasks*, not features.

## Where they are ahead, and it is our own backlog

| Foundation ships | Prax status |
|---|---|
| **Concurrency control** | **#64, still open.** Atomic writes landed 2026-08-14; *collision visibility* did not. Two spokes can still overwrite each other's work silently. |
| **Lineage & citations** | Partly. The [dereference-beats-search](tencentdb-agent-memory.md) work re-attached session ids through progress compaction — but there is no general "which source did this belief come from" edge across the memory stack. |
| **Versioning & diffs** | No. Memory is superseded, not versioned; `supersede_relation` closes an interval, it does not keep a diffable history you can browse. |
| **A memory benchmark number** | **LoCoMo only** (`prax/eval/benchmarks/locomo.py`, inline keyless seed set). No BEAM, and nothing that tests memory under *evolving state*. |

The concurrency line is the one that should sting. This is now the **third
independent signal** on #64: Anthropic measured [18 of 30 agents choosing the
identical branch name](multiagent-failure-modes.md), [Cordis](cordis-spatiotemporal-composability.md)
gave the property its formal name (Definition 19 — independence requires
commutation), and now a shipping product treats concurrency control as table
stakes for a multi-agent knowledge store. We have the theory, the measurement and
the market all pointing at a task we have half-finished.

## The benchmark gap is the concrete finding

**BEAM** — *Beyond a Million Tokens: Benchmarking and Enhancing Long-Term Memory
in LLMs* (ICLR 2026) — is scenario-based: agents act in multi-step environments
with **evolving state**, information appears early and is needed much later, and
the paper's point is that a 1M-token window does **not** remove the need for a
memory system.

That is the exact axis LoCoMo does not cover. LoCoMo is a long *dialogue* with
facts placed at distance; BEAM is a long *task* with facts that **change**. Prax
just shipped bitemporal validity (#65) — the machinery for "this was true then,
this is true now" — and **has no benchmark that would notice if it broke.** A
capability with no eval is a capability we cannot defend.

So the adopt is not "beat their number". It is: **we have an untested subsystem,
and there is a public benchmark aimed precisely at it.**

Two honest cautions on their claim. First, "SOTA on the BEAM memory benchmark"
appears on a product page with no linked evaluation — their
[research index](https://www.trychroma.com/research) lists Context-1, Context Rot,
Generative Benchmarking, chunking and embedding adapters, and **no BEAM writeup**.
First-party, unreproduced. Second, the benchmark is new enough that
[saturation](benchmark-saturation.md) is not yet the risk; contamination might be.

## What they have already given us, for free

**Context Rot** (July 2025) is genuinely good work and Prax already cites it — it
is upstream of the LID principle in [rlm-harness-lid](rlm-harness-lid.md) and shows
up again in [CRUX](crux-shadow-evals.md)'s instruction-drift finding. 18 models,
input length isolated as the sole variable, and the finding that *"models do not
use their context uniformly"* — plus the counter-intuitive result that **shuffled
haystacks outperformed logically structured ones**, which is a standing warning
against assuming tidier context is better context.

Worth noting what that means commercially: the research that makes the case for a
memory product is the same research that makes the case for a *harness* that
manages context. We have been reading their evidence for a year and reaching a
different conclusion about who should own the fix.

## Where Prax is genuinely different

- **Governance.** Foundation offers SOC 2 and granular access control — that is
  *enterprise compliance*, a different axis from `governed_tool.py`'s per-call risk
  tier, audit record and lethal-trifecta guard. Neither subsumes the other, and we
  should stop describing our governance as though it competes with theirs.
- **Memory writes as governed actions.** The [CoALA](coala-cognitive-architectures.md)
  audit already logged this as a gap; a knowledge layer that sits *outside* the
  agent loop structurally cannot close it, because it never sees the tool call. If
  we build it, it is something they cannot copy without owning the loop.
- **Keyless and self-hostable** against $30/user/month. Not a quality argument —
  a deployment-model argument, and it is the one real reason a privacy-sensitive
  user picks Prax's memory over theirs.

## Honest limits of this assessment

A product page, not a paper. I have not run Foundation, cannot see Context-1, and
the SOTA claim is unverified — the same first-party caution applied to
[ExtractBench](extractbench.md) and [Anthropic's multiagent post](multiagent-failure-modes.md).
The feature list may describe intent rather than depth; "versioning & diffs" on a
marketing page can mean anything from a full history model to a changelog. What I
am treating as solid is only the *shape* of what they chose to put on the front
page — which is itself evidence about what a company with real retrieval expertise
thinks matters in an agent memory store.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Add a BEAM-style memory-under-evolving-state benchmark adapter** — Prax's bitemporal validity (#65) currently has no eval that would catch a regression; LoCoMo tests distance, not change | 📋 queued — the concrete gap |
| **Finish #64's collision visibility** — 3rd independent signal (Anthropic 18/30, Cordis Def. 19, now a shipped product treating it as table stakes) | 📋 raised in priority |
| **Lineage as a general memory property** — a belief should carry a pointer to the source that produced it, not just the summary that replaced it | 📋 queued — 2nd sighting after [TencentDB](tencentdb-agent-memory.md) |
| Context Rot as prior art | ✅ already cited ([rlm-harness-lid](rlm-harness-lid.md), [crux](crux-shadow-evals.md)) |
| ChromaDB in place of Qdrant | ❌ declined — no measured reason to switch a working store |
| Foundation as Prax's memory layer | ❌ declined — a knowledge layer outside the agent loop cannot make memory writes governed actions, which is the thing we actually want |
| Memory-versioning with browsable diffs | ⏸ parked — real capability, but the bitemporal model is one week old; measure it before extending it |
