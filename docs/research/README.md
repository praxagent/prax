# Research — Academic Foundations for Agentic Workflow Design

This section summarizes empirically validated findings from academic research and production systems that inform Prax's architecture. These aren't theoretical — each finding has been demonstrated to improve agent performance on real benchmarks.

## Contents

- [Planning & Reflexion](planning-reflexion.md) — §1-§2: Explicit planning, self-verification
- [Orchestration](orchestration.md) — §3-§5: Bounded sub-agents, workspace persistence, context management
- [Grounding](grounding.md) — §6-§7: Error recovery via checkpointing, anti-hallucination
- [Production Patterns](production-patterns.md) — §8-§10: Framework patterns, tool overload, content pipelines
- [Pipeline Composition](pipeline-composition.md) — Hardcoded vs dynamic pipeline synthesis; skill libraries; LLM-Modulo
- [Plugin Sandboxing](plugin-sandboxing.md) — §11: The Glass Sandbox problem and process isolation
- [Model Routing](model-routing.md) — §12-§13: Thompson Sampling, difficulty-driven routing
- [Error & Metacognition](error-metacognition.md) — §14-§15: Multi-perspective recovery, failure profiles
- [Active Inference](active-inference.md) — §16-§18: Self-verification, Active Inference theory, empirical validation
- [Benchmarking](benchmarking.md) — §19: External validation with GAIA, SWE-bench
- [Agentic To-Do Flows](agentic-todo-flows.md) — §20-§27: Where explicit plan+execute loops beat alternatives (and where they don't)
- [Prax Changes From To-Do Research](prax-changes-from-todo-research.md) — Proposals doc mapping the to-do research findings to concrete Prax changes with priorities and sizes

## Key Takeaways

1. **Planning is not optional** — it's the single highest-leverage intervention for multi-step tasks.
2. **Verification beats generation** — checking work is cheaper and more reliable than generating it perfectly the first time.
3. **Persistence prevents drift** — re-inject state every turn; don't trust the context window to remember.
4. **Bound your parallelism** — diminishing returns kick in hard after ~4 concurrent sub-agents.
5. **Make the honest path the smooth path** — architectural enforcement (plan requirements, verification gates) works better than prompt-only instructions.
6. **Fewer tools, better choices** — tool selection accuracy collapses past 20–50 tools. Use hub-and-spoke delegation or on-demand tool search to keep each agent's tool set focused.
7. **Diverse reviewers improve quality** — different models/providers in a review loop outperform same-model self-critique by 9+ percentage points.
8. **Put security boundaries at the OS level** — in-process Python sandboxing is fundamentally fragile (the Glass Sandbox). Use subprocess/process isolation as the primary boundary, keep in-process guards as defence-in-depth, and make the framework enforce limits the plugin code cannot override.
9. **Let the data choose the model** — Thompson Sampling learns which tier works best per component, replacing guesswork with Bayesian inference.
10. **Spend compute where it matters** — difficulty-driven routing allocates expensive models to hard tasks and cheap models to easy ones, matching adaptive computation research.
11. **Never trust the model's self-reported confidence** — LLMs are systematically miscalibrated due to RLHF. Measure uncertainty extrinsically through prediction errors, behavioral variance, and token entropy. The harness must be the arbiter of confidence, not the model.
12. **Prove every mechanism earns its complexity** — log prediction errors, correlate with outcomes, A/B test epistemic gating. Remove mechanisms that don't measurably improve task completion.
13. **Benchmark externally** — internal tests validate architecture; external benchmarks (GAIA, SWE-bench) establish competitive positioning and surface blind spots.
14. **Autonomy is a continuum, not a trichotomy** — the meaningful question isn't "hardcoded vs dynamic" but "which degrees of freedom at runtime". Use an autonomy-level taxonomy: **L0** (recipe selection only), **L1** (parameterized fixed skeleton — prompts/rubric/budget dynamic, shape fixed), **L2** (validated graph composition from typed primitives), **L3** (unbounded — empirically broken, avoid). Pure dynamic composition is empirically fragile (AutoGPT-style); pure hardcoded pipelines can't cover the long tail. The winning shape across Voyager, ChatHTN, LLM-Modulo, RAP, MetaGPT, and Anthropic's orchestrator-workers is: **deterministic backbone + library of proven units + LLM as gap-filler when retrieval misses + hard verifier on output**. Prax today is L0 with L1 machinery built but not exposed. Before escalating, **instrument the current system first** — measure where L0 actually fails (Pareto chart of missing shapes) and justify L1/L2 from evidence, not ideology.
15. **Agentic to-do flows are only conditionally superior** — they beat one-shot prompting on controlled tool-use benchmarks by a few percentage points (ReAct 71%→45%; TPTU sequential 55%→50%), but absolute performance collapses on realistic constraint-heavy planning (WebArena 14%, GAIA 15%, TravelPlanner 0.6%). Plan-visibility is double-edged — exposing the to-do list raises cognitive load and can *reduce* plan quality via miscalibrated trust. Dependency graphs beat linear to-dos by ~15 points. Externalize working memory to files instead of stuffing the context window. Treat tool outputs as a to-do-injection attack surface; every agent-added subtask should trace back to a user goal. Plausible plans mislead users more than ugly correct ones do.
