# Grounding

[← Research](README.md)

### 6. Error Recovery via Checkpointing

**Finding:** Agents that can roll back to a known-good state recover from errors faster than those that must restart from scratch.

- **GCC (Git-Based Contextual Checkpointing)** proposed storing agent state as git commits, enabling rollback to any prior checkpoint. This is especially valuable for long-running tasks where a single bad tool call can corrupt the entire context.
- **OpenHands** and **Devin** both use checkpoint-based recovery in production, with OpenHands reporting that rollback + retry resolves ~40% of failures that would otherwise require human intervention.

**Prax implementation:** `CheckpointManager` with LangGraph checkpointer, `_invoke_with_retry` with rollback to last good checkpoint, fresh-start fallback for corrupted checkpoint state.

### 7. Tool-Grounded Responses (Anti-Hallucination)

**Finding:** Agents hallucinate actions less when the architecture enforces that responses must be grounded in actual tool results.

- **Toolformer** (Schick et al., 2023) established that models can learn when to call tools vs. when to generate — but without architectural enforcement, they still skip tool calls when the "shortcut" (generating a plausible answer) is easier. The solution: make tool use the path of least resistance.
- **ReAct** (Yao et al., 2022) showed that interleaving reasoning and action traces reduces hallucination compared to pure reasoning (chain-of-thought) or pure action (tool-only) approaches. The reasoning trace creates an audit trail that makes skipped steps visible.

**Prax implementation:** "Never hallucinate actions" prompt rules, plan-delegate-verify-synthesize workflow, claim audit post-processing, trace logging for all tool calls.

**References:**
- Schick et al., "Toolformer: Language Models Can Teach Themselves to Use Tools," NeurIPS 2023 — [arXiv:2302.04761](https://arxiv.org/abs/2302.04761)
- Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models," ICLR 2023 — [arXiv:2210.03629](https://arxiv.org/abs/2210.03629)
