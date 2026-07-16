# The eval matrix — running the full scorecard & keeping the historical record

[← Guides](README.md)

Prax is measured on a **matrix of standard benchmarks** run through the full
harness on real data. This guide covers two things:

1. **[Running the full matrix yourself](#running-the-full-matrix)** — one command.
2. **[The historical results record](#the-historical-results-record)** — the plan
   for tracking progress over time with public accountability.

---

## Running the full matrix

The whole matrix is one target:

```bash
make eval-matrix                    # 40 real cases per benchmark (a few dollars)
make eval-matrix MATRIX_LIMIT=200   # a definitive run (~200 cases/benchmark)
```

That runs **every benchmark adapter through the full harness** on the cheap
prepaid OpenRouter model, deterministically graded (no LLM judge). It's
**resumable** — re-run the same command after a kill/crash and it continues where
it stopped. Results land under `$PRAX_EVAL_DIR/suites/` (data-only, never
committed — the contamination firewall).

### What "real data" means here

Adapters ship a tiny **inline seed set** so keyless `make ci` never touches the
network. `eval-matrix` sets `PRAX_EVAL_FULL_DATASETS=1`, which swaps in the **real
HuggingFace test sets** for the benchmarks that have them wired, capped at
`MATRIX_LIMIT` cases (a representative subset — the "-lite"/-500 configs labs
report — so a pass costs a few dollars, not hundreds).

| Real dataset wired | Seed set only (bespoke format) |
|---|---|
| gsm8k, mmlu_pro, math (MATH-500), humaneval, truthfulqa, **gpqa** (Diamond) | ifeval, injecagent, sycophancy, bfcl, halueval |

The seed-only ones still run — they just measure against their inline set until
their real loaders are wired. `simpleqa` is model-graded (not in the deterministic
matrix).

### Prerequisites (one-time)

1. **Prepaid key + local embeddings** in `.env`:
   ```dotenv
   OPENROUTER_API_KEY=sk-or-xxxx
   EMBEDDING_PROVIDER=ollama
   EMBEDDING_MODEL=nomic-embed-text
   ```
   (`CHEAP=1` — which `eval-matrix` sets — points every model tier at the
   OpenRouter model and routes embeddings to local Ollama, so nothing leaks to a
   paid embedding endpoint. See [cheap-evals.md](cheap-evals.md).) Pull the model
   once: `ollama pull nomic-embed-text`.

2. **Fetch the real datasets once** (caches them under `$PRAX_EVAL_DIR/datasets/`,
   outside every git repo):
   ```bash
   uv run python scripts/fetch_eval_datasets.py            # all open sets
   uv run python scripts/fetch_eval_datasets.py gpqa       # GPQA-Diamond alone
   ```
   **GPQA-Diamond is gated** — set `HF_TOKEN_RO` in `.env` (a read-only HF token)
   **and accept the dataset's terms** on its HuggingFace page first, or the fetch
   401s. Once cached, refreshes are optional; the cache persists.

3. **Sandbox up** for the `humaneval` leg (it executes generated code in the
   container):
   ```bash
   make restart-sandbox
   ```

### Cost & time

At Prax's ~28K-token-per-task harness overhead, budget roughly **$1–2 per
40-case matrix** on DeepSeek-V4-Flash and a few hours wall-clock (concurrency
defaults to 1 for isolation). A 200-case matrix is ~5× that. The prepaid balance
is a hard ceiling — you cannot overspend.

### Individual benchmarks

To run one benchmark (or debug):

```bash
PRAX_EVAL_FULL_DATASETS=1 PRAX_EVAL_DATASET_LIMIT=40 make eval-benchmark BENCH=gpqa CHEAP=1
make eval-benchmark BENCH=mmlu_pro LIFT=1 CHEAP=1   # + the harness-lift number
```

---

## The historical results record

**Status: planned, not yet started.** We deliberately hold off until (a) the
matrix has been shaken down so every benchmark runs end-to-end, and (b) the last
planned benchmark is added — so the *first* committed record is a clean baseline.

The plan, once we start it:

### One non-negotiable: aggregates only, never the data

The record is **committed to the public repo** (that's the accountability point),
so it may contain **only aggregate metrics** — pass-rate, n, tokens, cost, config
— and **never benchmark questions or reference answers.** Committing per-case data
into a public repo would leak benchmark content and violate the
contamination firewall / never-spike rule. So:

- **Public, committed:** the distilled scorecard (numbers only).
- **Local, never committed:** the raw per-case runs stay in `$PRAX_EVAL_DIR`
  (`prax-evals/`, the sibling dir outside git) exactly as they do today.

That split is what makes public accountability *safe*.

### Structure

```
docs/eval-results/
  MATRIX.md                     # rolling public dashboard: one row per run,
                                #   columns per benchmark — the progress trend
  2026/
    2026-07-16-<runid>.json     # one immutable record per run
```

Each per-run record captures what makes it **reproducible and comparable**:
timestamp, **git commit of the harness** (so a row is pinned to exact code),
model + provider, subset size + config flags (`MATRIX_LIMIT`, dataset versions),
per-benchmark `{pass_rate, n, tokens, cost}`, the harness-lift number, and total
cost.

### Populated automatically

A `--record` flag on the eval runner will write the aggregate JSON and append a
`MATRIX.md` row at the end of a run, so recording is a byproduct of running, not a
manual chore (manual matrices rot). `make eval-matrix` will pass it by default.

Until then, the campaign write-ups in
[`docs/research/`](../research/) (e.g. the flag-eval and validation campaigns) and
the [Verification Ledger](../VERIFICATION_LEDGER.md) are the narrative record.
