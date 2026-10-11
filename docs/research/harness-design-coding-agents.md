# An Empirical Study of Harness Design for Coding Agents (UMass Amherst / Emory / Zoom)

**Verdict: document + adopt one thing; mostly validation.**
- **Validation.** Prax already uses this paper's best context policy:
  - rule-based elision of old tool results first;
  - LLM summarization only past a budget;
  - no recall tool. The paper finds recall adds machinery that models
    almost never use, for no accuracy gain.
- **The adopt: in-loop context management.** Prax applies that policy once,
  at the start of a turn. Inside the turn, tool results accumulate unmanaged.
  When the model rejects an over-long payload, Prax compacts the turn's
  *starting* messages and re-runs the whole turn. That repeats the work, and
  cannot help when the turn's own tool results caused the overflow. The paper
  shows that preventing overflow is where context management earns its keep.
  The fix is to run the same staged policy before each model call. It is
  queued, gated on how often overflow actually happens.
- **Demotion.** The paper's matched comparison demotes the `context_recall`
  half of the queued agent-managed-context adopt.
- **Bank.** Planning and the action space are model-dependent knobs.

Source: Fan, Zhang, Ma, Hu, Wang, Song, Liu, Zamani, Wang, "An Empirical Study
of Harness Design for Coding Agents",
[arXiv 2609.20804](https://arxiv.org/abs/2609.20804) (September 2026). The
work was done partly during internships at Zoom.

## What it is

The authors built a lightweight coding harness on LangGraph, running a ReAct
loop. They held the loop fixed and varied three components:
- **context management**: five tiers over two context-window budgets
  (§2.3, Table 2);
- **planning**: a persistent plan the model maintains with `update_plan`. It
  is injected into the model's input each turn, not stored in history (§2.1);
- **the action space**: eight predefined tools, including `bash`, versus a
  bash-only interface (§2.2).

Supporting components were fixed across every run:
- a workspace path guard;
- a read-before-write check with content hashes;
- an allow/ask/deny permission layer;
- post-edit diagnostics (ruff or pyflakes after each Python edit);
- stuck detection: a reminder after 5 identical calls, termination after 8
  identical failing calls.

The context tiers compose three mechanisms:

| Tier | Elision (M1) | Recall (M2) | Summarization (M3) |
|---|---|---|---|
| T0 | | | |
| T1 | ✓ | | |
| T2 | ✓ | ✓ | |
| T3 | | | ✓ |
| T4 | ✓ (at the soft threshold, 0.6 of the window) | ✓ | ✓ (at the hard threshold, 0.85) |

- **Elision** replaces a stale tool observation in the middle of the history
  with a stub.
- **Recall** stores the original and exposes a `recall_event(id)` tool.
- **Summarization** folds the oldest middle events into a running summary,
  using a separate tool-free call to the same model.
- In every tier, the system prompt, the task, and a recent window of at least
  two turns stay verbatim.

**The design space:** four models (Nemotron-3 30B, 120B and 550B;
Mistral-Medium-3.5-128B), served locally at temperature 0. Two benchmarks
(SWE-Bench Verified, 500 tasks; Terminal-Bench 2.1, 89 tasks). T0–T4 at
32k/64k/96k/128k windows, plus planning-off and bash-only ablations at
T4/128k: 176 settings in all.

**Statistics:** paired exact McNemar tests with Benjamini–Hochberg control at
q < 0.05.

## Results (as reported)

- **Context management matters as the window tightens, and mostly by
  preventing overflow.**
  - The managed−unmanaged success gap, averaged over models, falls from 35.7
    to 15.9, 5.5 and 2.7 points on SWE-Bench across 32k→128k. On
    Terminal-Bench it falls from 9.5 to 7.5, 4.8 and 2.8 (Tables 3–4).
  - The gap tracks T0's overflow rate, which falls from 78.7% to 8.7% on
    SWE-Bench and from 61.0% to 12.1% on Terminal-Bench. No managed tier ever
    overflows (Fig. 3).
  - Managed trajectories run longer "without substantially altering agent
    behavior" (§4).
- **Elide first, summarize second, is cheapest at equal accuracy.** T4
  matches T1–T3 on success and has the lowest mean cost at every budget
  (Fig. 4–5): cheap early elision avoids most of the LLM summarization calls.
- **Recall is rarely used and buys nothing.**
  - T2 against T1 (identical except for recall): T2 wins 15, loses 14 and
    ties 3 of 32 matched comparisons; the mean difference is −0.36 points.
  - 36 of the 64 settings that offer recall never call it. Use falls from
    0.54 calls per task at 32k to 0.007 at 128k, and the strong models almost
    never call it (§3.2, Table 13).
- **Planning helps weak models and saves cost for strong ones.**
  - **Nemotron-3 30B:** planning keeps runs alive long enough to attempt an
    edit. Without it, 68.6% of runs end with no edit and the median run is 5
    turns, against 40 with planning (Table 6).
  - **Stronger models:** success barely changes, and planning cuts the
    median trajectory, mostly by trimming redundant post-edit verification:
    108→74 turns for 550B, 68→53 for Mistral (Fig. 8, Table 5).
- **The action space depends on the model and the task.**
  - **Weak model:** predefined tools are essential. Bash-only falls 25.2→10.2
    on SWE-Bench, because it keeps emitting tool calls that aren't there.
  - **Strong, bash-capable model (550B):** bash-only is better and cheaper:
    +3.6 points and −53% cost on SWE-Bench; +5.6 points and −30% on
    Terminal-Bench. It uses denser composite commands.
  - **Mistral:** split. The predefined tools win on repository work
    (SWE-Bench); bash-only wins on shell-centric tasks (Fig. 7).

**Limitations the authors state:**
- one implementation of each component;
- the action-space change bundles tool count with prompts, file tracking and
  diagnostics;
- planning and the action space were ablated only at T4/128k;
- one run per setting at temperature 0.

Two more limitations, ours:
- **Model choice.** Every model is open-weight and mid-sized or below. The
  frontier models Prax usually runs are not tested, and the strongest model
  here already shows the strong-model pattern.
- **Coding only.** Both benchmarks are coding tasks.

## Against Prax

| The paper's component | Prax |
|---|---|
| Context management (T4 minus recall) | `prax/agent/context_manager.py: prepare_context`: old tool results become stubs (`clear_old_tool_results`, all but the last six); `compact_history` LLM-summarizes only when still over budget; `truncate_history` is the last resort. **The same staged shape, but applied once, when the turn starts.** |
| Context inside a turn | **None.** Tool results accumulate across the ReAct loop. On a provider's `ContextOverflowError`, the orchestrator compacts the turn's *starting* messages to 80%, 64% and 51% of the budget and re-runs the turn (`orchestrator.py`, up to three times); spokes do the same (`spokes/_runner.py`). Work done before the overflow is repeated. If the turn's own tool results caused the overflow, a smaller starting history doesn't prevent it recurring. |
| Recall of elided content | No recall tool. The record is kept anyway: trace logs and execution graphs in `RECORDS_DIR`, searchable with `trace_search` / `conversation_search`. |
| Planning | `agent_plan`: private, injected into the system prompt each turn, not kept in history. The paper's design exactly. |
| Action space | The orchestrator holds about 45 tools; the sandbox spoke has `sandbox_shell`. No bash-only profile. |
| Stuck detection | More than the paper's: a hub loop detector in governance, `SteadyingCounsel` (detects a spiral and injects a regroup), `IdempotentToolCache`, and the "two failures means pivot" rule. |
| Read-before-write, workspace guard, post-edit diagnostics | The epistemic read-before-write gate, `safe_join` containment, and syntax-checked `workspace_save`/`workspace_patch`. Files the agent edits with sandbox shell commands get no post-edit diagnostics. |

## Adopt

**Run the staged context policy inside the loop, before each model call.**
- **Where:** a middleware on the `build_agent_loop` seam
  (`loop_middleware.py`, `wrap_model_call`), so it covers the orchestrator and
  every spoke.
- **What it does:** keep the preamble and a recent window verbatim. Past a
  soft threshold, stub bulky tool results in the middle region. Past a hard
  threshold, summarize the oldest middle events. That is
  `context_manager`'s own two steps, at the paper's 0.6 / 0.85 of the
  window, per call instead of per turn.
- **What it replaces:** the reactive compact-and-re-run becomes a backstop
  rather than the mechanism. No recall tool.
- **Gate it on evidence.** Before building, count `CONTEXT_OVERFLOW` events
  in production's health telemetry: the orchestrator, by spoke, and how many
  turns failed after three retries.
  - The paper's gap at 128k windows is about 3 points, and Prax's
    orchestrator models have windows that size or larger.
  - The low tier's small windows and the long browser and sandbox spokes are
    where overflow is likely.
  - Ship behind a flag, default off, and A/B it with the eval gate.

## Re-rank: `context_recall`

The queued agent-managed-context adopt ([Context Language
Models](context-language-models.md), [ACM](acm-agentic-context-management.md))
pairs `context_compact` with `context_recall`. This paper is the first matched
evidence on recall:
- it is almost never used by capable models;
- it gives no accuracy gain over elision alone.

Its experiment is harness-driven elision, not agent-initiated compaction, so
it does not test `context_compact` itself. It does say that the recall tool,
built for accuracy, would probably sit unused.

**Keep "never destroy the record" as an invariant.** Prax already keeps the
record outside the context. If `context_compact` is built, build it without
`context_recall`, and measure whether it is ever missed, before adding the
tool.

## Bank

- **Planning as a per-model knob.**
  - For strong models, `agent_plan`'s value is stopping earlier (less
    redundant verification), not accuracy.
  - For low-tier models running long spoke tasks, a mandatory first-turn plan
    (the paper's reminder) may keep runs from collapsing early.
  - Worth one eval pair before changing anything.
- **A bash-lean profile for the sandbox spoke** on strong models, for
  shell-centric work: fewer, denser calls, at roughly half the cost in the
  paper. The authors' own caveat applies: repository-level tasks still
  benefit from read, search and edit tools, so this is a per-task-type choice.
- **Post-edit diagnostics for edits made in the sandbox.** Files written
  through `sandbox_shell` don't get the syntax check that `workspace_save`
  applies.

## Don't adopt

- **The paper's harness itself.** It is a research instrument, and Prax
  already has every one of its fixed components.
- **A recall tool, for accuracy.** See above.

## Related

- [Crush (Charm)](crush-charm-coding-agent.md) records the SWE-bench Pro
  harness swap (changing the harness moves pass@1 more than changing the
  model), and [No universally superior
  harness](harness-generalization.md) finds no harness wins everywhere. This
  paper says *which component* matters for *which model and budget*.
- [Harness-delta attribution](harness-delta-attribution.md): how much of a
  claimed harness improvement is real. A reason to A/B the adopt above rather
  than trust a gain.
- [PRO-LONG programmatic memory](prolong-programmatic-memory.md): compact the
  context, never the record. That is the invariant this note keeps.
