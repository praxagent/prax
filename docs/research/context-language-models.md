# Context Language Models (UW / Meta): the model edits its own context

**Verdict: document + adopt the mechanism, rebuilt under Prax's invariants.**
This is the third independent source after [ACM](acm-agentic-context-management.md)
and [OptMem](optmem-append-only-memory.md) saying the same thing: let the agent
decide what stays in its context, rather than a harness ladder. It is the first
of the three with zero-shot results on models Prax already uses, against ACM
itself as a baseline. It moves the queued `context_compact`/`context_recall`
row up the list.

As published, though, it is unsafe for Prax:
- only the system prompt and task are protected;
- roles are whatever the edited headers say, and tool results fold into `user`;
- nothing keeps the original once it is edited away.

The authors name the risk themselves: editable context "can become another
channel through which prompt injections or self-generated instructions persist".
Prax's version keeps their mechanism and adds the invariants below.

Source: [arXiv 2609.37725](https://arxiv.org/abs/2609.37725), *Context Language
Models* (Rulin Shao, Shannon Zejiang Shen, Junjie Oscar Yin, Yuetai Li, Minheng
Wang, Hamish Ivison, Radha Poovendran, Nathan Lambert, Teng Xiao, Mike Lewis,
Wen-tau Yih, Luke Zettlemoyer, Pang Wei Koh; University of Washington, Meta
Superintelligence Labs, MIT, Trillium Labs), 2026-09-29. Paper CC BY 4.0; code
[facebookresearch/context-language-models](https://github.com/facebookresearch/context-language-models)
is **CC BY-NC 4.0 (non-commercial)**, so nothing is vendored into Apache-2.0
Prax. Any implementation is written from the idea, with credit.

## What it is

**Context as a file.** Before each command, the harness writes everything after
the system prompt and the task to `/tmp/.live_ctx/LIVE_CTX_MAIN.txt`, one
`[[CTX_TURN i role=…]]` block per message. The model has one `bash` tool and
compacts by editing that file with `sed` or `python3`. The harness parses the
file back into messages, an edit gate accepts or rejects the change (`fit`: the
result fits the budget; `shrink`: it got smaller), and the model gets a
one-line receipt.

Around it:
- **Budget nudges** at 25%, 50% and 75% of the context budget, plus an adaptive
  urgent nudge every turn when the room left is smaller than about twice the
  recent largest tool output.
- **Rollback, then ask.** Over the limit, the newest turns are rolled back and
  the model is asked to compact, with a pinned note naming the outputs that
  overflowed, instead of truncating silently.
- **Edit-only turns are free** against the step budget (they still count
  against the LM-call cap).
- **Cache-aware advice** in the prompt: an edit forces everything after it to be
  re-read, so batch compactions, and don't compact a small early region above a
  long useful tail.

Beyond zero-shot:
- the compaction instructions are evolved with a skill-optimisation loop;
- online RL trains Qwen3.5-9B;
- "Suffix Cache Reuse" in SGLang cuts serving compute.

## Results (as reported)

| Setting | Result |
|---|---|
| BrowseComp-Plus, zero-shot | **+11.4% accuracy with 21.5% fewer FLOPs** than the best baseline |
| EdgeBench, 12 h | +5% score with 59% fewer FLOPs (best of three seeds per task) |
| 24 h multi-repository agent swarm | 65% greater improvement at the same compute |
| Evolved compaction instructions | up to **+35.9 points held-out** on a context-management task, at lower compute |
| Online RL, Qwen3.5-9B on BrowseComp-Plus | +47.6%, 12% fewer FLOPs |
| Suffix Cache Reuse | 35% less server compute than SGLang at matched performance |

**Baselines:** MEM1, Self-Compact, **ACM**, **RLM**, Codex-style summarisation,
Context Folding, and the Mini-SWE-Agent harness.

**Models:** Qwen3.5-9B, Qwen3.6-27B, GPT-5.4, GPT5.6-Sol, Claude 4.6 Sonnet,
Claude Opus 5, Claude Fable 5.1.

**Limits of the evidence:**
- EdgeBench reports the best of three seeds, and variance is mostly unreported.
- The paper doesn't evaluate deletion errors, invented summaries or edits to the
  task.
- It doesn't say whether the original survives an edit.
- I read the harness code and the paper, and ran nothing.

## Against Prax

| CLM | Prax today |
|---|---|
| The model decides when and what to compact | A harness ladder in `prax/agent/context_manager.py`: `clear_old_tool_results` → `compact_history` (an LLM summary) → `truncate_history`. The agent-decided version is the queued ACM row |
| Nudges at 25/50/75% and an adaptive urgent nudge | None; the ladder fires at overflow |
| Overflow: roll back the newest turns and ask the model to compact | Overflow: compact or truncate without asking |
| Edit-only turns are free | No such turns. The turn limits (#240/#243) would count compaction work like any other call |
| Only the system prompt and task are protected | — |
| Tool results fold into `user`; roles are rewritable | Provenance tainting of untrusted tool results (`loop_middleware`): a page re-read from the workspace keeps its untrusted tag ([provenance laundering](../security/provenance-laundering.md)) |
| The edited-away original is not kept | The full record exists outside the context (traces, `conversations.db`), but nothing links a summary back to it |

## Adopt: agent-managed context, with Prax's invariants

Build the queued `context_compact` / `context_recall` row as governed tools,
taking CLM's mechanics and adding what Prax needs. This is not a bash file: the
orchestrator has no shell, and a structured tool is what lets the invariants
be enforced.

1. **The record is never edited.** Compaction replaces a turn's *body in the
   context* with the agent's summary plus a pointer (trace and span id) to the
   original, and `context_recall(id)` returns the original. This is "compact the
   context, never the record" ([PRO-LONG](prolong-programmatic-memory.md),
   [OptMem](optmem-append-only-memory.md)) and "dereference beats search"
   ([TencentDB](tencentdb-agent-memory.md)).
2. **Pinned verbatim:** the system prompt, **every user turn**, and every approval
   or consent record. The user's words are the authority, and no summary
   replaces them.
3. **Roles and provenance are immutable.** A tool turn stays a tool turn, and a
   summary of untrusted content keeps the untrusted tag: provenance follows
   content. The agent writes bodies, never headers. This closes the [laundering](../security/provenance-laundering.md)
   channel the authors name, where injected text becomes a "user" turn or loses
   its taint.
4. **Their mechanics:**
   - budget nudges plus an adaptive urgent nudge;
   - rollback-then-ask before the existing ladder, which stays as the fallback;
   - a `shrink` / `fit` gate;
   - compaction turns not counted by the spoke/turn limits, but counted by
     the cost and time budgets;
   - the cache-aware batching advice in the tool description.
5. **Flag-gated, eval-gated.** Behind a default-off flag, with ACM's
   pre-registered kill condition: it dies if it doesn't cut peak tokens on
   long-horizon goldens at an unchanged private pass rate.

## Bank

- **Evolved compaction instructions** (+35.9 held-out): a cheap, well-bounded
  first target for the #29 loop. The artifact is one tool description, and the
  fitness is pass rate at a token budget, scored on a held-out split.
- **ATIF-CTX:** their trajectory format records each intermediate context
  snapshot. Prax's traces record spans, not what the model actually saw at each
  call. Worth adding beside the wire record if compaction ships, since "what was
  in context when it decided" becomes the question.

## Don't adopt

- **Online RL and Suffix Cache Reuse.** Weights and serving: the GPU wall, its
  eighth sighting.
- **Unrestricted editing** of the whole transcript, and a bash file as the
  interface. That is fine for a benchmark harness, and the opposite of
  Prax's trust model.
