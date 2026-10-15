# MASS: Recursive Self-Improvement through Multi-Agent Self-Supervision

**Verdict: document + adopt two small things for the spokes (built in #279)
and a replicated keep decision for #29 (built in #278); bank three lessons;
don't adopt the method.**
- **What it is.** One model plays agent, judge and optimizer. It evolves a
  multi-agent *workflow prompt* for each task, then fine-tunes itself on the
  trajectories its best workflows produce, and repeats.
- **Read the appendix, not the abstract.** The appendix is unusually candid,
  and it shrinks the result:
  - When the base model is simply *told* to organize a team, the trained
    model's edge falls to 53.5%, and below 50% on the shared training tasks.
  - The public research benchmarks improved because more runs finished. The
    average quality of finished work fell.
  - The two verifiable coding benchmarks went down.
  - This is the strongest evidence yet for a point we keep relearning: the
    harness carried what the weights were credited with.
- **Adopt (built):**
  - **Sources travel up** (#279, `SPOKE_SOURCES_ENABLED`). A spoke's reply carries the URLs it actually
    fetched, appended by the harness, not written by the model. Today a spoke
    can read a page and drop its link, which is the main false-flag case for
    read-before-cite (#279).
  - **Record spoke conversations as training data** (#279,
    `FULL_TRAJECTORIES_ENABLED`). Prax exports
    orchestrator-level trajectories only. In MASS's recipe, sub-agent windows
    are 2,054 of 2,435 training windows.
- **Bank:**
  - **One run per candidate is not a verdict.** The same workflow, prompt and
    seed won 6/6 judgments on one run and lost 6/6 on the next. Single-run
    verdicts flipped 22–40% of the time. #278's keep decision runs once.
  - **Report delivery separately from quality.**
  - **Optimizer capacity, not evaluator capacity, set the speed** of
    workflow search.

Source: Lee, Xu, Seely, Lee, Sojoudi, Zaharia, Tang, "Recursive
Self-Improvement through Multi-Agent Self-Supervision",
[arXiv 2610.12176](https://arxiv.org/abs/2610.12176) (October 2026). UC Berkeley
and Sakana AI.

## What it is

- **The setting:** "homogeneous" RSI on open-ended research tasks. One model
  (Qwen3.6-27B, running in the `qwen-code` agent) is the task solver, the
  workflow optimizer and the evaluator. On these tasks nothing better is
  available to judge.
- **The inner loop** (evolution with an elite of one):
  - A **workflow** is text appended to the task prompt. It names the
    sub-agents (role, instructions, an output **contract**: what each returns
    to the orchestrator) and the order of calls (**hops**).
  - The model executes the workflow, compares the result with the best so
    far, and revises the workflow from its own pairwise feedback. The
    optimizer is told to prioritise contracts and hops.
- **The outer loop:**
  - Run each task's best workflow 18 times, and rank the outputs with the
    model's own pairwise tournament (Bradley–Terry).
  - Fine-tune a LoRA (rank 64) on the top trajectories, with the workflow
    removed from the prompt so the behaviour is internalized.
  - The trained model takes all three roles in the next cycle. Two cycles
    were run.
- **The data:**
  - 12 synthetic research tasks written by GPT-5.5 (finance, robotics,
    pharmacy), 9 for training and 3 for testing. One training task found no
    winning workflow.
  - 119 trajectories → 2,435 windows (282 orchestrator, 2,054 sub-agent),
    17.2M supervised tokens.
- **The judges:** GPT-5.5 and Claude Opus 4.8 grade only the reported
  results, three seeds each, so six judgments per pair. These are "repeated
  assessments of one pair of outputs, not six independent task executions."

## Results (as reported)

- **The three test tasks:**
  - the second cycle beats the base model 69.9% of the time;
  - the first cycle's 53.9% is 48.3%, 45.0% and 68.3% per task, so it loses
    two of the three.
- **The self-evaluator's agreement with the external judges** rose 0.73 →
  0.91 → 0.93, although training used only task-solving traces.
- **Public benchmarks,** base → second cycle (Table 7):
  - ScienceAgentBench: 28.8 → 31.7% (±1.5 / ±2.0 over three trials);
  - MLR-Bench: 1.58 → 2.40;
  - AstaBench Hard: 0.053 → 0.067 (±0.017);
  - DSBench: 0.473 → 0.482 (±0.03);
  - **Terminal-Bench 2.0: 37.5 → 35.2%;**
  - **SWE-bench Verified: 62.6 → 61.1%.**

  The abstract's "1.2–1.6× per output token" covers the four research
  benchmarks only, and part of it is fewer tokens.
- **Multi-agent traces train better than single-agent ones.** A student
  trained on multi-agent traces wins 68.3% against the base model, using 28.8M
  tokens. A single-agent student trained on 42M tokens wins 64.0%.
  Checkpoints for these controls were chosen with the external judges, which
  the paper discloses.
- **Coordination is internalized.** Without the workflow in the prompt,
  delegation rises from 1/110 trajectories (base) to 87/110 (second cycle).
- **The bottleneck is the optimizer.** Replacing the optimizer with GPT-5.5
  finds winning workflows much sooner than replacing only the evaluator. The
  all-Qwen loop still gets there on 11 of 12 tasks.

## What the appendix says

The authors report all of the following themselves.
- **A prompt gets most of the effect** (C.4).
  - Base and trained model both receive the same "choose three to six
    specialist roles" directive. The trained model then wins 53.5%, and
    47.1% on the four shared training tasks.
  - Their own reading: "The observed change is increased bare-prompt use of
    coordination already available in the base model."
  - And against the workflow-prompted teachers it learned from, the trained
    model wins only 17.3%.
- **More finished work, not better work** (C.5).
  - MLR-Bench papers delivered: 31, 36, 50 of 60 across the generations. The
    mean score of delivered papers was 3.07, 3.00, 2.88.
  - AstaBench reports delivered: 60, 69, 91 of 120. Their mean scores were
    0.105, 0.095, 0.088.
  - Per-task sign tests on MLR-Bench give p = 0.23. "The evidence for
    improvement therefore depends on the aggregation unit."
  - And "None of the 180 MLR-Bench executions calls the delegation tool", so
    the claimed mechanism is not what moved that benchmark.
- **Verdicts from single runs are noise-dominated** (E.2–E.3).
  - The same workflow, prompt and seed completed and won 6–0 against the
    same reference on one run. On the next it hit a loop halt and lost 0–6.
  - Across search steps, a near-unanimous verdict reversed 22–40% of the
    time in every edit-size bin. Edit size predicts nothing (|ρ| ≤ 0.12).

**Two limits of our own:**
- **Every training task was written by one model (GPT-5.5),** and three test
  tasks carry the headline win rate.
- **"Non-verifiable" is doing a lot of work.** On the two benchmarks with
  executable checks, the trained model got slightly worse.

## Against Prax

| MASS | Prax |
|---|---|
| A workflow prompt: roles, instructions, output contracts, hops | A fixed hub and spokes. The orchestrator writes a free-text task to `delegate_<spoke>`; the spoke returns free text |
| The optimizer moves task-specific information into **contracts** (what a sub-agent returns) and **hops** | Spokes return a final message. `_append_preserved_tool_results` (in `spokes/_runner.py`) already appends evidence blocks with chosen prefixes, so auditors upstream see them. **Fetched URLs are not among them** |
| Training data: orchestrator + sub-agent windows, about 7 sub-agent windows per orchestrator window | `trajectory_service.export_trajectory` writes **orchestrator** turns only (ChatML); spoke conversations are not exported |
| Elite-one evolution, each candidate executed once | #278's `--revise-kept` is the same shape. Each candidate runs the scoring set once; only the private gate replicates |
| A homogeneous self-judge | #29 keeps the judge out of reach: a deterministic suite plus a private golden gate |

## Adopt

### 1. Sources travel up from spokes

**Built in #279** as `SPOKE_SOURCES_ENABLED`, a runtime setting. The
footer is "[Sources this agent fetched]", at most 20 URLs, taken from the
spoke's tool-call arguments. Credential-bearing URLs are dropped. A test
confirms it removes read-before-cite's false flag.

- **The change:** extend the evidence-preservation hook so a spoke's reply
  ends with the URLs its own tools fetched this turn. The harness writes this
  from the spoke's tool calls; the model doesn't.
- **Why:**
  - The orchestrator can cite what the spoke read.
  - #279's read-before-cite stops flagging a source just because a spoke
    summarized it away.
  - The audit trail upstream matches what happened downstream.
- **Evidence:** MASS's evidence that contracts matter is correlational. The
  optimizer moved task information into them, but that isn't an ablation.
  Ours is direct: the false flag I described when shipping #279.
- **Scope:** small, and flag-gated, because it changes the text a spoke
  returns.

### 2. Record spoke conversations as training data

**Built in #279** as `FULL_TRAJECTORIES_ENABLED`. Each turn appends one line
per agent (`orchestrator`, `spoke:<name>`) to `agents.jsonl`, in OpenAI chat
format with tool calls, linked by trace id, with this turn's secrets masked.
One finding while building it: the existing export kept only the user's
message and the final reply. So the orchestrator's own tool calls were
missing too, not just the spokes'.

- **The change:** export each spoke's conversation beside the orchestrator's
  trajectory, linked by turn. It goes in the user's records
  (`RECORDS_DIR/users/<ws>/trajectories/`), behind a flag, for storage.
- **Why:** a future training run on Prax's own traffic needs the sub-agent
  half. That is most of MASS's supervision, and the half the paper found more
  token-efficient. Collect it before the GPU is free, not after.
- **Caveats that come with it:**
  - Train only on trajectories a *grounded* signal ranked: user acceptance,
    or verifiable checks. MASS's self-ranked data cost it the two verifiable
    benchmarks.
  - Gate any trained model on Terminal-Bench / SWE-bench-style executable
    checks as well as judged quality.

### 3. Replicate #278's keep decision

**Built in #278** as `--keep-replicates N`. A change is kept only if its
mean gain beats the margin *and* the larger of the incumbent's and the
candidate's run-to-run spread.

- **The change:** run the scoring set at least twice per candidate, or keep
  only on a margin above the measured run-to-run spread.
- **Why:** MASS's identical-prompt flip is the cleanest demonstration we
  have that a single run can't separate a better overlay from a lucky
  execution. #278's private gate already replicates; the keep step before it
  doesn't.

## Bank

- **A prompt captured most of what training bought.** Another sighting of
  the harness beating the weights: [harness learning](harness-learning.md)'s
  fixed tool loop, [IdeaScientist](ideascientist.md)'s untrained harness, and
  now MASS's directive control. Before training anything when the GPU is
  free, run the prompt-only control first: give the base model the behaviour
  as an instruction, and see what's left.
- **Report delivery and conditional quality separately.**
  - A score with zero for missing work rises when more work is delivered,
    even as delivered work gets worse.
  - It is the mirror image of our own error-accounting rule, that failing
    harder must never score better (`summarize_capability_results`, guarded
    by `tests/test_eval_error_accounting.py`; [scorer audit](eval-scorer-audit-2026-08-07.md)): here *finishing more* scored
    better while quality fell.
  - Our scorecards should show both.
- **Optimizer capacity sets the speed of workflow search.** For #278, spend
  on the proposer's model tier before the auditor's.

## Don't adopt

- **The RSI method.** The model ranks its own trajectories, trains on them,
  and then judges the next round.
  - Its agreement with external judges does rise.
  - But the gains it produced were mostly finishing more often, and the
    verifiable coding scores fell.
  - #29 keeps the judge deterministic and out of reach for exactly this
    reason.
- **Mutual-information analysis of workflow text.** Interesting, but
  correlational, and estimated from 12 tasks.

## Related

- [Harness learning](harness-learning.md): trained proposer, frozen solver;
  #278 built its protocol. MASS trains the solver too and loses the
  verifiable benchmarks.
- [Scaling agent systems](scaling-agent-systems.md),
  [constraint factorization](constraint-factorization-mas.md),
  [DeLM shared context](decentralized-shared-context.md): when multi-agent
  structure helps.
- [Judge noise floor](judge-bias-audit-2026-08-20.md): our measurement of
  judge flips. MASS adds execution flips on top.
- [Agentic RL control plane](agentic-rl-evolution-control-plane.md):
  harness evolution as a peer of weight updates. MASS alternates the two.
