# Trajectory Export

[← Guides](README.md)

Prax automatically exports every conversation turn as a training trajectory in real time. This feeds the fine-tuning pipeline (vLLM + QLoRA) with high-quality, outcome-tagged data without any manual batch processing.

## How it works

```
User message → Orchestrator → Agent processes → Response delivered
                                                        ↓
                                              export_trajectory()
                                                        ↓
                                          completed.jsonl  or  failed.jsonl
```

At the end of every turn, the orchestrator calls `export_trajectory()` which:
1. Classifies the outcome (success, correction, negative, tool_failure, empty)
2. Builds a ChatML training example with context
3. Appends one line to the appropriate JSONL file in the user's workspace

This is fire-and-forget — it never blocks the response and silently recovers from errors.

## Output format

Trajectories use **ChatML format** — the standard for OpenAI-compatible fine-tuning:

```json
{
  "messages": [
    {"role": "system", "content": "You are Prax, a capable AI assistant."},
    {"role": "user", "content": "What's the weather?"},
    {"role": "assistant", "content": "Let me check... It's 72°F in LA."}
  ],
  "metadata": {
    "user_id": "12345",
    "outcome": "success",
    "timestamp": "2026-04-03T20:00:00+00:00",
    "agent": "Prax",
    "tool_count": 1
  }
}
```

Each line in the JSONL file is one complete training example. The `metadata` field is stripped before fine-tuning — it's for filtering and analysis.

### Why ChatML?

| Format | Structure | Supported by |
|--------|-----------|-------------|
| **ChatML** | `{"messages": [{"role": "...", "content": "..."}]}` | OpenAI, Unsloth, Axolotl, LLaMA-Factory, vLLM |
| ShareGPT | `{"conversations": [{"from": "human", "value": "..."}]}` | Axolotl, LLaMA-Factory |
| Alpaca | `{"instruction": "...", "input": "...", "output": "..."}` | Most frameworks |

ChatML is the most widely supported format and maps directly to how LLMs process multi-turn conversations. It's also what Prax's existing fine-tuning pipeline uses (Unsloth + QLoRA), so no conversion is needed.

## File locations

```
{workspace}/{user_id}/.prax/trajectories/
├── completed.jsonl   — successful exchanges
└── failed.jsonl      — corrections, tool failures, complaints
```

## Outcome classification

Each trajectory is tagged with one of:

| Outcome | File | Trigger |
|---------|------|---------|
| `success` | completed.jsonl | Normal exchange, no issues detected |
| `correction` | failed.jsonl | User corrected the agent ("no, I meant...", "that's wrong") |
| `negative` | failed.jsonl | User expressed dissatisfaction ("doesn't work", "useless") |
| `tool_failure` | failed.jsonl | A tool returned an error during the turn |
| `empty_response` | failed.jsonl | Agent produced no output |

### What gets skipped
- Scheduled task prompts (`[SCHEDULED_TASK ...]`)
- Reminder deliveries (`[Reminder] ...`)
- Anonymous/no-user messages

## Using trajectories for fine-tuning

### Approach 1: Train on successes only
```bash
# Use completed.jsonl directly — every line is a good example
uv run python -m prax.services.finetune_service train --data completed.jsonl
```

### Approach 2: Train on corrections (DPO/ORPO)
Failed trajectories contain the *wrong* response. When paired with the corrected follow-up (which becomes a `success` trajectory), you can build preference pairs for DPO (Direct Preference Optimization):
- **Chosen**: the corrected response (from completed.jsonl)
- **Rejected**: the original wrong response (from failed.jsonl)

### Approach 3: Filter by metadata
```python
import json

# Only high-quality examples (success + used tools)
with open("completed.jsonl") as f:
    examples = [json.loads(line) for line in f]
    rich = [e for e in examples if e["metadata"]["tool_count"] > 0]
```

## Session grouping

Conversations are multi-turn workflows, not isolated exchanges. "Research X → write a note → schedule a reminder" is one coherent session spanning 3+ turns. Training on individual turns loses this thread.

### How it works

At the start of each turn, a cheap LLM classifies whether the new message continues the current session or starts a new one:

```
Turn 1: "Research quantum computing"        → session abc123 (new)
Turn 2: "Write a note summarizing that"     → session abc123 (continuation)
Turn 3: "Schedule a reminder to revisit"    → session abc123 (continuation)
Turn 4: "What's the weather?"              → session def456 (new topic)
```

**Classification rules (in order):**
1. No existing session → always new
2. Time gap > 30 minutes → always new
3. LLM classification: "Is this a continuation of: {topic summary}?" → yes/no

After each turn, the topic summary is updated via LLM so the next classification has accurate context.

### Session IDs in trajectories

Each trajectory line includes `session_id` in its metadata:

```json
{"messages": [...], "metadata": {"session_id": "abc123", "outcome": "success", ...}}
```

### Assembling multi-turn training examples

```python
import json
from collections import defaultdict

# Group by session
sessions = defaultdict(list)
for filepath in ["completed.jsonl", "failed.jsonl"]:
    with open(filepath) as f:
        for line in f:
            ex = json.loads(line)
            sid = ex["metadata"].get("session_id", "")
            if sid:
                sessions[sid].append(ex)

# Build multi-turn training examples
for sid, turns in sessions.items():
    if len(turns) < 2:
        continue
    # Concatenate all turns into one training example
    multi_turn = {"messages": [turns[0]["messages"][0]]}  # system prompt
    for turn in turns:
        multi_turn["messages"].extend(turn["messages"][1:])  # user + assistant
    # Use for fine-tuning...
```

### Correcting session assignments

If the LLM misclassifies a session boundary, users can reassign traces in the Execution Graph panel:
- Hover over a trace → click the ↔ (move) icon
- Type or paste the target session ID
- Press Enter

The change is persisted to disk and reflected in trajectory metadata.

## Relationship to existing correction harvesting

Prax has two training data pipelines:

| Pipeline | When it runs | What it captures | Best for |
|----------|-------------|-----------------|----------|
| **Trajectory export** (new) | Real-time, every turn | All exchanges with outcome tags | Continuous training, broad coverage |
| **Correction harvester** (existing) | Batch, on-demand | Only user corrections from history | Targeted error correction |

They complement each other. Trajectory export captures everything with no delay. Correction harvesting does deeper analysis of conversation history to find correction patterns that the real-time classifier might miss.

## Configuration

Trajectory export is always on when `FINETUNE_ENABLED=true`. No additional configuration needed. Files accumulate in the workspace and can be cleared manually or via the workspace file browser.

To check trajectory counts:
```python
from prax.services.trajectory_service import get_trajectory_stats
stats = get_trajectory_stats(user_id)
# {"completed": 142, "failed": 8}
```
