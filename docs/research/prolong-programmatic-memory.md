# PRO-LONG — programmatic memory for long-horizon agents

**Source:** [alexisfox7/PRO-LONG](https://github.com/alexisfox7/PRO-LONG) ·
[arXiv:2607.20064](https://arxiv.org/abs/2607.20064) (Fox, Wang, Rosu, Dhingra —
Duke; submitted 2026-07-22, revised 07-23)
**Repo state at assessment (2026-08-06):** Python, 249 stars, 30 forks, 24
commits, created 2026-03-07, pushed the day of this assessment, **no LICENSE
file** (see *Declines*).
**Assessed:** 2026-08-06

**Verdict: document + adopt THREE. This is the most directly actionable paper of
the ARC-AGI-3 cluster** — more so than [Prime Agent](prime-agent.md) or
[NOOA](nooa-object-oriented-agents.md), because its entire method is *a file plus
grep*, and Prax already owns every piece it needs. It also **sharpens a row we
banked from [ACM](acm-agentic-context-management.md)** by drawing a line we had
not drawn: *compact the context, never the record.*

---

## The tradeoff they name, and the way out

The abstract states the problem better than we had:

> "Existing methods for context management face a significant tradeoff, as
> preserving more information makes retrieving relevant details less tractable."

Every context scheme we have assessed resolves that tradeoff by **throwing
information away** — [ACM](acm-agentic-context-management.md) compacts,
[MemGPT](memgpt-virtual-context.md) evicts to tiers, our own `progress_service`
folds old bullets into an Archive paragraph. All of them trade recall for
tractability.

PRO-LONG refuses the trade. It keeps a **complete, structured interaction log**
and makes retrieval *programmatic*: the harness appends every observation,
action and outcome to `logs.txt`, and the agent searches it with Grep/Bash/Python
rather than reading it into context. The log is never summarised, never
truncated, and never loaded. What made this newly viable is stated plainly —
they are "capitalizing on recent progress in coding agents to search this
history efficiently."

That is the important idea: **as coding ability improves, the searchable-log
answer gets better, while the summarise-and-evict answer gets no better at all.**
One scales with the model; the other fights it.

The method is genuinely ~30 lines. From `prompts.py`, the load-bearing sentence
is:

> "`/workspace/logs.txt` is the game log: action headers, tool calls, board
> states, and your own prior analyses. Parse it **programmatically**, as reading
> full 64x64 board states from prompt can introduce precision errors."

Plus a persistent `/workspace/` ("`actions.json` is cleared each call; other
files accumulate"), a small set of grep-able log markers
(`[INITIAL BOARD STATE]`, `[POST-ACTION BOARD STATE]`, `[frame 1/N]`,
`[settled]`), and Read/Write/Edit/Bash/Grep/Glob. No subagents, no vector store,
no retrieval model.

## Numbers — and which one to believe

| claim | value |
|---|---|
| improvement over the **same** coding agent without the log | **+18.0 pp** avg across frontier models |
| vs. state-of-the-art specialized harnesses | matches or exceeds (their cited SOTA: **up to 76.1% pass@1**) |
| token cost vs. those harnesses | **4.2–5.8× fewer** |
| best@2 with Fable 5 | **97.4%**, total cost **$1,750** |

**The +18.0 pp is the number to believe**, and it is the only one that is a
controlled comparison: same agent, same games, log on versus log off. A
leaderboard position confounds model, harness and budget; this ablation does not.

**The 97.4% needs care, per the standing [Prime Agent](prime-agent.md) lesson
(check which model, and check which metric).** It is **best@2 with Fable 5**.
Prime Agent reported **95.5% best@1** with Opus 5 (99.97% best@3);
[NOOA](nooa-object-oriented-agents.md) 85.1%; [OpenAI's harness
post](openai-arc3-harness-settings.md) 38.3%. best@2 is a *weaker* claim than
best@1 — two attempts, take the better — so **97.4% best@2 does not rank above
95.5% best@1**, and this table is not a ranking. Different models, different
metrics, one shared benchmark.

Provenance is better than most: `scorecards/` contains the official online
scorecards for all 25 Fable 5 runs, individually verifiable on arcprize.org, and
`release_logs/` carries game logs, agent transcripts and workspaces.
[Saturation](benchmark-saturation.md) names ARC-AGI-3 as still discriminating, so
this is not a saturated-target win.

## Adopt 1 — "compact the context, never the record"

We banked agent-initiated compaction from ACM. PRO-LONG is the counter-condition,
and their ablation table is designed to separate exactly this:

| condition | flags | history available |
|---|---|---|
| prolong | (default) | full game log |
| lw25 | `--log-window 25` | last 25 action sections |
| no-log | `--log-window -1` | none; current board in the prompt |
| stateless | `--workspace stateless` | full log, workspace wiped each call |

`lw25` **is** the compaction condition, and the full log beats it.

These findings are not in conflict, and the reconciliation is the rule worth
keeping: **compaction is right when the retriever is the model reading its own
context, and unnecessary when the retriever is code searching a file.** So
compact the *context window*; never compact the *record*.

Prax's `progress_service` already has the right bones — and its docstring says
so: `.progress/YYYY-MM-DD-{id}.md` detail files are written per session and
"never re-read during compaction — the summary loop only re-summarises text."
The complete record survives; only the index is folded.

**The gap this lens exposes is precise and small.** The detail files are
preserved but **only addressable by date**: `progress_detail(space_slug, date)`
(`workspace_tools.py:1014`) requires you to already know when the thing you are
looking for happened. There is no grep over `.progress/` — verified, nothing
under `prax/agent/` searches it. So Prax keeps the complete record and then makes
it retrievable only by a key the agent usually does not have. That is the
"preserving more makes retrieval less tractable" tradeoff, landed on the wrong
side, in code we already shipped.

The fix is a search tool over the space's `.progress/` directory, not more
compaction.

## Adopt 2 — a programmatic log for Prax's own long-horizon turns

Prax already has every ingredient PRO-LONG needs: a **persistent git-backed
workspace** per user, `workspace_save`/`workspace_patch`/read, `run_python`, and
the sandbox to run it in. What it does not have is the harness half — **an
append-only structured log that the harness writes and the agent greps.**

Today a long orchestrator turn carries its history in the context window and in
`agent_plan.yaml` (ephemeral, cleared at end of turn). Nothing durable records
"what I observed, what I did, what happened" in a form code can search.

This is worth noting against [Prime Agent](prime-agent.md), where we recorded a
persistent-REPL gap: **PRO-LONG does not need one.** Their workspace persists
between calls but each call is a fresh agent invocation with Bash — which is
exactly `run_python` + the workspace, the shape Prax already has. Of the two
ARC-AGI-3 systems, this is the one buildable now.

Scoped honestly: this is a real feature with a real cost (log format, rotation,
size bounds, what "the harness" means for a multi-channel assistant that is not
running a game loop), and it should be flag-gated and eval-gated like everything
else. It is a candidate, not a decision.

## Adopt 3 — the four-condition ablation as our memory eval design

Their conditions isolate *which property of memory pays*: completeness (prolong
vs lw25), existence (vs no-log), and durability of the scratch space (vs
stateless).

**We have never run that experiment on Prax's memory stack.** Qdrant vectors,
Neo4j graph, `progress_*`, `memory_stm_*`, `trace_search` — we have flags for
much of it and a flag campaign that A/B'd them one at a time against a fixed
baseline, but we have never asked the prior question: *does the complete record
beat a window, and does any of it beat none?* [MemGPT](memgpt-virtual-context.md)
already flagged that `memory_stm_*` is persistent-but-never-injected. A
four-condition ablation would tell us which cells are load-bearing before we
invest further in the one every assessment names as our weakest.

## Declines and cautions

- **No LICENSE file** — the GitHub API reports `license: null`, i.e. all rights
  reserved. **Do not vendor, copy, or adapt their code.** Same posture as
  [cdc-lean](cdc-lean-teach-prax-lean.md): read the paper, reimplement the idea. The
  idea is ~30 lines of prompt discipline and a log format, so this costs us
  almost nothing.
- **Half the repo is ARC-AGI-3-specific** (`environment/arcagi3.py`, board
  formatters, action queue, the 64×64 colour maps). The transferable part is the
  prompt + the log markers + the workspace contract.
- **Their agent is someone else's.** Backends are Codex CLI and Claude Code —
  they built the *harness* around an existing coding agent. In our stack Prax is
  the coding agent, so adopting means building the harness half, and the
  comparison "PRO-LONG vs Prax" is a category error.
- **One lab, arXiv-only, 24 commits, no venue**, and the paper page surfaced no
  limitations section in the fetched content — so their own caveats are unread
  here. Treat as promising and well-evidenced on the one controlled number, not
  as replicated.
- **Their security posture is stronger than most and worth noting rather than
  declining**: "The agent container only mounts the game workspace and, by
  default, has no network access except a proxy to the model API." That is
  prax-sandbox plus the secrets proxy, arrived at independently.
