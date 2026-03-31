# Research — Academic Foundations for Agentic Workflow Design

This section summarizes empirically validated findings from academic research and production systems that inform Prax's architecture. These aren't theoretical — each finding has been demonstrated to improve agent performance on real benchmarks.

## Contents

- [Planning & Reflexion](planning-reflexion.md) — §1-§2: Explicit planning, self-verification
- [Orchestration](orchestration.md) — §3-§5: Bounded sub-agents, workspace persistence, context management
- [Grounding](grounding.md) — §6-§7: Error recovery via checkpointing, anti-hallucination
- [Production Patterns](production-patterns.md) — §8-§10: Framework patterns, tool overload, content pipelines
- [Plugin Sandboxing](plugin-sandboxing.md) — §11: The Glass Sandbox problem and process isolation
- [Model Routing](model-routing.md) — §12-§13: Thompson Sampling, difficulty-driven routing
- [Error & Metacognition](error-metacognition.md) — §14-§15: Multi-perspective recovery, failure profiles
- [Active Inference](active-inference.md) — §16-§18: Self-verification, Active Inference theory, empirical validation
- [Benchmarking](benchmarking.md) — §19: External validation with GAIA, SWE-bench

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
