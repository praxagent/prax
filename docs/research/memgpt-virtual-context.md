# MemGPT — virtual context management (arXiv 2310.08560)

**Packer, Wooders, Lin, Fang, Patil, Stoica, Gonzalez (UC Berkeley), 2023.**
"MemGPT: Towards LLMs as Operating Systems."

**Verdict: document + adopt one small mechanism.** Prax independently arrived at
this architecture — including the self-edited working context — and in the graph
layer went past it. Reading the paper carefully enough to check the claim turned
up a real defect in our own code: working memory was selected by recency alone,
with the `importance` field written and never read. That is now fixed. The
remaining candidate is memory *pressure* as a signal, which is unbuilt.

---

## What the paper proposes

An operating-systems analogy, taken seriously rather than decoratively. The
context window is RAM; everything else is disk; and the *LLM itself* is the
process that decides what gets paged in.

| MemGPT term | What it is |
|---|---|
| **Main context** | The context window. System instructions + working context + a FIFO queue of recent messages. |
| **External context** | Everything outside it — recall storage (past conversation) and archival storage (arbitrary facts). |
| **Working context** | A small, persistent, in-prompt scratchpad the model edits itself with `core_memory_append` / `core_memory_replace`. |
| **Memory pressure** | When the FIFO queue nears the limit, a warning is *inserted into the context* so the model can act before it is truncated. |
| **Flush / eviction** | Evicted messages are recursively summarised and written to external context, so nothing is lost, only demoted. |
| **Interrupts / yield** | Function-call control flow: the agent runs, yields to the user, or chains another call via a heartbeat request. |

Evaluated on multi-session chat (deep memory retrieval over the MSC dataset) and
document QA / nested key-value retrieval beyond the context window.

The load-bearing idea is **self-directed paging**: the model is told how full its
memory is and given tools to manage it, rather than a framework silently
truncating history behind its back.

---

## What Prax already has

Most of it, arrived at independently — which is worth stating plainly rather
than dressing this up as a bigger gap than it is.

| MemGPT | Prax equivalent |
|---|---|
| Archival storage | `memory_remember` / `memory_recall` (Qdrant vectors) |
| Recall storage | `conversation_search` — same name, same job |
| Recursive summarisation on eviction | `progress_service`: `MAX_FILE_CHARS = 6000`, `MAX_RECENT_ENTRIES = 10`, LLM compaction when full |
| Per-session state that survives the context boundary | `progress_read` at session start, `progress_append` at session end |
| Working scratchpad | `agent_plan` — auto-injected every turn, cleared at end of turn |
| Function-call control flow | The whole tool loop; `self_upgrade_tier` is an interrupt-shaped self-modification |
| — | **Neo4j entity graph** (`memory_entity_lookup`, `memory_graph_query`) — MemGPT has no equivalent |
| — | **Governance on every tool** — memory writes pass the same audit as anything else |

Prax's progress files are, precisely, MemGPT's eviction discipline: bounded by
construction, compacted by an LLM when they overflow, with the detail demoted to
`.progress/` files fetched on demand rather than deleted.

---

## The gap — corrected

**My first reading of this was wrong, and the correction is worth recording.**

I claimed Prax never injected `memory_stm_*` into the prompt, having grepped
`orchestrator.py`, found it read only to hunt for a timezone string, and
generalised from one file. In fact `memory_service.build_memory_context()` emits
a `## Working Memory (Scratchpad)` block every turn and the orchestrator
includes it. **Prax already had MemGPT's self-edited working context.** Checking
one call site and concluding "never" is exactly the error this project asks
itself not to make.

The real gap was smaller and sharper:

```python
for entry in stm_entries[-5:]:                       # the five most RECENT
    parts.append(f"- **{entry.key}**: {entry.content[:200]}")
```

`importance` was accepted by `stm_write`, stored on every entry, and **never
read**. So a fact deliberately saved at 0.9 dropped out of the prompt as soon as
five pieces of trivia followed it — and nothing told the agent it had gone. The
module's own docstring cites Park et al., *"relevance + recency + importance"*;
only recency was implemented.

Fixed: selection ranks by importance with recency as the tie-break (so
equal-importance entries behave exactly as before), truncation says it happened
and names the tool to get the rest, and a count of hidden entries is shown so the
block cannot be mistaken for the whole scratchpad. Silence was the common thread
in all three.

### Still open: memory pressure as a signal

MemGPT tells the model how full its context is *before* truncation, so it can
choose what to save. Prax compacts by size thresholds without telling the agent.
Cheap to build, expensive to validate — it changes behaviour on every long turn,
so it belongs behind a flag and an eval-gate run rather than being switched on
because the paper is persuasive.

## What not to take

**The OS framing as architecture.** "LLM as operating system" is a good teaching
metaphor and a poor design constraint — Prax's governance layer, spoke topology
and graph memory do not map onto processes and page tables, and forcing them
would cost clarity for a slogan.

**Unbounded self-directed eviction.** MemGPT lets the model decide what to
forget. Prax treats memory writes as governed actions; a model that can silently
delete its own history is a model whose audit trail it can edit. Self-editing
working memory should be *additive and visible*, with deletion staying a
governed operation.

---

## Letta — where the authors took it (letta.com)

The MemGPT authors founded **Letta** (Berkeley Sky Computing lab), so the paper
is now the research floor of a product rather than a standalone result. Worth
reading for where they went, not for what to copy — it is a hosted commercial
platform and a direct peer to the TeamWork/Prax shape.

Two of their named concepts land on rows **already in our tracker**, which is
the useful part:

**Context repositories / MemFS — git-tracked memory.** They moved from opaque
"memory blocks" to memory versioned in git. Prax's workspace is *already* a git
repository that commits everything not ignored, and the Library lives inside it.
So Prax has the substrate and does not use it as memory: nothing reads history,
diffs a fact against its previous value, or can answer "when did I start
believing this?". That is a genuinely interesting gap and it costs nothing to
reach — the commits are already there.

**Sleep-time compute** — agents reasoning while idle. Already parked as
"scheduled sleep phase" (💤) from the [lm-sleep](lm-sleep-consolidation.md)
assessment, gated on having a held-out retrieval metric first. Letta shipping it
is evidence the idea is live, not evidence our gate was wrong: building a
memory-maximiser before the metric that would catch it degrading is the mistake
that gate exists to prevent.

**Memory-native RL / "continual learning in token space"** — training memory
models. Same GPU wall as [RLM](rlm-harness-lid.md), [lm-sleep](lm-sleep-consolidation.md)
and [MORPHEUS](skyfall-morpheus-continual-learning.md). Document-don't-adopt, and
by now that verdict is a pattern rather than a judgement call.

**Not** to copy: the hosted stateful-agent server and its SDK surface. Prax's
equivalent is the workspace plus the governed tool layer, and adopting someone
else's agent-state protocol would mean giving up the audit boundary that is the
point of the project.

Honest limit: this is read from marketing pages and partial docs. Licence and
self-host terms were not established, the V1→V2 SDK split is documented mainly
against itself rather than against the paper, and none of it is verified by use.
Treat the two adopt-adjacent rows above as leads, not findings.

---

## Honest limits of this reading

The paper is from **2023**, before long-context models were routine. Several of
its motivating constraints (8k–32k windows) are weaker now, which changes the
cost/benefit of aggressive paging but not the core claim — that an agent should
know the state of its own memory and be able to act on it.

I read the abstract and the architecture description, not the full experimental
section. The evaluation numbers are **not** independently verified here, and the
adopt recommendation does not rest on them: it rests on a gap in our own code
that this paper named clearly.

---

## Related

- [`self-improving-agents-survey.md`](self-improving-agents-survey.md) — flagged
  memory consolidation as Prax's weakest scaffolding cell. This is the concrete
  version of that finding.
- [`coala-assessment.md`](coala-assessment.md) — CoALA's memory axis; MemGPT is
  the mechanism CoALA describes abstractly.
- [`lm-sleep-assessment.md`](lm-sleep-assessment.md) — consolidation at the
  weights level; document-don't-adopt. MemGPT is the scaffolding-level answer to
  the same question, which is why it fares better here.
