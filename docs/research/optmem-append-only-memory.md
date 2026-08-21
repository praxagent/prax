# OptMem — the record is append-only, the tree is a cache, and the agent does the compressing

**Source:** [VictorTaelin/OptMem](https://github.com/VictorTaelin/OptMem) — Victor
Taelin (HVM/Bend). *"Permanent memory for AI agents. A 426-token prompt, a script,
plug and play."* One Python file, **no dependencies, and no LLM call anywhere in
the tool**. 1,304 stars / 86 forks / 9 open issues at time of reading; created
2026-07-25, last pushed 2026-07-31 — **under a month old**. **No LICENSE file.**

**Verdict: document + adopt three mechanisms; decline the substrate.** This is the
most useful thing I have read on agent memory in weeks, and not because it is
sophisticated — because it is a *shipped, running instantiation* of three
principles this project has written down as adopt rows and only partly built.

## How it actually works

- **`LOG.txt` is append-only and never edited.** One memory per line, ≤280 bytes.
- **`TREE/` is a binary merge tree of one-line summaries — and it is explicitly
  a cache, "rebuildable from the log alone".** Block `[lo,hi)` is the compression
  of `[lo,mid)` and `[mid,hi)`.
- **`wake` prints a fixed reading budget** (`WAKE_LINES=96` ≈ 8k tokens). The
  `cover(T, budget)` function tiles the log with aligned power-of-two blocks,
  keeping a block whole *iff its size is at most α times its age* and binary-
  searching α to hit the budget. **Detail decays with age**: recent memories stay
  verbatim, ancient ones collapse. If everything fits, nothing is compressed.
- **The agent writes every summary.** `note` returns a merge request; the agent
  answers `nap <lo>-<hi> "<one line>"` before its next action. The instruction is
  *"Keep what has lasting effect, drop what does not. Invent nothing."*
- **`zoom <a-b>`** opens a node into its two halves, down to raw memories.
  **`recall <regex>`** greps every memory ever recorded.
- **Fixed-width records** (320 B log, 288 B tree) so *position is identity*:
  memory `i` lives at `i*320`, no index file to keep in sync.

## Why this matters to us: three of our own rows, already running

**1. "Compact the context, never the record."** That is the sharpening we took
from [PRO-LONG](prolong-programmatic-memory.md). OptMem is that sentence as an
architecture: the log is immutable, the summaries are a derived cache, and a bad
summary is fixed by `forget` + rebuild rather than by surgery. **Second sighting,
first with running code.**

Prax does not have this property. `supersede_relation` mutates the graph;
consolidation rewrites. There is no log to rebuild from, so a bad consolidation is
permanent — which is the same hole the [Chroma](chroma-foundation.md) comparison
found under a different name ("versioning & diffs", which we lack).

**2. Dereference beats search — in a form that cannot decay.** The
[TencentDB](tencentdb-agent-memory.md) invariant was that an abstraction keeps a
deterministic pointer to its evidence, and the defect it exposed here was that
progress compaction *destroyed session ids*, fixed by re-attaching them in code
after the model wrote its summary.

OptMem makes that failure **unrepresentable**. The pointer is not carried in the
text at all — it is *arithmetic*. Block `#0-7` decomposes into `#0-3` and `#4-7`
by definition, so no rewrite can lose it. That is a strictly stronger version of
the rule we adopted, and the generalisable form is: **derive the pointer from
position rather than storing it in the summary.**

**3. It corrects our agent-initiated-compaction row.** The
[ACM](acm-agentic-context-management.md) row says the *agent* decides when to
compact. OptMem splits it differently: **the tool decides WHEN** (merges come due
by tree arithmetic — deterministic, unmissable), **the agent decides WHAT** (it
writes the line). That split is better on both halves. "When" is a scheduling
question an LLM judges badly and a counter judges perfectly; "what" genuinely
needs the model. Our row conflated them.

## What it buys that Prax does not have

**A bounded, mandatory, single entry point to memory.** `wake` runs first, always,
and prints exactly `WAKE_LINES` at any scale. Prax has no equivalent guarantee —
and the [MemGPT audit](memgpt-virtual-context.md) already found `memory_stm_*` is
*persistent but invisible*, never injected into the prompt at all. OptMem's answer
is blunt and effective: one entry point, mandatory, first, budget-bounded, with
detail decaying by age.

**No LLM in the tool.** Prax's consolidation needs a model; if it is down, slow, or
having a bad day, memory quality degrades quietly. OptMem cannot fail that way,
because the only intelligence involved is the agent already in the loop.

## Where Prax is genuinely ahead, and it is not close

A memory in OptMem is **one line of ≤280 bytes with no schema** — no entities, no
relations, no provenance, no validity time. It answers *"what happened, roughly,
ordered by recency"*. It cannot answer *"where did TJ live in April"*, which is
exactly what [bitemporal validity](semantica-knowledge-substrate.md) shipped for last week.

Two harder objections:

- **No provenance.** A memory learned from a web page and a memory taught by the
  user are the same line. That is precisely the laundering defect fixed on
  2026-08-07 (untrusted content read back as the user's private data). Adopting
  OptMem's substrate would reintroduce it.
- **"Do not register redundant memories" is a prompt instruction.** So is the
  subagent rule (*"it must never run memo"*), which is the concurrency story for a
  shared append-only file: a prompt-level lock. That is **a guard in the wrong
  layer** — the pattern that named every real defect we found this month, and the
  same shape as [#64](../IDEAS_BACKLOG.md).

**Regex-only recall** is a deliberate choice (*"word for word"*), and it means a
memory you cannot spell is a memory you cannot find. Prax has semantic search over
both vectors and the graph.

## Honest limits of the claims

**There is no evaluation. None.** No benchmark, no ablation, no comparison — 1.3k
stars is popularity, not evidence. Given the
[benchmark-saturation](benchmark-saturation.md) and
[audit-the-checker](eval-scorer-audit-2026-08-07.md) work, that is the first thing
to say about it.

**"Opt" is not established.** `cover()` is a binary search on a heuristic
parameter, not an optimisation with a proof. Nothing in the repo argues the tiling
is optimal in any stated sense.

**The headline number measures the cheap axis.** *"At a million memories (608 MB),
`wake` takes 0.03s"* is **read latency** — which was never the bottleneck. The cost
of an agent memory system is tokens and LLM calls, and 0.03s says nothing about
either. Same class as Semantica's "6000×" turning out to be cache-hit vs cold-load:
an impressive number on the axis nobody was struggling with.

**No LICENSE.** Unlicensed means all-rights-reserved by default, so this **cannot
be vendored** — the same call as [cdc-lean](cdc-lean-teach-prax-lean.md). The ideas are
free to learn from; the file is not free to copy.

**Under a month old**, 9 open issues, one author. Not a stable dependency.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Age-decaying reading budget for what Prax injects at session start** — a fixed line budget, recent detail verbatim, older collapsed, one mandatory entry point | 📋 queued — closes the [MemGPT](memgpt-virtual-context.md) gap (`memory_stm_*` is persistent-but-invisible) |
| **Split compaction WHEN/WHAT: the tool schedules the merge, the agent writes it** | 📋 queued — **corrects the [ACM](acm-agentic-context-management.md) row**, which gave the agent both |
| **Derive the pointer from POSITION, not from the summary text** — a rewrite cannot destroy what it does not carry | 📋 queued — 2nd sighting of [dereference-beats-search](tencentdb-agent-memory.md), in a strictly stronger form |
| **An append-only record with a rebuildable derived layer** | 📋 queued — 2nd sighting of ["compact the context, never the record"](prolong-programmatic-memory.md), first with running code; also the [Chroma](chroma-foundation.md) versioning gap under another name |
| The OptMem substrate — 280-byte schemaless lines, regex-only recall, no provenance, no validity time | ❌ declined — would discard bitemporal validity (#65), provenance (the 2026-08-07 laundering fix), and semantic search |
| Vendoring the `memo` script | ❌ declined — **no LICENSE**, so all-rights-reserved; learn from it, do not copy it |
| Prompt-level dedup + the subagent "never run memo" rule | ❌ declined — a guard in the wrong layer, and the same shape as #64 |
