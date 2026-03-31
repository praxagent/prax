# Agent Checkpointing

[← Agents](README.md)

## The Problem

When the agent chains multiple tool calls to accomplish a task, a failure midway through (API timeout, bad tool output, wrong approach) can leave things in a half-finished state. Without checkpointing, the only option is to re-run the entire chain from scratch.

## The Solution: LangGraph Checkpoints with Automatic Retry

Every conversation turn is checkpointed using LangGraph's built-in `InMemorySaver`. After each tool call, the agent's full state (messages, tool results, pending decisions) is saved to an in-memory checkpoint. If a tool call fails:

1. **Automatic rollback** — the orchestrator rolls back to the last clean decision point (skipping the failed tool result and the tool call itself)
2. **Retry from checkpoint** — the agent resumes from the rolled-back state, choosing a different approach
3. **Budget limit** — after `DEFAULT_MAX_RETRIES` (default: 2) failed attempts, the error is raised to the user

Checkpoints are scoped per-user with unique thread IDs, so one user's state can never leak into another's. Memory is freed automatically when a turn completes.

## How It Works

```
User message
  → start_turn() — creates a fresh thread_id
  → graph.invoke(messages, config={thread_id})
      ├─ checkpoint saved after each step
      ├─ tool call succeeds → checkpoint saved → continue
      └─ tool call fails →
          ├─ can_retry? → rollback 2 checkpoints → retry from last good state
          └─ no retries left → raise error
  → end_turn() — purge checkpoints, free memory
```

## Key Design Decisions

- **In-memory only** — no database or filesystem needed. Fast, zero-config. Checkpoints are ephemeral and scoped to a single turn.
- **Per-turn isolation** — each message from a user gets a fresh thread. No cross-turn state leakage.
- **Rollback granularity** — rolls back 2 steps by default (the failed result + the tool call), landing on the last clean agent decision point.
