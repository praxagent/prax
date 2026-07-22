# Eval results — the committed public scorecard

This directory is Prax's **public, trend-tracked benchmark scorecard**: one record
per matrix run, so anyone can see how Prax scores and how that changes over time. It
is the accountability layer on top of the eval engine.

## Non-negotiable: aggregates only

Every file here is **committed to a public repo**, so it may contain **only
aggregate metrics** — pass-rate, n, tokens, cost, config, git commit — and
**NEVER a benchmark question or reference answer.** Committing per-case data would
leak benchmark content and violate the contamination firewall / never-spike rule.
This isn't a guideline you have to remember: `prax/eval/scorecard.py:assert_no_leak`
raises if any per-case field appears in a record, and `tests/test_scorecard.py`
keeps it honest.

- **Public, committed (here):** the distilled scorecard — numbers only.
- **Local, never committed (`$PRAX_EVAL_DIR`):** the raw per-case runs.

## Layout

```
docs/eval-results/
  MATRIX.md            # rolling dashboard: one row per run, a column per benchmark
  2026/
    2026-07-22-<commit>.json   # one immutable, aggregates-only record per run
```

## How a record is made

```bash
make eval-matrix                 # runs every benchmark on real data, RECORD=1 by default
make eval-matrix MATRIX_LIMIT=100   # a more definitive run (more cases each)
```

Each record pins what makes it reproducible + comparable: timestamp, **git commit
of the harness**, model + provider, `matrix_limit`, dataset (real vs seed) per
benchmark, and the per-benchmark pass-rate / n / tokens. Re-running appends a new
row to `MATRIX.md` so the trend is visible at a glance.

## Reading the numbers honestly

- Prax is a **harness**, not a model. A benchmark number is *this model through this
  harness* — the matrix uses a cheap prepaid model for reproducibility, so absolute
  scores are lower than a frontier model would give. The harness's own contribution
  is the **harness-lift** (full vs bare, same model): `make eval-benchmark BENCH=<x> LIFT=1`.
- `dataset: seed` means a benchmark ran on its small inline seed set (no full dataset
  fetched), so treat its number as indicative, not definitive.
- Comparisons to other systems must hold the model constant (or compare lift), or
  they're measuring the model, not the harness.
