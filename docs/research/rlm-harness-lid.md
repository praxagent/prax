# RLMs & the "Locally-In-Distribution" harness principle (Zhang)

*Assessment of [alexzhang13.github.io/blog/2026/harness](https://alexzhang13.github.io/blog/2026/harness/)
(2026-07-20). A research argument that the **harness**, not the network, should be
the locus of compositional generalization — via "Recursive Language Models" (RLMs)
trained with RL.*

**Verdict: document-don't-adopt the training core (weights-level RL on an
open model — incompatible with a hosted-LLM harness), but bank the architectural
principles. The sharpest one — programmatic, code-REPL tool/sub-agent calling with
context offloaded to variables — is a genuine future direction for Prax's
orchestration, newly plausible now that `run_python` exists. The LID lens also
independently validates several things Prax already does.**

## The thesis

Standard agents (Claude Code, Codex) "flood the context window with interleaved
task-specific information," pushing each Transformer call out-of-distribution
("context rot"). Zhang's fix — the **Locally-In-Distribution (LID) principle**:
design the harness so that *each individual model call sees a prompt that is
in-distribution with its training*, even when the whole task is OOD. A good
harness "induces an equivalence relation between tasks with latent similarities,"
so structurally-similar tasks produce nearly the same token-level trajectory in
the root model's context. Two mechanisms:

1. **Context offloading** — pass input-specific context as a *symbolic variable*
   the root call never directly sees, so different problems look the same at
   step 1.
2. **Programmatic sub-agent/tool calling** — treat sub-agents *and* tools as
   functions in a **code REPL**; results flow through variables, not into the
   main context. Called "equally as important as context offloading."

RLMs are the trained realization: a root model recurses into sub-calls/tools
programmatically, keeping its own context abstract.

## What's real vs. what's oversold

- **Real (architectural):** the LID lens and the two mechanisms are sound and
  match established practice — this is essentially the **CodeAct / code-as-action**
  paradigm plus disciplined context isolation. The convergence with the OpenCode
  critique (`opencode-critique-eval.md`), which *independently* damns
  "flooding the context," is worth noting: two unrelated sources landing on
  "don't flood the context" strengthens the case for context discipline.
- **Oversold for us (training):** the headline numbers — "~10x eval lift,"
  trained Qwen3-30B "approaches or exceeds GPT-5.5 on MRCRv2," cross-domain
  strategy transfer — come from **RL-training an RLM**, not from a prompting
  technique. They're single-source and impressive, but **Prax cannot get them
  without the RL infra + an open-weight model** — the same wall as ARTS
  (`arts-agentic-tree-search.md`) and the SEAL/Sleep training lanes: weights-level,
  GPU-gated, parked until the finetune lane opens. The *architecture* is
  adoptable without the training; the *numbers* are not.

## What Prax already does that LID validates

- **Spoke summarization = context offloading (partial).** `delegate_*` returns a
  *summary* to the orchestrator, not the sub-agent's full trajectory — the root
  context stays abstract. This is LID's mechanism 1, done via text rather than a
  REPL variable.
- **Prompt selectivity** (`PROMPT_SELECTIVITY_ENABLED`) trims the system prompt to
  the relevant sections — a nod to keeping each call in-distribution. (Caveat from
  the caching eval: it also varies the cacheable prefix per turn — a real
  tradeoff, separate from LID.)
- **Context budgets / compaction** (`context_manager`) directly fight context rot.
- The **autonomy taxonomy** already in the research README (L0–L3;
  "deterministic backbone + proven units + LLM gap-filler") is the same instinct
  from the orchestration side.

## The bankable idea: code-REPL orchestration

The one genuinely *new* lever is mechanism 2 — **the orchestrator writing code
that calls tools/spokes as functions and chains results through variables**,
instead of JSON tool-calls whose full results re-enter its context. Today Prax is
JSON-tool-call shaped; a CodeAct-style path would let a large intermediate result
(a fetched document, a big query result) live in a REPL variable and be *referenced*
without flooding the model's context — exactly LID mechanism 1 done structurally.
This is newly plausible because `run_python` now exists (the execution substrate).

**But gate it hard, and don't overbuild:** this is the L2/L3 end of the autonomy
taxonomy the README already flags as "empirically fragile" in its unbounded form.
The honest next step is *not* to re-architect the orchestrator — it's a **narrow
experiment**: let a single spoke (e.g. a data/research task with big intermediate
artifacts) run in a code-REPL shape and measure, via the trace-grade + capability
suite, whether context-offloading actually lifts long-horizon tasks *for Prax*.
Instrument before committing (the README's standing rule).

## Also worth banking: the "nudge to decompose" failure mode

Zhang's RLMs can *cheat* — "offload the entire problem to a single sub-call,"
collapsing to the long-context baseline — and need a "nudge to decompose." This is
the same shape as Prax's observed variance (delegate-vs-do-it-all; the `run_python`
by-hand-vs-compute swing). It's another data point that *decomposition is a
decision the model won't reliably make on its own* — reinforcing that the harness,
not the prompt, has to carry it (cf. the verify-and-commit A/B, where a prompt
raised verification but not efficiency).

## Bottom line

Don't chase the RLM numbers — they're a training result behind the GPU wall.
**Do** bank the LID lens (it names *why* context offloading + spoke summarization
work) and treat **code-REPL orchestration as a tracked, narrowly-scoped experiment**
rather than a rewrite. The strongest immediate takeaway is cheap and already
half-true in Prax: keep each model call in-distribution by keeping large,
task-specific data *out* of the root context.

---

# Update 2026-08-08 — the blog is now a paper, with numbers that move one verdict

TJ resurfaced this as [arXiv 2512.24601](https://arxiv.org/abs/2512.24601)
(**Recursive Language Models**, Alex L. Zhang · Tim Kraska · Omar Khattab, MIT
CSAIL; v3, 11 May 2026; code at `github.com/alexzhang13/rlm`). Same idea as the
blog this file was written from, now with a results table — which changes the
priority of one banked item and leaves the main verdict intact.

## What an RLM actually is (the mechanism, precisely)

The root model is given a **persistent Python REPL** in which the user prompt
`P` is a *variable*, not context. It sees only constant-size **metadata** about
`P` (length, a short prefix, how to index it), writes code to peek at and
decompose it, and can **invoke the LLM programmatically from inside that code**
— including inside loops, so a run can make Ω(|P|) or Ω(|P|²) sub-calls.
`stdout` is truncated to constant-size metadata before it is appended to
history. The loop ends when the code sets a variable `Final`.

The paper is explicit that a "deceptively similar" scaffold (its Algorithm 2)
fails on three counts, and the third is the interesting one: without **symbolic
recursion**, a scaffold can only delegate a few *explicitly verbalised* tasks
rather than program them.

## Table 1, the part that matters (GPT-5 root, GPT-5-mini sub-calls)

| Method | CodeQA | BrowseComp+ (1K) | OOLONG | OOLONG-Pairs |
|---|---|---|---|---|
| Base GPT-5 | 24.0* | 0.0* | 44.0 | 0.1 |
| Compaction agent | 58.0 | 70.5 | 46.0 | 0.1 |
| OpenCode | 18.0* | 0.0* | 32.0 | 3.1 |
| **OpenCode + context offloading** | **64.0** | 0.0* | **52.0** | 4.8 |
| Claude Code (Opus 4.1) | 12.0* | 0.0* | 40.2 | 0.1 |
| **Claude Code + context offloading** | **62.0** | **84.0** | **48.0** | 6.5 |
| RLM (depth=0) | 58.0 | 88.0 | 36.0 | 43.9 |
| RLM (depth=1) | 62.0 | 91.3 | 56.0 | 58.0 |
| RLM (depth=2) | **66.0** | 92.0 | 56.5 | 65.5 |
| RLM (depth=3) | 58.0 | 92.0 | **58.0** | **76.0** |

`*` marks runs that hit input-context limits — several baseline zeros are
"could not run", not "ran and failed", which flatters the headline deltas.

**Three readings, in order of how much they should change what we do:**

1. **Context offloading alone is most of the win, and it is not RLM.** Adding
   it takes Claude Code from 12.0 → 62.0 on CodeQA and 0.0 → 84.0 on
   BrowseComp+; OpenCode from 18.0 → 64.0. That is a *scaffold* change with no
   recursion, no training, and no new paradigm — and it is the single largest
   intervention in the table.
2. **Depth=0 already beats nearly every baseline** (58.0 / 88.0 / 36.0 / 43.9).
   Depth=0 is an RLM with **no sub-calls at all** — just the prompt as a
   variable in a REPL. So the load-bearing part is *the environment*, not the
   recursion. Cheapest part, most of the benefit.
3. **Deeper is not monotonically better.** CodeQA peaks at depth=2 (66.0) and
   *drops* at depth=3 (58.0); on Qwen3-Coder, OOLONG peaks at depth=1 (48.0)
   and collapses to 26.0 at depth=2. Anyone citing "recursion helps" as a
   monotone law is over-reading the paper.

Cost is genuinely favourable, not just quality: RLM(GPT-5, depth=1) averages
**$0.99** on BrowseComp+ against a linearly-extrapolated **$1.50–$2.75** for
GPT-5-mini simply ingesting the 6–11M input tokens.

## Verdict changes

**Unchanged — don't adopt the training.** RLM-Qwen3-8B (+28.3% median over its
base, from 1,000 filtered trajectories) still needs a fine-tune, and the GPU
wall this file already documents has not moved.

**Promoted — context offloading goes from "banked" to a concrete adopt with
evidence.** This file previously logged it as an idea; Table 1 makes it the
best-evidenced cheap scaffold change we have seen, and
[PRO-LONG](prolong-programmatic-memory.md) argued the same shape from the
memory side ("compact the context, never the record").

**Named blocker, third sighting: Prax has no persistent REPL.** `run_python`
(`prax/agent/workspace_tools.py`) base64s a script through `docker exec` — a
fresh process per call, so no variable survives between calls. Prax therefore
*cannot* hold a large object symbolically and manipulate it across turns, which
is the entire RLM mechanism. [Prime Agent](prime-agent.md) named this gap
first; this is the strongest quantitative case for closing it.

## Why this landed on the right day

While reading this, the `spiral-20260808` campaign measured Prax doing the
exact anti-pattern the paper names. On `honesty_stale_reference` Prax made
**146 tool calls and spent 1,063,079 tokens** without answering; on
`honesty_absent_source_body`, **162 calls / 704,804 tokens**. Two cases, 36% of
the whole 30-case run's token spend, both scoring zero.

Every one of those tool results went **into the context**, which then got
re-sent on the next call — precisely what "truncate stdout to constant-size
metadata and keep the object in a variable" is designed to prevent. The paper
frames this as an expressiveness limit; our measurement shows it is also a
*cost* limit and a *termination* limit. That does not make offloading the fix
for unbounded escalation — an agent that cannot answer will loop cheaply
instead of expensively — but it does mean the two problems share a mechanism,
and #58's flag decision and this adopt should be read together.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Context offloading** — large tool results land in a file/variable; the model sees constant-size metadata plus a pointer, not the payload | 📋 promoted from banked; best-evidenced cheap scaffold change available |
| **Persistent REPL** so an object can be held symbolically across calls (3rd sighting; blocks any RLM-shaped work) | 📋 queued |
| RLM training recipe (RLM-Qwen3-8B) | ❌ declined — GPU wall unchanged |
