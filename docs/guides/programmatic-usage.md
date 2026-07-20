# Running Prax programmatically

Most of the time Prax is reached through a channel — TeamWork, Discord, SMS. But
you can also run **one prompt through the full harness directly**, from the
command line or a script: all tools, spokes, memory, and (optionally) the
sandbox, in an isolated workspace, with the answer returned to you as a string.

This is how the eval suite works, and how the ad-hoc "probes" are run (e.g. the
[praxbench](#example-the-praxbench-jacobian-probe) Jacobian question). It's the
right tool when you want to ask Prax something reproducibly and inspect exactly
what it did — which tools it called, how many tokens it burned — without a chat
UI in the loop.

> **Isolation & data.** Each run executes in a **clean, throwaway workspace**
> under `$PRAX_EVAL_DIR` with a synthetic user — it does **not** touch a real
> user's memory or workspace. That's deliberate: it's for probing/evals, not for
> acting as a real user's agent.

## The one-liner: `scripts/ask_prax.py`

```bash
uv run python scripts/ask_prax.py "What is 2^10?"
```

Prompt from a file or stdin (best for long / multi-line prompts), pick a model
tier, and show the tool/spoke trace:

```bash
uv run python scripts/ask_prax.py --file question.txt
echo "explain the CAP theorem" | uv run python scripts/ask_prax.py -
uv run python scripts/ask_prax.py --tier medium --verbose "..."
```

`--verbose` prints (to stderr) the tools and spokes the run used and its token
count — the answer itself always goes to stdout, so you can pipe it.

## The building block: `orchestrator_executor`

Under the hood `ask_prax.py` calls one function — the same one the eval harness
uses:

```python
from prax.eval.capability import orchestrator_executor

run = orchestrator_executor(
    "your prompt here",
    tier="low",             # low | medium | high | pro
    case_id="my-probe",     # names the isolated workspace
    fold_artifacts=False,   # False = return the DIRECT answer (see note below)
)
print(run.answer)           # the answer string
print(run.tools, run.spokes, run.tokens)   # what it did + cost
```

`orchestrator_executor` returns a `CaseRun` with `answer`, `tools`, `spokes`,
`error`, and `tokens`.

> **`fold_artifacts`**: leave it `False` for Q&A-style probes. When `True` the
> executor appends any files the run saved to its workspace onto the answer —
> correct for "produce and save a document" tasks, but it corrupts answer
> extraction for a plain question (it can fold Prax's own system files into the
> reply). Benchmarks always pass `False`.

## Controlling the run with environment variables

Everything about a run is set by env vars, so the same command reproduces it
exactly. `FLASK_SECRET_KEY` is required (any value); the rest have defaults.

### Model & provider

| Var | What it does |
|---|---|
| `LLM_PROVIDER` | `openai` (default), `openrouter`, `anthropic`, … |
| `BASE_MODEL`, `LOW_MODEL`, `MEDIUM_MODEL`, `HIGH_MODEL`, `PRO_MODEL` | concrete model per tier |
| `OPENAI_KEY` / `OPENROUTER_API_KEY` / `ANTHROPIC_KEY` | provider key |
| `OPENAI_BASE_URL` | point the OpenAI-compatible client at a third-party (prepaid) endpoint |

The cheapest path is a prepaid **OpenRouter** model — set `LLM_PROVIDER=openrouter`
and point every tier at one cheap model (this is what `make eval CHEAP=1` does).

### Tools & behaviour

| Var | What it does |
|---|---|
| `SANDBOX_ENABLED` | `true` (default) gives the run the sandbox — `run_python`, `sandbox_shell`, browser, desktop |
| `DATA_TOOLS_ENABLED` | adds the `data_query` DuckDB SQL tool |
| `SEARCH_PROVIDER` | web-search backend (`ddgs`, `serper`, `brave`, `tavily`, `jina`) |
| `TOOL_ECONOMY_ENABLED` | when `true`, nudges the agent to *avoid* unnecessary tool calls — set `false` if you want it to freely search/compute |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` | memory/knowledge embeddings (use `ollama` + `nomic-embed-text` for a keyless local path) |

### Timeouts & self-rate-limiting

| Var | What it does |
|---|---|
| `PRAX_EVAL_TASK_TIMEOUT_S` | per-run wall-clock kill (`0` = none) |
| `PRAX_EVAL_LLM_MAX_RETRIES` | retry transient provider failures — connect timeouts, 429s, empty answers — with backoff (default 4; `0` disables) |
| `PRAX_EVAL_LLM_MIN_INTERVAL_S` | minimum seconds between LLM calls, to pace a heavy run against a rate-limited endpoint (default 0) |

The retry/throttle (see `prax/eval/rate_limit.py`) matters when you run several
probes against one prepaid endpoint at once: without it, a transient timeout or
an empty response scores as a real (wrong) answer and silently deflates a number.

## Example: the praxbench Jacobian probe

A worked example — ask Prax a hard math question on a cheap model, with the
sandbox (so it can use `sympy`/`run_python`) and search on, unthrottled tool use:

```bash
DATA_TOOLS_ENABLED=true SANDBOX_ENABLED=true SEARCH_PROVIDER=serper \
TOOL_ECONOMY_ENABLED=false \
LLM_PROVIDER=openrouter \
BASE_MODEL=deepseek/deepseek-v4-flash LOW_MODEL=deepseek/deepseek-v4-flash \
MEDIUM_MODEL=deepseek/deepseek-v4-flash HIGH_MODEL=deepseek/deepseek-v4-flash \
PRO_MODEL=deepseek/deepseek-v4-flash \
EMBEDDING_PROVIDER=ollama EMBEDDING_MODEL=nomic-embed-text \
PRAX_EVAL_TASK_TIMEOUT_S=360 \
FLASK_SECRET_KEY=ci-test-key \
uv run python scripts/ask_prax.py --tier low --verbose \
  "((1+xy)^3 z + y^2 (1+xy) (4+3xy),  y + 3x(1+xy)^2 z + 3x y^2 (4+3xy),  2x - 3x^2 y - x^3 z) : C^3 -> C^3 has Jacobian determinant -2, and sends (0,0,-1/4), (1,-3/2,13/2), and (-1,3/2,13/2) to (-1/4,0,0). What does this establish? Be rigorous and honest."
```

For a long-running probe, redirect to a log and background it rather than piping
inline:

```bash
... FLASK_SECRET_KEY=ci-test-key \
nohup uv run python scripts/ask_prax.py --file prompt.txt > run.log 2>&1 &
```

## Related

- **Benchmarks** (`make eval-benchmark BENCH=…`) run the *same* executor over a
  dataset with deterministic scoring — see [eval-matrix.md](eval-matrix.md).
- **Cheap prepaid runs**: [cheap-evals.md](cheap-evals.md).
- The isolation/workspace mechanics live in `prax/eval/capability.py`
  (`orchestrator_executor`) and `prax/eval/gaia_single.py`
  (`_isolated_prax_scope`).
