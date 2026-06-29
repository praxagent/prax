# Agents

Prax keeps its main conversation loop lean by delegating domain-specific work to focused **spoke agents**. Each spoke runs its own LangGraph ReAct loop with a specialized system prompt and curated tool set.

Research shows that LLM tool-selection accuracy degrades past 20–30 tools ([see Research](../research/README.md)). The hub-and-spoke pattern keeps the orchestrator's tool count low while giving each spoke deep domain capabilities.

## Contents

- [Delegation](delegation.md) — Hub-and-spoke delegation, spoke agents, sub-hubs, adding new spokes
- [Self-Improvement](self-improvement.md) — Fine-tuning pipeline, vLLM + Unsloth + LoRA hot-swap
- [Self-Modification](self-modification.md) — Staging clone + verify + hot-swap / PR workflow
- [Self-Regeneration](self-regeneration.md) — The recursive self-improvement *loop* (notice→propose→isolate→verify→canary→record) that drives the two surfaces above. Thesis: RSI is only as safe as its fitness function is **un-gameable**, so the eval-robustness stack (verify / auditor / accept-rate #22) is the *precondition*. Scope = harness self-improvement; graded-autonomy boundary; plugin-first first rung (#29)
- [Checkpointing](checkpointing.md) — LangGraph checkpoints with automatic retry
