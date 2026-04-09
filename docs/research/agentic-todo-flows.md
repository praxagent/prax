# Agentic To-Do Flows — Empirical Evidence

[← Research](README.md)

This note summarizes the research record on "agentic to-do management" —
the design pattern where an LLM generates and updates an explicit task
list, then iteratively executes steps with tools, observes results, and
replans. Prax's newly shipped [Library Kanban](../library.md) and
[orchestrator plan mechanism](planning-reflexion.md) are instances of
this pattern, so the empirical results here are directly load-bearing
for design decisions.

The headline is that the evidence is **more nuanced than product
narratives suggest**: agentic flows often beat less-agentic baselines
on controlled multi-step benchmarks, but absolute performance on
realistic long-horizon tasks remains far below human levels, and
human-centered studies show real trade-offs in cognitive load and
trust calibration.

### 20. When agentic loops beat less-agentic baselines

**Finding:** On controlled benchmarks of multi-step tool use and
interactive decision-making, agentic flows (plan-then-execute,
interleaved reasoning + acting, sequential subtasking) outperform
single-pass or act-only baselines — but the advantage is
domain-dependent and gains are modest in realistic settings.

- **ReAct** (Yao et al., 2023) reports 71% success on ALFWorld vs 45%
  for an Act-only baseline and 37% for BUTLER, and improvements on
  WebShop (40% vs 30.1% one-shot). The gains come from interleaving
  reasoning and acting so the plan can be revised online. ReAct is
  effectively the prototype agentic to-do flow.
- **TPTU** (Ruan et al., 2023) directly compares a one-step agent
  (global plan emitted at once) against a sequential agent (iteratively
  requesting the next subtask/tool after executing the current one).
  ChatGPT improves from 50% → 55% and InternLM from 15% → 20% in the
  sequential condition. This is one of the cleanest signals that
  "managing tasks as a stepwise list" beats a non-sequential
  alternative — though the gains are modest and some models still fail
  completely.

**References:**
- Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models," ICLR 2023 — [arXiv:2210.03629](https://arxiv.org/abs/2210.03629)
- Ruan et al., "TPTU: Task Planning and Tool Usage of Large Language Model-based AI Agents," NeurIPS 2023 Foundation Models for Decision Making — [arXiv:2308.03427](https://arxiv.org/abs/2308.03427)

### 21. The realism gap — where agentic flows break

**Finding:** On realistic, constraint-heavy, long-horizon planning
benchmarks, even frontier agents achieve a tiny fraction of human
performance. The raw to-do list representation doesn't fix it.

- **WebArena** (Zhou et al., 2024) — 812 long-horizon web tasks in a
  self-hosted environment mirroring real sites. Best GPT-4 agent
  reaches **14.41%** end-to-end success vs **78.24%** for humans. Error
  analysis shows dead loops, invalid actions, and tool misuse —
  failures that map directly onto bad subgoal tracking and poor
  replanning.
- **GAIA** (Mialon et al., 2023) — 466 general-assistant questions
  requiring web browsing, file handling, and multi-hop reasoning.
  Humans **92%**, GPT-4 + plugins **15%**. GAIA was explicitly designed
  to resist gameability, and that gap has held up under scrutiny.
- **TravelPlanner** (Xie et al., 2024) — multi-constraint travel
  planning over ~4M records. The strongest tested agent framework
  achieves **~0.6% final pass rate**. The paper's error analysis
  highlights bad tool argument filling and repetitive dead loops, and
  explicitly introduces a `NotebookWrite` tool to manage working memory
  — essentially externalizing the to-do log to avoid context blowup.

The common thread: agentic scaffolding improves things relative to
one-shot prompting but doesn't rescue constraint-heavy planning. The
to-do list representation is necessary but not sufficient — it needs
to be paired with grounded verification, not just a prettier plan.

**References:**
- Zhou et al., "WebArena: A Realistic Web Environment for Building Autonomous Agents," ICLR 2024 — [arXiv:2307.13854](https://arxiv.org/abs/2307.13854)
- Mialon et al., "GAIA: A Benchmark for General AI Assistants," ICLR 2024 — [arXiv:2311.12983](https://arxiv.org/abs/2311.12983)
- Xie et al., "TravelPlanner: A Benchmark for Real-World Planning with Language Agents," ICML 2024 — [arXiv:2402.01622](https://arxiv.org/abs/2402.01622)

### 22. The double-edged sword of human-visible plans

**Finding:** Exposing the plan/to-do list to the human is powerful but
not uniformly beneficial. Plausible-looking but wrong plans mislead
users, oversight increases cognitive load, and user edits sometimes
make plans *worse*.

The most directly relevant controlled study is **Plan-Then-Execute**
(He et al., CHI 2025), a user study with **N=248** evaluating an LLM
agent as a daily assistant across six everyday tasks (finance
transactions, credit card payment, repair scheduling, alarm setting,
flight booking, travel itinerary planning). Key findings:

- Separating planning from execution enables oversight — the user can
  edit the plan and step through it with proceed/feedback/specify
  controls — **but user involvement can reduce plan quality when the
  system's initial plan was already correct**.
- **User involvement increases cognitive load** (NASA-TLX subscales
  show significant hits to mental demand, temporal demand, frustration,
  and effort).
- **Plausible plans mislead users** — the paper frames agentic
  planning as "double-edged": strong when the plan is high-quality
  and involvement is appropriate, but plausible-looking plans produce
  miscalibrated trust.

**Implication for Prax:** surfacing a to-do list to the human is not
free. The design has to either (a) be confident enough in the plan
that oversight is optional, or (b) communicate uncertainty clearly so
the human doesn't rubber-stamp a plausible-but-wrong plan.

**Reference:**
- He et al., "Plan-Then-Execute: An Empirical Study of User Trust and Team Performance When Using LLM Agents As A Daily Assistant," CHI 2025 — [arXiv:2502.01390](https://arxiv.org/abs/2502.01390)

### 23. Linear to-do lists are weaker than dependency graphs

**Finding:** Real workflows are graphs (branching, parallelizable steps,
dependencies), not linear lists. Benchmarks that measure graph planning
show a substantial gap between sequence and graph representations.

**WorFBench** (Qiao et al., ICLR 2025) benchmarks workflow generation
with intricate graph structures and reports a **~15% gap between
sequence planning and graph planning even for GPT-4**. Generated
workflows also improve downstream task performance with reduced
inference time compared to re-planning from scratch on each turn.

**Implication for Prax:** a flat Kanban or a linear ordered-lesson
notebook captures *some* structure but loses dependency information
("task B requires A's output; task C can run in parallel with B").
Promoting tasks to a DAG representation is a measurable capability
improvement, not just an aesthetic one.

**Reference:**
- Qiao et al., "Benchmarking Agentic Workflow Generation," ICLR 2025 — [arXiv:2410.07869](https://arxiv.org/abs/2410.07869)

### 24. Tool-use decomposition is measurable and improvable

**Finding:** When you break "tool-mediated task automation" into
decomposition → tool selection → parameterization → execution, each
stage has distinct failure modes and distinct improvement levers.

- **TaskBench** (Shen et al., 2023) formalizes task automation into
  stages (task decomposition, tool selection, parameter prediction) and
  evaluates tool graphs with 17,331 samples. It reports strong
  correlation between automated metrics (F1 for tool selection and
  parameter prediction) and human judgments — validating that these
  sub-capabilities can be measured independently.
- **API-Bank** (Li et al., 2023) provides a runnable system with 73
  tools, 314 annotated tool-use dialogues, and 753 API calls. It shows
  a fine-tuned tool-augmented model (Lynx) surpassing its base
  (Alpaca) by >26 points on tool utilization metrics, demonstrating
  that targeted training on decomposition/tool-selection produces
  large measurable gains.

**Implication for Prax:** if Prax's to-do flows are underperforming,
the right diagnostic question isn't "is the plan good?" but "at which
stage does the plan break — decomposition, tool selection, or
parameter filling?" Each stage needs its own observability.

**References:**
- Shen et al., "TaskBench: Benchmarking Large Language Models for Task Automation," NeurIPS 2024 — [arXiv:2311.18760](https://arxiv.org/abs/2311.18760)
- Li et al., "API-Bank: A Comprehensive Benchmark for Tool-Augmented LLMs," EMNLP 2023 — [arXiv:2304.08244](https://arxiv.org/abs/2304.08244)

### 25. Externalized task memory beats context stuffing

**Finding:** Long-horizon agentic flows blow through context limits
quickly. Writing the to-do list / working notes to an external store
(file, notebook, database) and reading them back as needed beats trying
to carry everything inside the conversation context.

This is the motivation behind `NotebookWrite` in TravelPlanner — an
explicit tool that writes intermediate results to a scratchpad so the
agent can free context space. The generalized pattern: **treat the
to-do list as an externally-stored artifact the agent reads and writes,
not a transient thought in the context window.**

AgentBench (Liu et al., 2024) reports "Average Turns" and notes that
"context limit exceeded" is a common failure mode as long-horizon
agentic flows accumulate context — another indirect signal that
agents without external memory get brittle as the task grows.

**Implication for Prax:** Prax's Library already has this primitive —
`library/outputs/` for generated reports, `library/raw/` for unsorted
captures, and `library/.pending_engagements.yaml` for proactive state.
The Kanban's `.tasks.yaml` is externalized task state. The pattern
matches the research — what's less clear is whether Prax's orchestrator
actually *uses* these stores as working memory during long turns, or
whether it still loads everything into the main context.

**References:**
- Xie et al., "TravelPlanner," ICML 2024 — [arXiv:2402.01622](https://arxiv.org/abs/2402.01622)
- Liu et al., "AgentBench: Evaluating LLMs as Agents," ICLR 2024 — [arXiv:2308.03688](https://arxiv.org/abs/2308.03688)

### 26. Task-level prompt injection via tool outputs

**Finding:** In agentic systems, tools can introduce new to-do items
into the agent's working plan. If a tool output reads *"Pay the
electricity bill, buy groceries"*, the agent may incorporate those as
real subtasks even though the user never asked. This is an alignment
and prompt-injection attack surface specific to to-do-managing agents.

The **Task Shield** paper (2024) formalizes this as a "task alignment"
problem: every subtask the agent adds to its plan should be traceable
to the user's original goal, not to a tool's output. The paper
proposes alignment checks at the orchestration layer — essentially
asking "does this new subtask serve the user's goal?" before the agent
commits to running it.

**Implication for Prax:** Prax already has the `claim_audit` hook and a
permission gate for note edits; task alignment is the Kanban analog.
The Library Kanban is especially exposed because it's collaborative
(both human and agent can add tasks), and a malicious or confused tool
could silently drop a task into the board. Prax's current design
doesn't verify that agent-added tasks trace back to a user request.

**Reference:**
- "Task Shield: Enforcing Task Alignment to Defend Against Indirect Prompt Injection in LLM Agents," 2024 — [arXiv:2412.16682](https://arxiv.org/abs/2412.16682)

### 27. Benchmark hygiene caveat — don't trust the headline numbers

**Finding:** Agentic benchmarks are vulnerable to solution leakage,
weak tests, and contamination. The headline success rates often
collapse when the benchmark is audited.

The **SWE-Bench+** analysis (OpenReview 2025) audits the SWE-bench
dataset and finds **32.67% solution leakage** and **31.08% weak tests**
among "successful" patches. After filtering suspicious instances,
apparent pass rates drop from 12.47% to **3.97%**. On a newly-collected
post-cutoff dataset, pass rates are even lower.

**Implication for Prax:** when we eventually benchmark Prax externally
(per the [benchmarking doc](benchmarking.md)), we should either use
post-cutoff datasets or design our own scenarios rather than trusting
public leaderboards. Internal coverage harness runs are more
trustworthy than published SWE-bench numbers.

**Reference:**
- "SWE-Bench+: Enhanced Coding Benchmark for LLMs," OpenReview 2025 — [Paper](https://openreview.net/pdf?id=pwIGnH2LHJ)

## Comparative study table

| Study | Year | Domain | Main finding |
|---|---|---|---|
| ReAct (Yao et al.) | 2023 | ALFWorld / WebShop | Interleaved reasoning + acting beats act-only: 71% vs 45% on ALFWorld. |
| TPTU (Ruan et al.) | 2023 | Multi-tool evaluation | Sequential subtasking beats one-step planning: 55% vs 50% (ChatGPT), 20% vs 15% (InternLM). |
| WebArena (Zhou et al.) | 2024 | Realistic web tasks | Best GPT-4 agent reaches 14.41% vs human 78.24% on 812 long-horizon tasks. |
| GAIA (Mialon et al.) | 2023 | General assistant | Humans 92% vs GPT-4+plugins 15% on 466 multi-hop tool-use questions. |
| TravelPlanner (Xie et al.) | 2024 | Multi-constraint planning | Best agent ~0.6% final pass rate; introduces `NotebookWrite` for externalized task memory. |
| TaskBench (Shen et al.) | 2023 | Tool automation pipeline | Stage-wise decomposition/tool-selection/parameter metrics correlate strongly with human judgments. |
| API-Bank (Li et al.) | 2023 | Tool-augmented dialogue | Fine-tuned tool model beats base by >26 points on tool utilization. |
| WorFBench (Qiao et al.) | 2025 | Workflow graph generation | ~15% gap between sequence and graph planning even for GPT-4. |
| Plan-Then-Execute (He et al.) | 2025 | Daily-assistant tasks, N=248 | User oversight is double-edged: higher cognitive load, sometimes *reduces* plan quality when the system was already correct. |
| Task Shield | 2024 | Safety of tool agents | Tools can introduce to-do items; alignment checks needed to prevent prompt-injection drift. |
| SWE-Bench+ audit | 2025 | SWE-bench eval validity | 32.67% solution leakage + 31.08% weak tests; pass rates collapse under stricter filtering. |

## Key takeaways

1. **Agentic loops help, but only on the margin, and only in the right
   domains.** ReAct and sequential subtasking beat one-shot prompting
   by a few percentage points on tool-use benchmarks. They do not
   magically solve constraint-heavy planning.
2. **Long-horizon realism is the ceiling.** WebArena (14%), GAIA (15%),
   and TravelPlanner (0.6%) all show frontier agents performing
   catastrophically worse than humans on realistic tasks.
3. **Plan-visibility has a dark side.** Exposing the to-do list to the
   human raises cognitive load and can produce miscalibrated trust.
   Plausible plans lull users into rubber-stamping bad work.
4. **Linear to-dos are a lossy representation.** Dependency graphs
   beat sequences by ~15 percentage points for GPT-4-class models.
5. **Externalize working memory.** Write the to-do log to a file, not
   the context window. Context-limit-exceeded is a top failure mode.
6. **Tool outputs are a to-do-injection attack surface.** Agent-added
   tasks should trace back to a user-level goal; alignment checks are
   research-validated.
7. **Benchmarks lie.** Public leaderboard numbers are inflated by
   leakage. Trust post-cutoff or self-collected data.

**Net claim:** "Agentic to-do managers are superior" is only
*conditionally* supported. The design is the right direction for
multi-step tool-mediated work, but the gains vanish in realistic
constraint-heavy settings, and human-centered trade-offs (cognitive
load, trust calibration, safety) are persistent.
