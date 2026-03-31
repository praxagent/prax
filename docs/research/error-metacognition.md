# Error Recovery & Metacognition

[← Research](README.md)

### 14. Multi-Perspective Error Recovery

**Finding:** When a tool call fails, analyzing the failure from multiple perspectives before retrying dramatically outperforms blind retry. This extends chain-of-thought reasoning to error diagnosis.

- **Wei et al. (2022)** established chain-of-thought prompting — breaking reasoning into intermediate steps improves accuracy on arithmetic, commonsense, and symbolic reasoning tasks. The same principle applies to error diagnosis: decomposing "why did this fail?" into multiple angles.
- **Shinn et al. (2023)** showed that Reflexion — reflecting on failures and storing verbal feedback for retry — improved HumanEval pass rates from 80% to 91% and ALFWorld from 75% to 97%, all without weight updates.

**Prax implementation:** `analyze_tool_failure()` (`prax/agent/error_recovery.py`) examines each failure from four perspectives: (1) logical consistency (was the tool called correctly?), (2) information completeness (was input missing?), (3) assumptions (did the agent assume something false?), and (4) alternative approach (is there a different tool?). Each perspective produces a confidence-scored diagnosis and suggestion. `build_recovery_context()` formats the analysis for injection into the retry prompt. The four-perspective decomposition was inspired by PR-CoT multi-perspective repair as implemented in ATLAS ([itigges22/ATLAS](https://github.com/itigges22/ATLAS)), which reported an 85.7% rescue rate on code generation failures.

**References:**
- Wei et al., "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models," NeurIPS 2022 — [arXiv:2201.11903](https://arxiv.org/abs/2201.11903)
- Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning," NeurIPS 2023 — [arXiv:2303.11366](https://arxiv.org/abs/2303.11366)

### 15. Metacognitive Failure Profiles

**Finding:** Agents that maintain explicit models of their own weaknesses — and compensate for them via prompt injection — exhibit more stable long-term behavior than agents that treat each failure independently.

- **Flavell (1979)** coined the term "metacognition" — knowledge about one's own cognitive processes and the ability to monitor and regulate them. In an AI agent context, this translates to tracking recurring failure patterns and proactively adjusting behavior.
- **Shinn et al. (2023)** demonstrated that episodic failure memories (Reflexion) enable agents to avoid repeating the same mistakes across multiple attempts, without any gradient updates.
- **Madaan et al. (2023)** showed that iterative self-feedback (Self-Refine) improves outputs by 5–20% across tasks. The key insight: the agent improves more when it has explicit awareness of what went wrong.

**Prax implementation:** `MetacognitiveStore` (`prax/agent/metacognitive.py`) maintains per-component `ComponentProfile`s with `FailurePattern` records. Each pattern has a confidence score, occurrence count, and an optional compensating instruction. When a pattern reaches ≥3 occurrences and ≥40% confidence, it becomes "active" and is injected as a warning into the component's system prompt. Confidence decays over time via an Ebbinghaus-inspired forgetting curve (`conf *= 0.95^days`), naturally pruning stale patterns. ATLAS ([itigges22/ATLAS](https://github.com/itigges22/ATLAS)) implemented a similar metacognitive model with per-category failure tracking and confidence decay.

**References:**
- Flavell, J.H., "Metacognition and Cognitive Monitoring," American Psychologist, 1979 — [doi:10.1037/0003-066X.34.10.906](https://doi.org/10.1037/0003-066X.34.10.906)
- Ram & Cox, "Introspective Reasoning Using Meta-Explanations for Multistrategy Learning," Morgan Kaufmann, 1994 — introspective blame assignment for systematic failure diagnosis in AI systems
- Cox, M.T., "Metacognition in Computation: A Selected Research Review," Artificial Intelligence, 2005 — [doi:10.1016/j.artint.2005.10.007](https://doi.org/10.1016/j.artint.2005.10.007)
- Shinn et al., "Reflexion," NeurIPS 2023 — [arXiv:2303.11366](https://arxiv.org/abs/2303.11366)
- Madaan et al., "Self-Refine," NeurIPS 2023 — [arXiv:2303.17651](https://arxiv.org/abs/2303.17651)
