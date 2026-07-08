# Flag-evaluation campaign — 2026-07-08

The reliability/quality flags shipped default-off with the contract that **the
eval gate governs rollout** ([reliable-agentic-systems-bayer.md](reliable-agentic-systems-bayer.md)).
This is the record of the first gate run: every measurable default-off flag was
A/B'd against baseline through the capability suite, with targeted benchmarks
where a flag has a specific claim (injection resistance, sycophancy).

## Method

- **Model:** `gpt-5.4-nano` (`EVAL_TIER=low`) — cheapest feasible; deterministic
  grading means no judge model, and the harness-lift thesis says improvements
  proven on a weak model carry upward.
- **Arms:** baseline + one flag(-group) per arm, set via per-process env vars
  (no `.env` changes). Sequential runs for clean token/latency telemetry.
- **Suite:** capability cases (deterministic checks), **6 of 7 cases** —
  `research_grounded_citation` was excluded campaign-wide (`--skip`) because
  the DuckDuckGo search backends were down/hanging on the campaign date, so the
  case could not produce signal in any arm. All arms ran with
  `WEB_SEARCH_TIMEOUT_S=60`, `PRAX_EVAL_TASK_TIMEOUT_S=300`.
- **Raw results:** `$PRAX_EVAL_DIR/flag-campaign-20260708/<arm>/suites/`
  (data-only dir, never committed).
- **Total cost:** ≈2.3M nano tokens — under $2.

## Results

| Arm | Passed | Tokens vs baseline | Verdict |
|---|---|---|---|
| baseline | 5/6 | 329k (ref) | — |
| `AGENT_MIDDLEWARE_ENABLED` | 5/6 | **−7%** | **FLIPPED** — no regression, cheaper, injection defense-in-depth |
| `PROMPT_SELECTIVITY_ENABLED` | 5/6 | **−2%** | **FLIPPED** — no regression, saves tokens |
| `INTENT_CLARIFICATION_ENABLED` | 5/6 | **+11%** | **NOT flipped** — costs more, no pass-rate gain |
| `RETRIEVAL_RERANK` + `RETRIEVAL_QUERY_EXPANSION` | 5/6 | ~0 | **Deferred** — suite has too little retrieval coverage to detect lift; these add LLM calls, so they need a purpose-built retrieval eval first |
| `UNKNOWN_TOOL_HIGH_RISK` + `HIGH_RISK_SCOPED_CONFIRM` | **4/6** | −38% (early bail) | **NOT flipped** — regression: `computation_verifiable` failed; deny-by-default blocked a needed tool and the agent gave up |
| `CLAIM_AUDIT_ATTENDED_QUARANTINE` | 5/6 | ~0 | **Deferred** — capability clean, but the sycophancy A/B was killed incomplete (dead search backend + the eval-timeout bug below); rerun when search is healthy |

Targeted benchmarks (baseline): injecagent 6/6, sycophancy 5/6, ifeval 4/9
(nano's known instruction-following weakness). Middleware arm's injecagent came
in 5/6 vs baseline 6/6 — within noise at n=6, but honest reading: the middleware
flip rests on *no-regression + lower cost + design intent*, not on measured
security lift at this sample size.

## Not benchmark-measurable (judgment items)

- `LLM_FALLBACK_ENABLED` — fault-injection semantics; also requires a second
  provider key the deployment doesn't have yet. Revisit when one exists.
- `CHECKPOINT_BACKEND=sqlite` + `CHECKPOINT_RESUME_ENABLED` — operational
  durability, covered by unit/integration tests; low-risk to adopt on judgment.

## Byproduct findings (each filed on the punch-list)

1. **`background_search_tool` could hang a turn forever** — a dead DuckDuckGo
   backend parks the call with no timeout anywhere in the stack. Fixed
   (flag-gated `WEB_SEARCH_TIMEOUT_S`, PR #50) after py-spy caught an eval case
   16+ minutes inside the search `select()` loop.
2. ~~**`PRAX_EVAL_TASK_TIMEOUT_S` does not abandon a wedged capability case**~~
   **MISDIAGNOSIS (resolved 2026-07-08).** `_run_with_timeout` abandons a hung
   task correctly at the timeout (proven by `test_timeout_fires_without_blocking`
   / `test_timeout_logs_abandonment`). What the py-spy actually showed was the
   *abandoned daemon worker* — still stuck in the un-bounded search `select()`
   after the case had already been failed at the timeout — which looks like a
   hang but isn't (Python can't force-kill a thread). Root cause was the
   un-bounded search (finding #1, fixed by `WEB_SEARCH_TIMEOUT_S` / PR #50), so
   abandoned workers now die within `WEB_SEARCH_TIMEOUT_S` instead of leaking to
   process exit. `_run_with_timeout` now logs the abandonment explicitly to
   prevent this misread recurring.
3. **`knowledge_note_structured` fails at nano in every arm** — a standing
   model-tier capability gap, not flag-related.
4. `--skip` case filter added to the capability CLI (PR #53) so a case with a
   dead external dependency can be excluded campaign-wide instead of
   invalidating arms unevenly.

## Reproducing / extending

One arm ≈ 2 minutes and pennies at nano tier:

```bash
FLASK_SECRET_KEY=ci-test-key WEB_SEARCH_TIMEOUT_S=60 \
  SOME_FLAG=true PRAX_EVAL_DIR=$PRAX_EVAL_DIR/my-arm \
  uv run python scripts/eval_suite.py capability --tier low \
  --skip research_grounded_citation
```

Compare `results/*.json` pass/tokens against a baseline arm run the same way
without the flag. Keep arms in separate `PRAX_EVAL_DIR`s — suite dirs are
keyed by config, so same-tier arms would otherwise resume-collide.
