# Orchestration

[← Research](README.md)

### 3. Centralized Orchestration with Bounded Sub-Agents

**Finding:** A hub-and-spoke architecture (one orchestrator delegating to specialized sub-agents) scales better than peer-to-peer multi-agent systems — but only up to a point.

- A Google DeepMind scaling study found that **agent performance peaks at roughly 4 sub-agents** in parallel. Beyond that, coordination overhead dominates — the orchestrator spends more time synthesizing than the sub-agents save by parallelizing. The recommendation: cap parallelism and use sequential delegation for dependent tasks.
- **MetaGPT** (Hong et al., 2023) showed that encoding Standard Operating Procedures (structured role-based workflows) into multi-agent systems reduced code hallucinations by 20–30% compared to unstructured agent communication.

**Prax implementation:** `delegate_task` / `delegate_parallel` with centralized orchestrator, `_run_subagent` with category-specific tool sets.

**References:**
- Hong et al., "MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework," ICLR 2024 — [arXiv:2308.00352](https://arxiv.org/abs/2308.00352)

### 4. Workspace and Scratchpad Persistence

**Finding:** Agents that write intermediate results to persistent storage (scratchpad files, workspace logs) maintain coherence over long tasks far better than those relying solely on context windows.

- **SWE-Agent** (Yang et al., 2024) demonstrated that a well-designed agent-computer interface — including a scratchpad for notes, structured file navigation, and linting feedback — raised SWE-bench resolve rates from 1.3% (raw GPT-4) to 12.5%. The scratchpad was critical for tasks requiring more than 5 tool calls.
- **Voyager** (Wang et al., 2023) used a persistent skill library in Minecraft — essentially a workspace of reusable code. Agents with the skill library explored 3.3× more map area and obtained 15.3× more unique items than memoryless baselines.

**Prax implementation:** Git-backed per-user workspaces, instruction persistence (`save_instructions`), workspace context injection every turn, trace logging.

**References:**
- Yang et al., "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering," 2024 — [arXiv:2405.15793](https://arxiv.org/abs/2405.15793)
- Wang et al., "Voyager: An Open-Ended Embodied Agent with Large Language Models," NeurIPS 2023 — [arXiv:2305.16291](https://arxiv.org/abs/2305.16291)

### 5. Context Window Management and Drift Prevention

**Finding:** As conversations grow, agent performance degrades due to "context drift" — earlier instructions get diluted by accumulated messages.

- **Lost in the Middle** (Liu et al., 2023) showed that LLMs are worst at retrieving information placed in the middle of long contexts. Performance follows a U-curve: best for information at the beginning or end, worst in the middle. This means system prompts and plans should be re-injected or placed at boundaries, not buried in conversation history.
- Production systems (Devin, OpenHands) address this by **re-injecting the plan and current state as system messages** at each turn, rather than relying on the model to remember earlier instructions from deep in the context.

**Prax implementation:** System prompt + workspace context rebuilt every turn, plan status injected into messages, instruction persistence for mid-conversation re-reads.

**References:**
- Liu et al., "Lost in the Middle: How Language Models Use Long Contexts," TACL 2024 — [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)
