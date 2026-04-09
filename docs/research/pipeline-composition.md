# Pipeline Composition: Hardcoded vs Dynamic

[← Research](README.md)

How should a multi-agent system decide *what agents to run in what order* for a given request?  This is the central architectural question for Prax as it scales past its current hand-crafted spokes.  The literature has converged on a clear answer, and this doc captures the research and the resulting design principles for Prax.

## The question

Three possible architectures:

1. **Hardcoded pipelines** — developers write a fixed pipeline for each task type (blog writer, deep-dive note, research spoke). Predictable, testable, fast. Fails when a new request shape arrives: every novel task needs new code.
2. **Pure dynamic composition** — a meta-agent writes a pipeline from scratch per request, choosing which sub-agents to spawn, in what order, with what prompts. Maximally flexible, catastrophically unreliable.
3. **Hybrid** — a library of proven patterns with a dynamic escape hatch when nothing in the library fits.

This doc summarises the academic and industry consensus that the hybrid shape wins, and documents the patterns Prax should adopt.

## 1. Anthropic's position — "Building Effective Agents" (Dec 2024)

Anthropic's [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) establishes the canonical distinction:

- **Workflows** — "LLMs and tools orchestrated through predefined code paths." The human picks the structure.
- **Agents** — "LLMs dynamically direct their own processes and tool usage." The model picks the structure.

Their explicit guidance: **most production work is workflows**.  Reach for agentic dynamic composition only when the structure genuinely cannot be predicted ahead of time.  Even then, use their five named workflow patterns as the vocabulary:

| Pattern | When to use | Composition style |
|---|---|---|
| **Prompt Chaining** | Task cleanly decomposes into fixed subtasks | Hardcoded sequence |
| **Routing** | Input has distinct categories handled by specialists | Hardcoded routing |
| **Parallelization** | Independent subtasks OR voting over multiple attempts | Hardcoded fan-out |
| **Orchestrator-Workers** | Subtasks aren't known upfront, only the top-level shape | **Dynamic interior, fixed outer shape** |
| **Evaluator-Optimizer** | Clear evaluation criteria, refinable output | Hardcoded generate → critique → refine loop |

The crucial one for this discussion is **Orchestrator-Workers** — it's the only pattern that crosses into dynamic composition, and Anthropic describes it as: "subtasks aren't pre-defined, but determined by the orchestrator based on the specific input." This is the escape hatch for requests where the number and shape of subtasks is genuinely unknown.

> **Prax alignment:** the current hub-and-spoke architecture is exactly Orchestrator-Workers. Prax is the orchestrator; spokes are specialised workers.  The `SynthesisPipeline` is an Evaluator-Optimizer.  `research_subtopics` is Parallelization.

## 2. Pure dynamic composition is fragile — the math and the evidence

> **Epistemic note:** Earlier drafts of this doc said "pure dynamic composition FAILS" and that "AutoGPT PROVED unreliability." Both overstated the evidence. The honest framing is: **pure dynamic composition is empirically fragile under realistic conditions, and the error-compounding math makes long chains mathematically difficult** — but AutoGPT/BabyAGI are anecdotal artifacts and explicit "not for production" caveats, not controlled scientific demonstrations. Dynamic systems *can* be tested (probabilistically, via property-based and regression suites); they just require stronger eval harnesses and traceability than deterministic workflows. The critique below is directional, not absolute.

### 2.1 The error-compounding argument

For a chain of *n* steps with per-step success rate *p*, overall success is *pⁿ*.

| Per-step success | 5 steps | 10 steps | 20 steps |
|---|---|---|---|
| 99% | 95% | 90% | 82% |
| 95% | 77% | 60% | 36% |
| 90% | 59% | 35% | 12% |

A fully autonomous planner chaining 20 tool calls at 90% per-step reliability has a **12% end-to-end success rate**. This is the arithmetic reason AutoGPT and BabyAGI degraded into loops and never produced reliable production deployments.  The fix is never "a better model" — it's "fewer steps, with verified checkpoints between them."

### 2.2 AutoGPT / BabyAGI — anecdotal but directionally consistent

These systems are not controlled experiments, but community-reported artifacts are consistent:

- **Hallucination loop** — [AutoGPT Issue #1994](https://github.com/Significant-Gravitas/AutoGPT/issues/1994) and many similar reports: agent endlessly refines its task list, never executes anything material
- **Restart loops** — gets stuck, restarts from scratch instead of progressing
- **Error compounding** — above
- **Uncapped cost** — recursive task spawning burns tokens without proportional output
- **BabyAGI is explicitly labelled "not meant for production use"** in its own [README](https://github.com/yoheinakajima/babyagi)
- **No human-in-the-loop production deployments survived** — every serious derivative added guardrails, domain restrictions, or human checkpoints

**What these artifacts do NOT prove**: they don't prove dynamic composition is *inherently* broken. A dynamic planner constrained by a typed DSL, static validation rules, compute budgets, and a restricted tool allowlist can be engineered to be reliable — LangGraph, DSPy's `Suggest`/`Assert` machinery, and CodeAct all demonstrate this. The failure mode is *unconstrained* autonomy with *unbounded* tool loops and *no* verifiers — not dynamism per se.

### 2.3 Kambhampati — "LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks" ([ICML 2024](https://arxiv.org/abs/2402.01817))

Position paper from the most prominent academic critic of autonomous LLM planning.  The central argument: an autoregressive next-token model "cannot, by itself, do principled reasoning" because planning requires search and verification, which constant-time-per-token decoding cannot do.  Cites Blocksworld benchmarks where GPT-3.5 and GPT-4 produce valid plans only ~12% of the time on standard problems.

Kambhampati's alternative — **LLM-Modulo** — is structurally identical to the hybrid architecture: LLMs as "approximate knowledge sources" generating candidate plans, with **external sound verifiers** (classical planner, simulator, theorem prover) in a tight bidirectional loop.  The verifier is hardcoded; the LLM is the dynamic part; **the verifier never lets bad plans through**.

### 2.4 Tool-call hallucination research (2024–2025)

- **"The Reasoning Trap"** ([arXiv:2510.22977](https://arxiv.org/html/2510.22977v1)) — finding: "current reasoning enhancement methods inherently amplify tool hallucination."  The more latitude you give the model to think, the more confidently it invents nonexistent APIs.
- **ToolComp / NoisyToolBench** — state-of-the-art models score **<50%** on multi-step tool plans where intermediate outputs condition later steps.  Models hallucinate parameter names, misorder dependent calls, and invent default values rather than asking for clarification.

### 2.5 LangChain's strategic pivot

[How to Think About Agent Frameworks](https://blog.langchain.com/how-to-think-about-agent-frameworks/) — LangChain, originally the target of "too much abstraction" criticism, built **LangGraph** as a "lower-level and more flexible alternative" because production customers wanted *control* and *durability*, not autonomy.  A tacit admission from the most prominent agent-framework company that the high-abstraction autonomous-agent direction was wrong for production.

## 3. The winning shape — library + dynamic gap-filler

Six independent research lines converge on the same architecture:

| Source | Library/backbone | Dynamic component | Verifier |
|---|---|---|---|
| **Voyager** ([Wang et al., NeurIPS 2023](https://arxiv.org/abs/2305.16291)) | Growing skill library (code indexed by embeddings) | LLM generates new skills when retrieval misses | Execution errors + self-verification |
| **ChatHTN** ([Muñoz-Avila et al., ICAPS 2025](https://arxiv.org/abs/2505.11814)) | Hand-authored HTN decompositions | LLM fills gaps the symbolic planner can't cover | Provably sound — symbolic frame contains hallucination |
| **LLM-Modulo** ([Kambhampati, ICML 2024](https://arxiv.org/abs/2402.01817)) | External sound verifiers | LLM as "approximate knowledge source" | Classical planner / simulator / theorem prover |
| **Retrieval-Augmented Planning** ([Kagaya et al., 2024](https://arxiv.org/abs/2402.03610)) | Past successful trajectories retrieved as in-context demos | LLM plans when retrieval misses | Environment feedback |
| **MetaGPT** ([Hong et al., ICLR 2024 oral](https://arxiv.org/abs/2308.00352)) | Fixed Standard Operating Procedures (PM → Architect → Engineer → QA) | Dynamic content flowing through fixed roles | Structured output schemas between roles |
| **Anthropic Orchestrator-Workers** | Fixed top-level orchestrator shape | Dynamic subtask decomposition | Synthesis step at the end |

The emergent consensus has a specific shape: **deterministic backbone + library of proven units + LLM as gap-filler when retrieval misses + hard verifier on output**.  No single paper canonicalises this — it's what the field collectively decided works.

### 3.1 Voyager's skill library (the key demonstration)

[Voyager](https://arxiv.org/abs/2305.16291) is the cleanest existence proof that a learned skill library beats pure dynamic planning.  Three components:

1. **Automatic curriculum** — proposes the next exploration goal
2. **Skill library** — executable JavaScript code, indexed by natural-language description embeddings, retrieved by similarity at runtime
3. **Iterative prompting** — execution-error feedback and self-verification between attempts

Results: **3.1× more unique items, 15.3× faster tech-tree progression, 2.3× distance traveled** vs memoryless baselines in Minecraft.

**Skills are composable** — skill A can import skill B. The library grows over time through the curriculum.  The critical mechanic: when a task is *similar* to a previously solved one, the library returns a proven recipe; when it's genuinely novel, the LLM generates a new skill that may or may not get added to the library.

**Voyager's documented failure modes** (both the paper and follow-up discussions):
- Skill granularity is unclear — there's no principled boundary between "a skill" and "a sub-skill"
- Cold-start problem: the library is brittle on novel tasks where no relevant skill exists
- Silent corruption: lenient self-verification can add bad skills to the library, which then propagate

These are design-time concerns Prax needs to handle explicitly.

### 3.2 ChatHTN — the symbolic hybrid

[ChatHTN](https://arxiv.org/abs/2505.11814) interleaves classical HTN decomposition with LLM queries.  When the symbolic planner can't find a decomposition method for a compound task, it asks the LLM to provide one.  The key claim: **"ChatHTN is provably sound; any plan it generates correctly achieves the input tasks."**

The symbolic frame *contains* the LLM's hallucination.  The LLM proposes; the verifier disposes.  This is the same pattern as LLM-Modulo in a different vocabulary.

### 3.3 Where the "pipelines as code" community lands

DSPy, LMQL, Guidance, and Outlines — the compositional meta-prompting community — have **explicitly rejected** runtime LLM pipeline generation.  Their philosophy is the opposite: **lift structure into deterministic code, let the compiler optimize the prompts**.  [DSPy](https://arxiv.org/abs/2310.03714) in particular: pipeline structure is code (developer-controlled), module implementations are learned at compile time through bootstrapped few-shot demonstrations.

The closest exception is **CodeAct** ([Wang et al., ICML 2024](https://arxiv.org/abs/2402.01030)): the model writes executable Python that calls tools.  The Python program *is* the runtime-generated pipeline.  It works because Python is a constrained, verifiable, debuggable meta-language with decades of tooling — not because dynamic composition is intrinsically safe.

## 3.4 "Dynamic" is not binary — autonomy as a continuum

A key insight missing from the "hardcoded vs dynamic vs hybrid" framing: **dynamism is a continuum, not a trichotomy**. The meaningful question isn't "dynamic or not" — it's "*which degrees of freedom* does the orchestrator have at runtime."

### Autonomy levels (decision taxonomy)

| Level | What the orchestrator decides at runtime | What is fixed in code | Verification shape |
|---|---|---|---|
| **L0 — Recipe selection only** | Which pre-built spoke to call | Pipeline shape, prompts, tools | Test each spoke deterministically |
| **L1 — Parameterized skeleton** | Writer/reviewer prompts, revision limit, rubric, model tier | Pipeline phase shape (e.g., research → write → review → revise), tool allowlist | Schema validation on parameters + reviewer backbone |
| **L2 — Validated graph composition** | Sequence of stages from a typed primitive set (including loops/parallel) | Primitive catalog, typed schema, invariants (ordering, tool allowlist, budgets) | Static graph validator + execution trace + human checkpoints |
| **L3 — Unbounded synthesis** | Free-form execution graph, arbitrary tool invention | Nothing | Empirically broken; avoid |

**Prax today is L0** across its spokes. The `SynthesisPipeline` + `research_subtopics` + plan-and-verify machinery are the foundation for L1. L2 is possible with a validated stage primitive set. L3 is off the table.

The decision to escalate from L1 → L2 should be evidence-based: if L1 covers >80% of novel requests after deployment, L2 is unnecessary.

## 3.5 DeepMind scaling study — nuance on "peaks at 4"

Earlier drafts said "parallelism peaks at 4 sub-agents" based on the [Towards a Science of Scaling Agent Systems](https://ar5iv.org/pdf/2512.08296) paper. This is an oversimplification.

The actual findings:

- **Coordination overhead is real** — adding more agents does not monotonically help
- **Optima depend on the task shape** — parallelizable tasks can benefit from more agents, sequential reasoning tasks can degrade sharply
- **Budget ceilings matter** — under fixed compute budgets, communication dominates reasoning beyond ~3-4 agents
- **Different topologies have different sweet spots** — Figure 5 of the paper shows different peaks per model/task/topology

The honest takeaway: **bound parallelism and measure empirically for your task mix**. "4" is a heuristic, not a theorem.

## 4. Prax design principles

Drawing from the above, here are the principles Prax should follow for pipeline composition:

### Principle 1 — Structure is hardcoded, content is dynamic

The *shape* of a pipeline (research → write → publish → review → revise) is committed in code and tested. The *prompts* that instantiate each phase can be dynamic — either hand-written (current spokes) or generated at runtime (future dynamic composition). This matches MetaGPT, Reflexion, Evaluator-Optimizer, and Anthropic's workflow patterns.

Concrete: `SynthesisPipeline` fixes the five phases. The writer/reviewer prompts inside them are pluggable. A custom pipeline would inject different prompts, not a different graph.

### Principle 2 — The backbone must be verifier-enforced

Every dynamic step needs a downstream sound verifier that catches hallucination. In Prax this means:

- **Quality review** for notes (writer → reviewer with specific rejection criteria)
- **Claim audit** for responses (auditor sub-agent checks ungrounded numeric claims)
- **Execution results** for tool calls (governed tool layer, prediction error tracking)
- **Heuristic guards** where cheap (fabrication guard in `note_create`, loop detector in `governed_tool`)

No dynamic composition without a verifier on the output.

### Principle 3 — Library first, synthesise only when nothing fits

The orchestrator's decision tree is:

```
User request
    ↓
Does a proven spoke fit? (Retrieval / routing)
    ↓
Yes  → use it (fast path, predictable, cheap)
No   → synthesise a pipeline from primitives
         ↓
       Run it through the verifier backbone
         ↓
       If successful, optionally save for reuse (Voyager-style skill library)
```

This is the hybrid shape the research converges on.  It's already most of what Prax does — spokes are the library, and `delegate_task` is the dynamic fallback.  What's missing: **a way to synthesise a multi-phase pipeline at runtime, not just a one-shot sub-agent**.

### Principle 4 — Cap the search space

Dynamic composition must be bounded:

- **Fixed phase shapes** — writer, reviewer, publisher, researcher. No arbitrary DAGs generated at runtime.
- **Depth limits** — sub-agents cannot further decompose (`research_subtopics` enforces max depth 2)
- **Width limits** — max 5 subtopics in a parallel fan-out
- **Recursion guards** — `_research_depth` contextvar prevents infinite recursion
- **Budget caps** — tool call budget, model tier caps, explicit revision limits (max 3)

### Principle 5 — Skills grow from usage, not speculation

Do not pre-build exhaustive pipeline libraries.  Start with the common ones (already done: blog, note, research).  When the dynamic fallback successfully handles a new request shape, **save that shape** to a skill library indexed by request embedding.  Next time a similar request comes, retrieve it first.

This is Voyager's curriculum idea, applied to pipelines.  The library grows based on what users actually ask for, not what we guessed they'd ask.

### Principle 6 — Critical operations stay hardcoded

Some things should never be dynamically composed:

- Authentication
- Database writes
- Financial transactions
- Self-modification (the self-improve pipeline has fixed phases for a reason)
- Deletion / destructive operations

The dynamic surface is for content synthesis, research, and document production — tasks where the worst case is "mediocre output" rather than "data loss."

## 4.6.5 Automatic Prompt Engineering (APE) for dynamic prompts

When the orchestrator dynamically generates writer/reviewer prompts (the L1 escape hatch), the quality of those generated prompts directly determines the quality of the output. Using a single forward-pass to write a prompt is fragile — the generated prompt has the same hallucination/specificity issues as any other LLM output.

[Zhou et al., "Large Language Models Are Human-Level Prompt Engineers" (ICLR 2023)](https://arxiv.org/abs/2211.01910) — the APE framework treats prompt generation as a black-box optimization problem with three phases:

1. **Proposal** — generate a diverse pool of candidate instructions
2. **Scoring** — evaluate each candidate via a held-out target model + likelihood/criteria
3. **Resampling** — iterative Monte Carlo refinement, generating semantically similar variants of the highest-scoring candidate

APE achieves human-competitive or superior results without manual trial-and-error.

**For Prax**: when the L1 escape hatch generates writer/reviewer prompts, use APE-style optimization rather than a single forward pass. Generate 3-5 candidate prompts, score them on a representative example, pick the best. This adds latency but dramatically improves output quality on novel requests.

**The theoretical "Meta-Prompting Monad"** ([Zhang et al., 2023](https://arxiv.org/abs/2311.11482)) is mostly category-theory dressing. The practical takeaway is: validate each step of recursive prompt refinement against a rubric to prevent drift. Don't over-engineer the formalism.

## 4.6.6 Three dimensions of self-evolution (taxonomy)

[A Survey of Self-Evolving Agents](https://arxiv.org/html/2507.21046v4) categorizes how agents can autonomously improve over time into three orthogonal dimensions:

| Dimension | What evolves | Example mechanism |
|---|---|---|
| **Model-Centric** | The model's internal processing | Inference-time improvements: parallel sampling, sequential self-correction, structured reasoning pathways |
| **Environment-Centric** | The agent's external knowledge / skill library | Offline experience compilation: successful dynamic pipelines get committed to a persistent skill library (Voyager pattern) |
| **Agentic Topology** | The graph structure / interaction protocol | Runtime adaptation: if a reviewer keeps failing, add parallel reviewers; if a stage is unused, prune it |

**For Prax**: the immediate evolution path is *environment-centric*. When `run_custom_pipeline` succeeds and gets approved, save `(request_embedding → pipeline_config)` to the knowledge graph namespace `pipelines`. Topology evolution is a Phase 3+ concern.

**EvoSkills** ([Liu et al., 2026](https://arxiv.org/html/2604.01687v1)) is one academic precedent for the skill library + verifier pairing — it couples a Skill Generator with a Surrogate Verifier so the agent can test new skills without ground-truth human content. Worth knowing about, not yet a proven production pattern.

## 4.6.7 Production engineering principles for dynamic systems

From [A Practical Guide for Designing, Developing, and Deploying Production-Grade Agentic AI Workflows](https://arxiv.org/html/2512.08769v1) and [Anthropic's Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents):

### Context boundaries

A primary failure mode in dynamic multi-agent systems is **context pollution** — sharing the orchestrator's full operational memory with a worker agent that just needs to format a date. Production systems must enforce:

- **Intra-session isolation**: worker agents get fresh context windows when switching tasks, receiving only the minimum state needed for their phase
- **Cross-task handoffs**: orchestrator passes data between agents via explicit handoffs with structured schemas, not full reasoning traces
- **Global state ≠ per-node state**: structured state machines (LangGraph's `TypedDict`) hold the evolving global state; individual nodes remain lightweight and hyper-focused

### Programmatic orchestration

When tool count grows past 30-50, passing full JSON schemas in the LLM context window becomes computationally prohibitive (tens of thousands of tokens before the agent reads the user request). The fix:

- **Tool discovery via integration layer** — load only the modules relevant to the current task (e.g., Model Context Protocol)
- **Control flow in deterministic code** — `while` loops, strict conditionals, and complex transformations belong in Python, not in the LLM's reasoning loop
- **LLM provides semantic parameters; the SDK layer handles deterministic execution** — explicit separation of "neural reasoning" from "structural routing"

This is the defining hallmark of a scalable hybrid system: the LLM handles the parts it's actually good at (semantic reasoning, content synthesis), and the deterministic runtime handles everything else.

### Graceful degradation

A continuity architecture identifies which agent roles are *essential* and which are *secondary*. If an external service fails, the system should gracefully bypass non-critical sub-agents and complete the primary workflow rather than failing catastrophically. This is the agent-level analogue of circuit breakers.

For Prax: tag each spoke with `essential=True/False`. If the reviewer in a `SynthesisPipeline` is unreachable, fall back to `force_save` mode rather than failing the whole pipeline. We already do this in some places (the existing reviewer "fail-open" behaviour) — make it explicit and consistent.

### Multi-dimensional evaluation

Static accuracy metrics are insufficient for agentic systems. Evaluation must cover four orthogonal dimensions:

| Dimension | Metrics | Strategy |
|---|---|---|
| **Intelligence and accuracy** | Task completion rate, reasoning quality, grounding faithfulness | LLM-as-judge on reasoning traces, comparison to ground truth |
| **Reliability and resilience** | Input variation robustness, API failure recovery, error handling | Stress testing with ambiguous inputs, deliberate failure injection |
| **State and context stability** | Context retention, long-session memory stability, drift rate | Extended session testing across thousands of tokens |
| **Operational efficiency** | Cost per query, latency, tool invocation count | Continuous monitoring with baseline thresholds |

[AgentBench](https://github.com/THUDM/AgentBench) is the standard multi-domain benchmark — Operating System, Database, Knowledge Graph, Lateral Thinking Puzzles, and more, evaluated in containerized environments. Beyond just BFCL for tool calling, AgentBench is a fuller picture.

## 4.7 Decision criteria — mediation questions before building

Before building any new dynamic composition layer, answer these questions explicitly. They transform the "hardcoded vs dynamic" debate from ideology into engineering:

1. **Which failure is more expensive today?**
   - (a) Inability to serve a novel request shape without engineering work, OR
   - (b) An autonomous loop that burns budget / produces wrong output?

   The answer determines whether to prioritize coverage (build L1/L2 fallback) or reliability (keep L0, expand spoke library manually).

2. **Which Prax tasks are genuinely "agentic" vs workflow-suitable?**
   - Agentic: uncertain step counts, environment interaction, unpredictable intermediate state (e.g., bug hunting, open-ended research, browser automation with login flows)
   - Workflow-suitable: stable decomposition (e.g., blog pipeline, deep-dive note, scheduled briefing)

   Build workflows for the second category, reserve dynamic composition for the first.

3. **What is the maximum acceptable variance in latency/cost per task?**
   - Tight tiers (SMS, quick chat) → prefer L0
   - Loose tiers (research projects, content publication) → L1 acceptable
   - Open-ended (self-improvement, long-horizon tasks) → L2 may be warranted

4. **What percentage of requests currently fall through the cracks?**
   - <5% → stay at L0, add spokes as needed
   - 5-20% → L1 fallback is well-justified
   - \>20% → the spoke library isn't the right abstraction; reconsider shape

## 4.8 Engineering next steps — instrument before building

Rather than jumping to L1/L2, the research literature recommends an **evidence-first** approach:

1. **Instrument the current system for coverage gaps.** Log:
   - Request intent clusters (via embeddings)
   - Which spoke matched (or `delegate_task` fallback)
   - Fallback frequency
   - User-rated outcome (explicit feedback or implicit signals)

   Produce a Pareto chart: "top N missing shapes causing X% of failures." This directly tests whether Prax is in "whack-a-mole" territory or simply missing a few high-leverage spokes.

2. **Build the minimum L1 escape hatch as a fallback.** One parameterized pipeline (write → review → revise) with typed schema, explicit budgets (max iterations, max tool calls, max tokens), and validation. Do not over-engineer.

3. **Add a validation layer before execution.** Even at L1, validate that generated parameters meet invariants:
   - Rubric is checkable (produces bool/structured output)
   - Revision limit is bounded (≤ N)
   - Requested tools are in the allowlist
   - Budget is non-zero and finite

4. **Establish an evaluation harness oriented around tool use and long-horizon behavior.**
   - Use [BFCL (Berkeley Function Calling Leaderboard)](https://gorilla.cs.berkeley.edu/leaderboard) as a reference for tool-call correctness benchmarks
   - Internal replay harness: record real traces, replay with different pipeline shapes, compare outcomes
   - Explicitly track tool-call correctness, abstention behavior, and revision cycle convergence

5. **Decide whether L2 is warranted via a gated experiment.** If L1 still fails too often on novel structures after instrumentation:
   - Trial L2 graph composition with a minimal primitive set
   - Add human checkpoints and durable execution (LangGraph-style)
   - Kill switch when traces show repetitive loops
   - Evaluate against the same corpus used to justify L1

**Critical**: do not skip instrumentation. Building L1/L2 without measuring where L0 actually fails produces a solution to a problem you may not have.

## 5. Proposed architecture for Prax

### 5.1 Current state — mostly L0 with L1 machinery already built

Prax's current position on the autonomy continuum:

| Capability | Level | Notes |
|---|---|---|
| Spokes (browser, content, knowledge, research, sandbox, ...) | L0 | Library of hardcoded pipelines for known task shapes |
| `SynthesisPipeline` primitive | L1-ready | Reusable write → review → revise; phase callables injectable |
| Content spoke using `SynthesisPipeline` | L0 | Uses the primitive but phase functions are hardcoded |
| Knowledge spoke `note_deep_dive` using `SynthesisPipeline` | L0 | Same — primitive is there but phases are hardcoded |
| `research_subtopics` | L0.5 | Bounded parallelization with depth/width/timeout caps |
| `delegate_task` | L0.5 | Generic single-agent fallback; no multi-phase shape |
| Quality reviewer + fabrication guard + claim audit + loop detector | Verifier layer | Prevents the failure modes autonomous agents exhibit |
| Plan-and-verify loop (`agent_plan` + `agent_step_done`) | Bounded planning | Inside the orchestrator |

The observation: we already built the L1 primitive (`SynthesisPipeline`). What we haven't done is **expose it as a tool** so the orchestrator can construct custom pipelines at runtime. All the verifier infrastructure needed to make L1 safe is already in place.

### 5.2 Candidate L1 escape hatch — `run_custom_pipeline` with APE

Before building this, instrument the system per §4.8 and measure whether the fallback frequency justifies it.

When/if built, the L1 escape hatch should incorporate the additional research findings:

**APE-style prompt synthesis** (§4.6.5): when the orchestrator generates writer/reviewer prompts at runtime, do not use a single forward pass. Generate 3-5 candidate prompts, score them against a representative example, pick the best. This adds latency but dramatically improves novel-request output quality.

**Context boundaries** (§4.6.7): the writer agent gets a fresh context window with only the task + source material — not the orchestrator's full reasoning trace. The reviewer gets only the draft + rubric — not the writer's intermediate thoughts.

**Programmatic orchestration**: the pipeline's `while` loop (revision cycle), `if` branches (approved vs revise), and budget enforcement live in Python, not in the LLM's reasoning. The LLM only fills in the semantic content (prompts, criteria).

**Graceful degradation**: if the reviewer fails (LLM provider down, parsing error, timeout), fall through to `force_save` after the configured number of attempts rather than failing the whole pipeline. This is already how `note_quality.review_note` behaves — extend the pattern.

**Multi-dimensional evaluation hooks**: every L1 invocation logs intelligence metrics (revision count, reviewer verdict), reliability metrics (timeouts, retries), state metrics (context size at each phase), and efficiency metrics (token spend, latency). Use these to evaluate whether L1 is actually working.

Expose `SynthesisPipeline` as a tool the orchestrator can invoke with runtime-generated phase configurations:

```python
@tool
def run_custom_pipeline(
    task: str,
    writer_instructions: str,
    reviewer_criteria: str,
    needs_research: bool = False,
    max_revisions: int = 2,
    item_kind: str = "Document",
) -> str:
    """Run a custom multi-agent pipeline when no spoke fits.

    Use ONLY when the request doesn't match an existing spoke.
    You specify the writer's approach and the reviewer's criteria;
    the pipeline handles write → publish → review → revise.

    The structure is fixed. Only the PROMPTS are dynamic.
    """
```

**Why this is safe**: the pipeline shape is hardcoded in `SynthesisPipeline`. The orchestrator can only change the prompts that fill the phases — it cannot generate arbitrary execution graphs, invent new tools, or bypass the verifier.

This implements Anthropic's **Orchestrator-Workers** pattern exactly: fixed top-level shape (the pipeline), dynamic content (the prompts), verifier at the end (the reviewer).

### 5.3 Future — skill library that grows

Once `run_custom_pipeline` is stable, add:

1. **Skill memory**: when a custom pipeline succeeds (approved by reviewer, positive user feedback), save `(request_embedding → pipeline_config)` to the knowledge graph namespace we built earlier (`KnowledgeConcept` + custom namespace `pipelines`)
2. **Skill retrieval**: before synthesising a new pipeline, search for similar past pipelines by embedding. If one exists with high similarity + recent success, reuse its config
3. **Skill promotion**: if a skill is reused N times across M users with consistent success, promote it to a hardcoded spoke

This is Voyager's growing skill library, adapted to pipeline configs.  The library grows organically from actual usage.

## 6. What to explicitly NOT build

Based on the research, these should be avoided:

| Anti-pattern | Why | Evidence |
|---|---|---|
| Agent generates its own execution graph at runtime | Arbitrary graphs break the verifier assumption and hallucinate structure | AutoGPT failure modes |
| Agent invents new tools at runtime | Unbounded search space, no verification | Gorilla, ToolBench studies |
| Meta-meta-planner (agent planning how to plan how to plan) | More layers = more compound error | Error-compounding math |
| Removing hardcoded spokes in favour of "let the agent figure it out" | Loses the fast path for the 80% of common requests | Anthropic workflow pattern, DSPy philosophy |
| Fully autonomous execution with no verifier | Tool hallucination amplifies with more autonomy | "The Reasoning Trap" (2025) |
| Unbounded recursion or parallelism | Cost explosion + coordination overhead | DeepMind agent scaling study (peaks at 4 parallel sub-agents) |

## 7. Key references

### Position papers
- Anthropic, "Building Effective Agents" (Dec 2024) — [anthropic.com/engineering/building-effective-agents](https://www.anthropic.com/engineering/building-effective-agents)
- Kambhampati, "LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks" (ICML 2024) — [arXiv:2402.01817](https://arxiv.org/abs/2402.01817)
- LangChain, "How to Think About Agent Frameworks" — [blog.langchain.com](https://blog.langchain.com/how-to-think-about-agent-frameworks/)

### Skill libraries and hybrid architectures
- Wang et al., "Voyager: An Open-Ended Embodied Agent" (NeurIPS 2023) — [arXiv:2305.16291](https://arxiv.org/abs/2305.16291)
- Muñoz-Avila et al., "ChatHTN: Interleaving HTN Planning and ChatGPT" (ICAPS 2025) — [arXiv:2505.11814](https://arxiv.org/abs/2505.11814)
- Kagaya et al., "RAP: Retrieval-Augmented Planning with Contextual Memory" (2024) — [arXiv:2402.03610](https://arxiv.org/abs/2402.03610)
- Hong et al., "MetaGPT: Meta Programming for Multi-Agent Collaboration" (ICLR 2024) — [arXiv:2308.00352](https://arxiv.org/abs/2308.00352)

### Planning approaches
- Yao et al., "ReAct: Synergizing Reasoning and Acting" (ICLR 2023) — [arXiv:2210.03629](https://arxiv.org/abs/2210.03629)
- Wang et al., "Plan-and-Solve Prompting" (ACL 2023) — [arXiv:2305.04091](https://arxiv.org/abs/2305.04091)
- Yao et al., "Tree of Thoughts" (NeurIPS 2023) — [arXiv:2305.10601](https://arxiv.org/abs/2305.10601)
- Besta et al., "Graph of Thoughts" (AAAI 2024) — [arXiv:2308.09687](https://arxiv.org/abs/2308.09687)
- Shinn et al., "Reflexion" (NeurIPS 2023) — [arXiv:2303.11366](https://arxiv.org/abs/2303.11366)
- Wang et al., "CodeAct: Executable Code Actions Elicit Better LLM Agents" (ICML 2024) — [arXiv:2402.01030](https://arxiv.org/abs/2402.01030)

### Failure modes
- "The Reasoning Trap: How Reasoning Amplifies Tool Hallucination" (2025) — [arXiv:2510.22977](https://arxiv.org/html/2510.22977v1)
- Georgievski & Aiello, "An Overview of HTN Planning" (2014) — [arXiv:1403.7426](https://arxiv.org/abs/1403.7426)
- AutoGPT Issue #1994 "Gets stuck in a loop" — [github.com/Significant-Gravitas/AutoGPT](https://github.com/Significant-Gravitas/AutoGPT/issues/1994) (anecdotal, not controlled)
- BabyAGI README "Not meant for production use" — [github.com/yoheinakajima/babyagi](https://github.com/yoheinakajima/babyagi)

### Tool-use evaluation benchmarks
- Berkeley Function Calling Leaderboard (BFCL) — [gorilla.cs.berkeley.edu/leaderboard](https://gorilla.cs.berkeley.edu/leaderboard)
- Patil et al., "BFCL: From Tool Use to Agentic Evaluation" (ICML 2025) — [proceedings.mlr.press](https://proceedings.mlr.press/v267/patil25a.html)
- Patil et al., "Gorilla: LLM Connected with Massive APIs" (NeurIPS 2024) — [arXiv:2305.15334](https://arxiv.org/abs/2305.15334)
- AgentBench (multi-domain agent evaluation: OS, DB, KG, lateral thinking) — [github.com/THUDM/AgentBench](https://github.com/THUDM/AgentBench)

### Automatic prompt engineering and meta-prompting
- Zhou et al., "Large Language Models Are Human-Level Prompt Engineers" (ICLR 2023) — [arXiv:2211.01910](https://arxiv.org/abs/2211.01910) — APE framework
- Zhang et al., "Meta Prompting for AI Systems" (2023) — [arXiv:2311.11482](https://arxiv.org/abs/2311.11482) — theoretical framing of recursive prompt refinement

### Self-evolving agents and skill libraries
- "A Survey of Self-Evolving Agents" — [arXiv:2507.21046](https://arxiv.org/html/2507.21046v4) — three-dimensional taxonomy (model/environment/topology)
- EvoSkills — [arXiv:2604.01687](https://arxiv.org/html/2604.01687v1) — Skill Generator + Surrogate Verifier coupling
- "A Practical Guide for Designing, Developing, and Deploying Production-Grade Agentic AI Workflows" — [arXiv:2512.08769](https://arxiv.org/html/2512.08769v1)
- Anthropic, "Effective Context Engineering for AI Agents" — [anthropic.com/engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

### Scaling and coordination studies
- Huang et al., "Towards a Science of Scaling Agent Systems" — [arXiv:2512.08296](https://ar5iv.org/pdf/2512.08296) — coordination tax, sequential-task degradation, budget ceilings (nuance on "peaks at 4")

### Agent runtime frameworks
- LangGraph — [docs.langchain.com/oss/javascript/langgraph/overview](https://docs.langchain.com/oss/javascript/langgraph/overview) — durable execution, human-in-the-loop, cyclic graphs

### Pipelines-as-code
- Khattab et al., "DSPy: Compiling Declarative Language Model Calls" (NeurIPS 2023) — [arXiv:2310.03714](https://arxiv.org/abs/2310.03714)
- Beurer-Kellner et al., "Prompting Is Programming: LMQL" (2023) — [arXiv:2212.06094](https://arxiv.org/pdf/2212.06094)

### Related Prax docs
- [Orchestration](orchestration.md) — hub-and-spoke, bounded sub-agents
- [Planning and Reflexion](planning-reflexion.md) — Plan-and-Solve, Reflexion in Prax
- [Production Patterns](production-patterns.md) — multi-agent content pipelines, tool overload, sequential prompting
- [Model Routing](model-routing.md) — per-component model selection
- [Hub-and-Spoke Architecture](../architecture/hub-and-spoke.md) — current Prax architecture
