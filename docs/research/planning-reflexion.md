# Planning & Reflexion

[← Research](README.md)

### 1. Explicit Planning Before Execution

**Finding:** Agents that create a structured plan before taking action significantly outperform those that dive straight into tool calls.

- **Plan-and-Solve Prompting** (Wang et al., 2023) showed that generating a plan before solving improves accuracy on math and reasoning benchmarks by 5–10% over standard chain-of-thought. The key insight: planning reduces error propagation because the agent commits to a strategy before executing.
- **ADaPT** (Prasad et al., 2024) demonstrated that recursive task decomposition — breaking a task into subtasks, and subtasks into sub-subtasks — matches or beats fixed-depth planning on ALFWorld and WebShop benchmarks, while adapting to task complexity at runtime.

**Prax implementation:** `agent_plan` + complexity classifier + orchestrator-level plan enforcement.

**References:**
- Wang et al., "Plan-and-Solve Prompting," ACL 2023 — [arXiv:2305.04091](https://arxiv.org/abs/2305.04091)
- Prasad et al., "ADaPT: As-Needed Decomposition and Planning with Language Models," NAACL 2024 — [arXiv:2311.05772](https://arxiv.org/abs/2311.05772)

### 2. Self-Verification and Reflexion

**Finding:** Agents that verify their own outputs and retry on failure dramatically outperform single-pass agents.

- **Reflexion** (Shinn et al., 2023) introduced a loop where the agent reflects on failures (stored in an episodic memory buffer) and retries with that context. This improved pass rates on HumanEval from 80% to 91% and on ALFWorld from 75% to 97% — without any weight updates.
- **Self-Refine** (Madaan et al., 2023) showed that iterative self-feedback (generate → critique → refine) improves output quality by 5–20% across tasks including code generation, math reasoning, and dialogue.

**Prax implementation:** Plan step verification via `agent_step_done`, checkpoint-based retry in `_invoke_with_retry`, claim audit layer.

**References:**
- Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning," NeurIPS 2023 — [arXiv:2303.11366](https://arxiv.org/abs/2303.11366)
- Madaan et al., "Self-Refine: Iterative Refinement with Self-Feedback," NeurIPS 2023 — [arXiv:2303.17651](https://arxiv.org/abs/2303.17651)
