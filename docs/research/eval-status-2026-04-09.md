# Eval Status — 2026-04-09

[← Research](README.md) · [← Benchmarks Plan](prax-benchmarks.md)

This doc captures the state of the GAIA eval harness and first
improvement cycle as of 2026-04-09.  It exists so we can context-switch
to other work and return to evals without losing the thread.

## What was built

### Harness (`prax/eval/`)

- **`prax/eval/gaia_single.py`** — single-task GAIA runner with:
  - Honest prompt (raw GAIA question, no eval-mode hints, no tool-use
    instructions — tests Prax exactly as a real user would experience)
  - Isolation: eval data at `/Users/d7082791602/PROJECTS/prax-evals/`,
    outside the repo and outside `workspaces/` so Prax can't read his
    own ground-truth answers
  - Pre-run contamination assertions (`_guards.py`)
  - 120s hard timeout (after overnight hang on first batch)
  - Cost tracking with $2 default kill-switch
  - Natural-response answer extractor (parses Prax's conversational
    output without requiring any special formatting)
  - Pareto CSV accumulator (`gaia_runs.csv`) — one row per run with
    timestamp, task_id, tier, pass, duration, tokens, cost, error
  - Scrubbed public receipt dumper (no GAIA raw content in committed
    files — only task_id, pass/fail, cost, retro notes)
- **`prax/eval/_guards.py`** — asserts `PRAX_EVAL_DIR` is outside
  the git repo AND outside `settings.workspace_dir` before any run
- **`prax/eval/README.md`** — documents the isolation contract,
  compliance rules, and directory layout

### Data location

All eval data lives at `/Users/d7082791602/PROJECTS/prax-evals/`:

```
prax-evals/
├── .gitignore          # `*` — belt and suspenders
├── README.md           # "DO NOT COMMIT"
├── gaia-cache/         # HF datasets cache
├── gaia_runs.csv       # Pareto CSV (added after initial runs — backfill needed)
└── runs/
    └── gaia-{short_id}-{run_id}/
        ├── task.json     # raw GAIA task
        ├── workspace/    # per-run isolated Prax workspace
        ├── response.txt  # Prax's full response
        ├── answer.txt    # extracted answer
        ├── grade.json    # pass/fail + normalized match
        ├── cost.json     # token counts + USD estimate
        └── meta.json     # model, tier, duration, timestamp
```

## Results so far

### 4 tasks, 14 runs, 3 code changes

All runs used medium tier (gpt-5.4-mini) with honest prompts.

#### Run history (chronological)

| Timestamp | Task | Pass | Prax answer | Truth | Duration | Phase |
|---|---|---|---|---|---|---|
| 06:44 | Mercedes Sosa albums | FAIL | 5 | 3 | 24s | Baseline (low tier, no tools used) |
| 06:49 | Mercedes Sosa albums | FAIL | Cantora 2 | 3 | 41s | Medium tier (tools used, wrong count) |
| 16:21 | Scikit-Learn changelog | FAIL | SGDRegressor | BaseLabelPropagation | 33s | Batch 1 |
| 16:22 | 1928 Olympics | FAIL | PAN | CUB | 101s | Batch 1 |
| 16:24 | Yankees 1977 walks | **PASS** | 519 at-bats | 519 | 66s | Batch 1 |
| 16:26 | Mercedes Sosa albums | **PASS** | 3 studio albums | 3 | 29s | After fact-check prompt (cheating ver) |
| 16:27 | Scikit-Learn changelog | FAIL | RegressorMixin | BaseLabelPropagation | 33s | After fact-check prompt (cheating ver) |
| 16:29 | 1928 Olympics | **PASS** | CUB | CUB | 94s | After fact-check prompt (cheating ver) |
| 16:29 | Yankees 1977 walks | FAIL | 600 at-bats | 519 | 45s | After fact-check prompt (cheating ver) |
| 16:35 | Mercedes Sosa albums | FAIL | 4 studio albums | 3 | 37s | After abstract fact-check prompt |
| 16:36 | Scikit-Learn changelog | FAIL | (empty — crash) | BaseLabelPropagation | 50s | After abstract fact-check prompt |
| 16:37 | 1928 Olympics | **PASS** | CUB | CUB | 76s | After abstract fact-check prompt |
| 16:38 | Yankees 1977 walks | **PASS** | 519 at-bats | 519 | 53s | After abstract fact-check prompt |

#### Per-task summary

| Task | Description | Runs | Passes | Pass rate | Stable? |
|---|---|---|---|---|---|
| 8e867cd7 | Mercedes Sosa studio albums 2000-2009 | 4 (excl low-tier) | 1 | 25% | No — answers vary (3, 4, 5, Cantora 2) |
| d0633230 | Scikit-Learn July 2017 changelog bug fix | 3 | 0 | 0% | Stable fail — always picks wrong entry |
| cf106601 | 1928 Olympics least athletes (tie-break) | 3 | 2 | 67% | Mostly stable after fact-check |
| 3f57289b | Yankees 1977 most-walks at-bats | 3 | 2 | 67% | Mostly stable |

**Aggregate: 5/13 = 38% pass rate** (excluding the baseline
low-tier run which used no tools).

### Code changes driven by eval findings

| Finding | Fix | Type | Benchmark-specific? |
|---|---|---|---|
| Low tier (nano) uses 0 tools | Orchestrator default: low → medium | **Systemic** | No — affects all users |
| Low tier on spokes uses 0 tools | Spoke runner default: low → medium | **Systemic** | No — affects all spokes |
| Scheduled tasks use low tier | `_on_fire` creates medium-tier agent | **Systemic** | No — fixes daily briefing |
| No fact-check before answering | Abstract "fact-check before committing" in system prompt | **Systemic** | No — applies to all factual Q&A |
| Overnight hang (no timeout) | 120s hard timeout on eval runner | Harness | N/A |
| Eval data in `workspaces/` | Moved to `../prax-evals/` | Harness | N/A |
| Benchmark examples in prompt | Removed; abstract version only | **Integrity fix** | Rule: never spike benchmarks |
| Per-component tier not env-configurable | Added `{COMPONENT}_{KEY}` env var overrides | **Systemic** | No — all users benefit |

### Failure modes identified

1. **Category confusion** (Mercedes Sosa) — Prax finds a list but
   doesn't filter to the exact category the question asks for.
   Abstract fact-check helps sometimes but not reliably.
   **Status: partially mitigated, not solved.**

2. **Wrong-row-from-a-list** (Scikit-Learn) — Prax finds the right
   page (the changelog) but picks the wrong entry every time.  Three
   runs, three different wrong answers.  The fact-check prompt doesn't
   help because Prax IS re-reading — he's just misidentifying the
   entry.
   **Status: unsolved.  Needs deeper investigation.**

3. **Non-determinism** — the same task can pass on one run and fail
   on the next.  Mercedes Sosa: answers of 3, 4, 5, Cantora 2 across
   runs.  Yankees: 519, 600 across runs.  Temperature is 0.7 — this
   is expected behavior but makes single-run scores meaningless.
   **Status: expected.  Mitigation: report pass^k, not pass^1.**

4. **Tool-call reliability on low tier** — the nano model generates
   0 tool calls for tasks that require them.  Fixed by upgrading
   orchestrator/spoke defaults to medium.
   **Status: fixed.**

## What's next (when we return to evals)

### Immediate (pick up here)

1. **Backfill the Pareto CSV** from the existing 14 runs (the CSV
   code was added after the runs — need a one-time script to parse
   the run dirs and write rows).
2. **Run 5 more Level 1 tasks** to expand the sample beyond 4.  Four
   tasks is too small for meaningful statistics.
3. **Run each task 3× to measure reliability** (`pass^k` at k=3).
   Single-run scores are noise at temperature 0.7.
4. **Investigate the "wrong row" failure** (Scikit-Learn) — trace the
   tool calls to see exactly which fetch result Prax is reading and
   where the misidentification happens.

### Short-term (this month)

5. **Wire real token counting** into the runner — current cost
   estimates are rough char-count approximations.  Hook into
   LangChain's callback system to capture actual prompt/completion
   tokens per model call.
6. **Add τ²-bench runner** — second P1 benchmark.  Retail domain
   first (most published baselines).
7. **Add AgentDojo runner** — P1 safety benchmark.  Tests the
   governance stack.
8. **Run GAIA validation set in full** (53 Level 1 tasks) once the
   harness is stable and reliable.

### Medium-term (next 2 months)

9. **Phase 2 benchmarks**: SWE-bench Verified, Terminal-Bench 2.0,
   WebArena, BrowseComp.
10. **HAL integration** — use Princeton's hal-harness as the adapter
    layer for cross-benchmark cost-aware reporting.
11. **Monthly run cadence** — receipts committed to
    `docs/research/receipts/` with diff-against-last-month.

## Rules established during this cycle

1. **Never spike benchmarks.**  Prompt changes must be an
   abstraction of the problem class, not specific examples from
   failed tasks.  If someone who knows the benchmark reads the
   system prompt, they must NOT be able to tell which tasks failed.
   (See CLAUDE.md Rules section.)

2. **Eval data stays outside the repo** at
   `/Users/d7082791602/PROJECTS/prax-evals/`.  Prax's workspace
   tools can't reach it.  Never propose `workspaces/eval/`.

3. **Honest prompts only.**  No "benchmark evaluation mode", no
   "you MUST use tools", no answer-format instructions.  Prax gets
   the raw question exactly as a user would type it.

4. **Report pass^k, not pass^1.**  Single-run scores are marketing
   numbers.  Temperature 0.7 + non-deterministic tool routing means
   the same task can flip between pass and fail across runs.

## References

- [Benchmarks Plan](prax-benchmarks.md) — the full 1,300-line
  catalog with phased adoption, harness design principles, and
  comparative tables
- [GAIA dataset](https://huggingface.co/datasets/gaia-benchmark/GAIA)
  (gated — requires HF auth)
- [CLAUDE.md](../../CLAUDE.md) — the "never spike benchmarks" rule
- [prax/eval/README.md](../../prax/eval/README.md) — isolation contract
