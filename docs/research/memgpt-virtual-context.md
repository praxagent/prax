# MemGPT — virtual context management (arXiv 2310.08560)

**Packer, Wooders, Lin, Fang, Patil, Stoica, Gonzalez (UC Berkeley), 2023.**
"MemGPT: Towards LLMs as Operating Systems."

**Verdict: document + adopt ONE mechanism.** Prax independently arrived at most
of this architecture and, in the graph layer, went past it. The single thing
MemGPT has that Prax genuinely lacks is **self-edited working memory that is
always in the prompt** — and the reason that matters is not capacity, it is that
Prax currently has the storage for it and does not put it where it would be
used.

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

## The one real gap

`memory_stm_write` / `memory_stm_read` / `memory_stm_delete` exist. They are the
storage half of MemGPT's working context: small, keyed, self-edited facts.

**They are never injected into the prompt.** The orchestrator reads STM in
exactly one place — to hunt for a timezone string:

```python
stm_entries = stm_read(uid)
for entry in stm_entries:
    if "timezone" in entry.key.lower() or ...
```

So Prax can *write* "the user prefers metric units" and will never see it again
unless it thinks to call `memory_recall` — and an agent does not call recall for
things it does not know it has forgotten. `agent_plan` is auto-injected but
cleared every turn; STM persists but is invisible. Neither is what MemGPT's
working context is: **persistent AND always present.**

That is a smaller change than it sounds — the storage, the tools and the
injection point all exist. It is a wiring gap, not an architecture gap, which is
also why it is worth doing.

### Second, weaker candidate: memory pressure as a signal

MemGPT tells the model how full its context is *before* truncation, so it can
choose what to save. Prax truncates and compacts by size thresholds without ever
telling the agent it is happening. Cheaper to add than it is to evaluate,
though: it changes behaviour on every long turn, so it belongs behind a flag and
an eval-gate run rather than being switched on because the paper is persuasive.

---

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
