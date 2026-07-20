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
