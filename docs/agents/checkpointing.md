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

- **In-memory by default** — `InMemorySaver`, fast and zero-config. Checkpoints are ephemeral and scoped to a single turn.
- **Per-turn isolation** — each message from a user gets a fresh thread. No cross-turn state leakage.
- **Rollback granularity** — rolls back 2 steps by default (the failed result + the tool call), landing on the last clean agent decision point.

## Durable checkpoints + user-initiated resume (opt-in)

The defaults above keep everything in-memory: a crashed or timed-out turn is lost when the
process exits. Two opt-in settings make a failed turn **resumable** — including across a
restart — so the user can continue from the failure point instead of re-running from scratch.

| Setting | Default | Effect |
|---|---|---|
| `CHECKPOINT_BACKEND` | `memory` | `sqlite` persists checkpoint **data** to `CHECKPOINT_DB_PATH` (`.prax/checkpoints.sqlite`) so it survives a restart. Falls back to in-memory if `langgraph-checkpoint-sqlite` isn't installed. |
| `CHECKPOINT_RESUME_ENABLED` | `false` | When on, a failed/timed-out turn's checkpoints are **kept** (not purged) for `CHECKPOINT_RESUME_TTL` seconds (default 3600), and a pointer to the thread is persisted to `CHECKPOINT_RESUME_STATE_PATH` (`.prax/resumable.json`). |

How it fits together:
- `CheckpointManager.end_turn(user_id, keep_for_resume=True)` retains the thread instead of
  purging it (the orchestrator passes `keep_for_resume` on a `failed`/`timed_out` turn).
- `ConversationAgent.has_resumable_turn(user_id)` / `resume_last_turn(user_id)` continue the
  saved LangGraph thread (skipping completed steps) and return the response.
- The resumable **pointer** is wall-clock-stamped and written to disk, so after a restart a
  fresh `CheckpointManager` reloads it and resume still works — **provided the checkpoint
  data is durable too** (`CHECKPOINT_BACKEND=sqlite`). With the in-memory backend the pointer
  survives but the data doesn't, so resume restarts the turn.

### Resetting / opting out of a pending resume

A kept turn auto-expires after `CHECKPOINT_RESUME_TTL`. To discard pending resumes sooner —
e.g. you don't want the assistant to pick a failed turn back up — use any of:

- **Disable the feature:** set `CHECKPOINT_RESUME_ENABLED=false` (failed turns stop being kept;
  the persisted file is ignored).
- **Delete the state file:** `rm .prax/resumable.json` (or whatever `CHECKPOINT_RESUME_STATE_PATH`
  points to). This drops every pending resume.
- **Programmatically:** `CheckpointManager.clear_resumable(user_id)` drops one user's pending
  resume (or all of them when called with no argument).
