# Agents

Prax keeps its main conversation loop lean by delegating domain-specific work to focused **spoke agents**. Each spoke runs its own LangGraph ReAct loop with a specialized system prompt and curated tool set.

Research shows that LLM tool-selection accuracy degrades past 20–30 tools ([see Research](../research/README.md)). The hub-and-spoke pattern keeps the orchestrator's tool count low while giving each spoke deep domain capabilities.

## Contents

- [Delegation](delegation.md) — Hub-and-spoke delegation, spoke agents, sub-hubs, adding new spokes
- [Self-Improvement](self-improvement.md) — Fine-tuning pipeline, vLLM + Unsloth + LoRA hot-swap
- [Self-Modification](self-modification.md) — Staging clone + verify + hot-swap / PR workflow
- [Checkpointing](checkpointing.md) — LangGraph checkpoints with automatic retry
