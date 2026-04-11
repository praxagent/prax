# Agent Improvement Loop

Prax implements a trace-centered improvement loop that turns production
failures into permanent regression guards. The pipeline:

```
feedback → failure journal → eval runner → verified fix
```

Every cycle starts with a trace and ends with a better agent.

## Architecture

```
User rates message       ┌──────────────┐
  (thumbs down)    ───►  │   Feedback    │  workspace JSONL
                         │   Service     │
                         └──────┬───────┘
                                │ negative feedback
                                ▼
                         ┌──────────────┐
                         │   Failure     │  JSONL + Neo4j + Qdrant
                         │   Journal     │
                         └──────┬───────┘
                                │ unresolved cases
                                ▼
                         ┌──────────────┐
                         │  Eval Runner  │  replay + LLM judge
                         └──────┬───────┘
                                │ pass/fail report
                                ▼
                         ┌──────────────┐
                         │  Self-Improve │  propose fix → PR
                         │  Agent        │
                         └──────────────┘
```

## 1. Feedback Capture

Users rate agent messages with thumbs up/down via TeamWork. Each rating
is stored in `{workspace}/.prax/feedback/feedback.jsonl`.

**API:**

```
POST /teamwork/feedback
{
  "rating": "negative",
  "trace_id": "a1b2c3d4e5f6",
  "message_content": "The agent said X but should have done Y",
  "comment": "It used the wrong tool"
}
```

Positive feedback is recorded for aggregate quality tracking. Negative
feedback automatically creates a failure journal entry.

**Query feedback:**

```
GET /teamwork/feedback?rating=negative&limit=20
GET /teamwork/feedback/stats
```

## 2. Failure Journal

The failure journal is the bridge between user feedback and the eval runner.
Each entry captures:

- **User input** — what the user asked
- **Agent output** — what the agent said/did
- **Execution trajectory** — the full execution graph snapshot (tools used,
  delegation chain, tier choices)
- **Feedback comment** — the user's correction or complaint
- **Failure category** — auto-classified or manually tagged
- **Tools involved** — extracted from the execution graph

### Storage

The journal uses three-tier persistence:

| Layer | Purpose | Availability |
|-------|---------|-------------|
| JSONL | Primary source of truth | Always |
| Neo4j `:FailureCase` nodes | Graph queries, tool relationships | Best-effort |
| Qdrant embeddings | Semantic similarity search | Best-effort |

If Neo4j or Qdrant are down, the JSONL file keeps working. The system
degrades gracefully.

### Auto-Classification

Failure cases are automatically classified based on signal words in the
feedback:

| Category | Signals |
|----------|---------|
| `wrong_tool` | "wrong tool", "shouldn't have used", "don't use" |
| `hallucination` | "made up", "not true", "doesn't exist" |
| `incomplete` | "didn't finish", "missed", "forgot" |
| `asked_instead_of_acting` | "just do it", "stop asking" |
| `too_slow` | "took forever", "timeout" |
| `permission_error` | "permission", "blocked", "denied" |

### API

```
GET  /teamwork/failures?resolved=false&category=wrong_tool
GET  /teamwork/failures/stats
POST /teamwork/failures/{case_id}/resolve
     {"resolution": "Updated tool descriptions to prefer delegate_browser"}
```

## 3. Eval Runner

The eval runner replays failure cases through the current agent and uses
an LLM judge to score whether the failure has been addressed.

### Single Case

```
POST /teamwork/eval/run
{"case_id": "abc123", "judge_tier": "low", "replay": true}
```

Response:
```json
{
  "passed": true,
  "score": 0.85,
  "reasoning": "The agent now correctly uses txt2presentation instead of refusing.",
  "judge_model": "gpt-5.4-nano"
}
```

### Full Suite

```
POST /teamwork/eval/run
{"judge_tier": "low", "max_cases": 20}
```

Response:
```json
{
  "total": 12,
  "passed": 10,
  "failed": 2,
  "score": 0.82,
  "pass_rate": 0.833,
  "results": [...]
}
```

### Scoring

The LLM judge scores outputs on a 0.0–1.0 scale:

| Score | Meaning |
|-------|---------|
| 1.0 | Failure completely fixed |
| 0.7–0.9 | Substantially improved, minor issues |
| 0.4–0.6 | Partially improved, core failure still present |
| 0.1–0.3 | Marginal improvement |
| 0.0 | Same failure or worse |

A score >= 0.7 counts as **passed**.

### Cost Control

- `judge_tier`: Use `"low"` for cheap judges, `"medium"` for better accuracy
- `replay`: Set to `false` to skip replaying through the agent (cheaper but
  less accurate — only useful if you want to re-judge existing outputs)
- `max_cases`: Limits how many cases are evaluated per suite run

### Results

Results are persisted to `{workspace}/.prax/eval_results/results-YYYY-MM-DD.jsonl`.

```
GET /teamwork/eval/results?date=2026-04-02
```

## 4. Integration with Self-Improve Agent

The self-improve agent can read the failure journal to understand what's
broken and propose targeted fixes. The workflow:

1. Review unresolved failures: `get_failures(resolved=False)`
2. Search for similar past failures: `search_similar_failures(query)`
3. Analyze failure patterns (category breakdown, failing tools)
4. Propose code/prompt changes
5. Run the eval suite to verify the fix: `run_eval_suite()`
6. If passing, deploy via PR

## Key Design Decisions

**Local JSONL as source of truth.** Neo4j and Qdrant are best-effort
enrichment layers. The feedback and failure journal always work, even
if the memory infrastructure is temporarily down.

**Resolved failures stay forever.** A resolved failure case becomes a
permanent regression guard. It's never deleted — only marked as resolved
with a description of the fix.

**Auto-classification is heuristic, not LLM.** Classification runs on
every negative feedback and must be instant. Signal-word matching is fast
and good enough. Misclassification is harmless — the raw feedback comment
is always preserved for the eval judge and self-improve agent.

**The eval runner is expensive.** Each replayed case makes real LLM API
calls (agent + judge). Use `max_cases` to control cost. The default
`judge_tier="low"` uses the cheapest model available.

## File Layout

```
prax/
  services/
    feedback_service.py      # Feedback capture & storage
    memory/
      failure_journal.py     # Failure case persistence (JSONL + Neo4j + Qdrant)
  eval/
    __init__.py
    runner.py                # Eval replay & LLM judge

{workspace}/.prax/
  feedback/
    feedback.jsonl           # All feedback entries
  failure_journal/
    failures.jsonl           # All failure cases
  eval_results/
    results-YYYY-MM-DD.jsonl # Eval results by date
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/teamwork/feedback` | POST | Submit feedback (thumbs up/down) |
| `/teamwork/feedback` | GET | List feedback entries |
| `/teamwork/feedback/stats` | GET | Feedback statistics |
| `/teamwork/failures` | GET | List failure cases |
| `/teamwork/failures/stats` | GET | Failure journal statistics |
| `/teamwork/failures/{id}/resolve` | POST | Mark failure as resolved |
| `/teamwork/eval/run` | POST | Run eval (single or suite) |
| `/teamwork/eval/results` | GET | View eval results |

## Self-Reflection: `review_my_traces`

In addition to the external feedback loop above, Prax can proactively review his own execution traces. The `review_my_traces(count, focus)` tool:

1. Pulls the N most recent completed execution traces from memory
2. Sends them to a HIGH-tier LLM with a structured review prompt
3. Returns concrete, actionable advice on failures, efficiency, patterns, and improvements

**When Prax uses it:**
- After a task fails and he wants to understand why
- When the user says he did something wrong
- Proactively to identify patterns and improve his approach
- When stuck on a class of problems (e.g. `review_my_traces(10, focus="desktop tasks")`)

**What the reviewer analyzes:**
- Tool selection patterns (good and bad)
- Wasted steps and unnecessary tool calls
- Duration and efficiency
- Failure root causes
- What worked well and should be continued

This creates a fast inner loop for self-improvement that doesn't require user feedback — Prax can reflect on his own performance anytime. The system prompt encourages proactive use.
