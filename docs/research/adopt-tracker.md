# Adopt Tracker — concrete things to bring into Prax, and where each stands

**Purpose.** The research lane (`docs/research/`) produces a verdict per assessment, but the *actionable* "adopt X" candidates scatter across ~30 docs and the backlog. This is the single place that tracks them so none is lost. It is a **living index of decisions**, not new analysis — each row points at the assessment that argued it and the code/backlog item that will carry it.

**Started:** 2026-07-13 (TJ: "track other things we should bring in so we don't lose track"). Seeded from the recent assessment cluster; extend it whenever an assessment lands an adopt-candidate.

**Status legend:** ✅ shipped · 🔨 in PR / in progress · 📋 queued (agreed, not started) · ⏸ parked (blocked on a decision or a precondition) · 💤 someday (needs infra Prax doesn't have)

---

## The self-regeneration cluster (highest leverage — this is the #29 through-line)

Everything here feeds [IDEAS_BACKLOG #29](../IDEAS_BACKLOG.md) (close the recursive-self-improvement loop inward). The gate is an un-gameable fitness function; these rows build it.

| Item | From | Status | Where it lands |
|---|---|---|---|
| **Public/private golden split** (select on held-out score) | [aide2](aide2-recursive-self-improvement.md) | 🔨 PR #80 | `Golden.visibility`, `summarize_split`, `accept_change` in `prax/eval/goldens.py` |
| **`accept_change` selection gate** (private↑ at ≤cost, fail-closed, reward-hack hard-reject) | [aide2](aide2-recursive-self-improvement.md) | 🔨 PR #80 | shipped as a pure helper; **no loop consumes it yet** |
| **Cost-budgeted selection** (improve private at equal-or-lower tokens) | [aide2](aide2-recursive-self-improvement.md) | 📋 | `accept_change` takes cost args; **wire real token metering** through `run_golden_suite`/replay |
| **Heterogeneous golden gate** (never gate on one golden = never reward a spike) | [aide2](aide2-recursive-self-improvement.md) | 📋 | #29 P1 design: gate a proposed change against the *whole* private set |
| **Complexity/dead-code gate on self-mods** (score↑ is necessary, not sufficient) | [aide2](aide2-recursive-self-improvement.md) | 📋 | #29 accept gate runs `/simplify` + syntax/layer linters before adopt |
| **Eval-harness edits = HIGH-risk / human-gated** (the loop can reach its own scorer) | [aide2](aide2-recursive-self-improvement.md) | 📋 | #29 graded-autonomy boundary (policy, cheap to state) |
| **Taste signal in the accept-gate** (pairwise "is the new version *better*, not just passing?" preference judge — the missing *measurable* quality axis beyond correctness+simplicity) | [proofjudge](proofjudge-taste.md) | 📋 | #29 accept-gate: correctness (verify) + simplicity (complexity gate) + **taste** (pairwise-preference judge). Stops evolving correct-but-bloated code. Signal not hard-gate (~66% aligned); name whose taste |
| **Pairwise/context-aware judging** (rejected-vs-accepted preference + tools to judge *fit*, not isolated correctness; "ties ⇒ fix the rubric") | [proofjudge](proofjudge-taste.md) | 📋 | strengthens `score_golden` / the [diffuse-ai-control](diffuse-ai-control-judge-robustness.md) auditor where quality is subjective |
| **Audit the *spec/check*, not just the answer** (any flow where Prax writes BOTH a check and the thing checked — self-written test, self-graded rubric, self-formalized spec — the check is the un-verified surface; maker≠checker applied to the spec) | [axiomprover](axiomprover-imo-formalization.md) | 📋 | general auditing principle; extends `claim_audit`. Un-gameable ensembles = members filtered by a *sound* verifier (kernel), not an LLM judge |
| **RSI ladder framing** (delegation→net-positive→ignition→inflection) | [aide2](aide2-recursive-self-improvement.md) | ✅ | in the aide2 doc + #29 |
| **Disagreement-driven golden curation** (auditor↔judge disagreement = mislabel) | [expert-judgment](expert-judgment-finetune.md) | ✅ | `run_golden_curation` in `prax/eval/goldens.py` |
| **Supervising auditor** (high-tier re-checks the cheap judge's passes) | [diffuse-ai-control](diffuse-ai-control-judge-robustness.md) | ✅ | `score_golden(audit=…)`, `EVAL_AUDITOR_ENABLED` (#26) |
| **Substance-over-polish hardened judge prompt** | [diffuse-ai-control](diffuse-ai-control-judge-robustness.md) | ✅ | `_SCORE_PROMPT` |
| **Binary per-criterion judging (not Likert)** | [edge-bench](edge-bench-learning-curves.md) | ✅ | `_binarize` in `score_golden` |
| **"Verifiable beats judgeable"** (deterministic regex criteria) | [edge-bench](edge-bench-learning-curves.md) | ✅ | `RubricCriterion.verify` |
| **P1 plugin micro-loop** (notice→propose→isolate→verify→canary→rollback) | [autoresearch](autoresearch-labless.md) + [aide2](aide2-recursive-self-improvement.md) | ⏸ TJ | #29 P1 — de-risked by aide2 evidence; ~3–5 days; **gate ready after the split lands** |
| **Failure-provenance diagnosis** (classify bad-plan vs bad-execution before retry → replan vs retry) | [arts](arts-agentic-tree-search.md) | 📋 | flag-gated orchestrator/#29 heuristic (more surgical than auto-tier-escalation) + an eval-scoring lens ("don't punish a correct plan for flaky execution") |

## Capability candidates

| Item | From | Status | Notes |
|---|---|---|---|
| **Lean toolchain in sandbox + governed `lean_check`** (+ axiom-audit trust gate) | [cdc-lean](cdc-lean-teach-prax-lean.md) | ✅ | Shipped + **verified live** on known theorems (`prax/agent/lean_tools.py`, `LEAN_TOOLS_ENABLED`; Lean 4.31.0 in the sandbox image, toolchain-only). The `sorry`/axiom trust gate works. |
| **Lean eval adapter** (miniF2F/PutnamBench; keyless seed + sandbox-scored halves) | [cdc-lean](cdc-lean-teach-prax-lean.md) | ⏸ | after `lean_check`; datasets stay in `PRAX_EVAL_DIR` |
| **Persona/user-simulator multi-turn evals, deterministic Pass@k** (τ-bench/τ²-bench) | [sierra](sierra-agent-platform.md) | 📋 | the biggest gap vs the single-turn capability suite; CPU-cheap (deterministic state grading) |
| **"Executable world-models" — a general model-induction capability** (the **5-module verifier-centric stack**: perceive/abstract → propose executable hypotheses → execution-verify → counterexample-repair → plan-and-disambiguate; a `modeling`/`simulate` extension of the sandbox) | [arc-agi-3](arc-agi-3-schema-harness.md) + [exec-world-models](executable-world-models.md) | ⏸ precondition | **the feature, not a "game spoke"** — broadly useful (onboard unknown APIs, empirical debugging, simulation-planning, rule discovery). Prax already owns the load-bearing module (sandbox+codegen for propose/execute/repair); gaps are perception helpers + a disambiguating planner. Land with a **non-ARC** demo. Build after the shakedown runs clean |
| **Offline small-model ARC playbook** (program-synthesis+evolution > selective QLoRA-TTT > tiny-direct 2nd attempt > sampling; start from **Soar-qwen-7b** / NVARC Qwen3-4B / TRM, not a generic 14B) | [exec-world-models](executable-world-models.md) | ⏸ precondition | the model step-down ladder lands *here*. Recommended build: **NVARC-derived prediction + SOAR-style Python evolution + selective per-task QLoRA + TRM orthogonal 2nd attempt**. Honest bar: verified offline SOTA ~24% ARC-2 / ~8% ARC-3 — grand prizes are a moonshot for everyone; target a credible 20–30% open entry |
| **ARC-AGI-2 + ARC-AGI-3 benchmark adapters** (thin, on the capability above) | [arc-agi-3](arc-agi-3-schema-harness.md) | ⏸ precondition | adapters live in the eval engine (measurement, not special-casing). `arc_agi_2.py` first (static, deterministic exact-grid-match, keyless-CI-safe) then `arc_agi_3.py` (interactive, RHAE, `ARC_AGI3_ENABLED`, real games in `PRAX_EVAL_DIR`, mock for CI). ARC Prize 2026: $2M pool, offline Kaggle, OSS-required |
| **Self-authored ARC task generators** (seed from **`re-arc`** — Hodel's procedural generators for all 400 ARC-1 training tasks — not from scratch; + ARC-3 mini-games with *known* programs) | [arc-agi-3](arc-agi-3-schema-harness.md) + [exec-world-models](executable-world-models.md) | ⏸ precondition | unlimited, novel, **contamination-free** signal; trains the *general* capability not memorized answers. Public *train* half = dev, public *eval* half = held-out, never trained on. **generator + deterministic verifier = the un-gameable fitness function [#29](../IDEAS_BACKLOG.md) needs**; the proven template is **FunSearch/AlphaEvolve** (frozen LLM + deterministic evaluator + evolutionary population) |
| **Out-transparency the leaders: publish reasoning + induced schema programs** (not just action counts) as a public HF traces dataset | [arc-agi-3](arc-agi-3-schema-harness.md) | 📋 | sub-GB (frames as int-arrays); extends `trace.py`; public-set traces only (hidden eval uncontaminated); in-repo record stays aggregates-only |
| **Reproducibility-artifact bundling** (output + code + env + message history) | [claude-science](claude-science-workbench.md) | 📋 | extends trace records |
| **Traceability reviewer** (numbers/figures/citations trace to source) | [claude-science](claude-science-workbench.md) | 📋 | extends `claim_audit` |
| **World-model verify soundness** (verify induced programs against ground truth / held-out examples / an *independent* check — never self-report; "runs" ≠ "correct") | [lanyon](lanyon-formal-verification.md) + [exec-world-models](executable-world-models.md) | 📋 | design rule for the `prax/reasoning/worldmodel.py` loop — anti-*misformalization*; reproduces-every-example gate, counterexample survival, not "it didn't crash" |
| **Email as a channel** (OSS transport: Postal/Cloudflare) | [agentmail](agentmail-email-as-a-channel.md) | ⏸ | **ship only after** the lethal-trifecta guard — inbound email is a prime injection vector |
| **Mixture-of-Agents hard-task escalation** (`MOA_ENABLED`, default off) | [mixture-of-agents](mixture-of-agents.md) | ⏸ | gate rollout on the HAL `pass_per_1k_tokens` cost axis |

## Memory / learning (needs infra or eval coverage first)

| Item | From | Status | Notes |
|---|---|---|---|
| **Scheduled "sleep phase"** (offline replay → re-distill/prune memories) | [lm-sleep](lm-sleep-consolidation.md) | 💤 | task_runner-shaped; **gated on held-out retrieval-probe evals** (don't build the maximizer before the un-gameable metric) |
| **SEAL → Sleep recipe** for the local-model finetune lane | [lm-sleep](lm-sleep-consolidation.md) + [expert-judgment](expert-judgment-finetune.md) | 💤 | GPU-gated; the recipe to reach for when the finetune lane opens |
| **Learning-curve harness-lift + experience-reuse ablation** | [edge-bench](edge-bench-learning-curves.md) | 📋 | an eval *method*, not the gated task set |
| **Non-stationary / no-reset "does memory actually adapt?" eval** (adaptation/recovery/forgetting, curve-not-point) | [morpheus](skyfall-morpheus-continual-learning.md) | 🔨 | **Banked as a tracked golden** (`memory_adaptation_under_drift.yaml` — single-turn, measures the adaptation *reasoning*); the full multi-config curve-not-point *harness* is still 📋 (same bucket as learning-curves + τ-bench; build the metric before any memory maximizer) |
| **Trace-grading** (score the process — committed/verification/efficiency — not just the answer) | [verify-and-commit](verify-and-commit-discipline.md) | ✅ | `prax/eval/trace_grade.py` + `docs/guides/trace-grading.md`; praxbench Q1 is the first dual-axis consumer |
| **Verify-discipline hint** (`VERIFY_DISCIPLINE_ENABLED`, default off) | [verify-and-commit](verify-and-commit-discipline.md) | 🔨 | `_VERIFY_DISCIPLINE_HINT` in orchestrator; **eval-gate + trace-grade before any flip** (prompt hints have underperformed) |
| **Per-call output cap + forced commitment** (fixes GPQA single-call non-commitment) | [verify-and-commit](verify-and-commit-discipline.md) | 📋 | structural — a between-steps hint can't reach a single runaway call; **truncation risk, needs eval validation** |
| **Verify-once memoization — idempotent reads** (`TOOL_MEMOIZE_ENABLED`, default off) | [verify-and-commit](verify-and-commit-discipline.md) | 🔨 | `IdempotentToolCache` in `loop_middleware.py`; per-turn cache, reads-only (run_python/shell/writes/browser NEVER). Grade with trace-grade before flip |
| **Effectful over-verification** (run_python ×6) — still open | [verify-and-commit](verify-and-commit-discipline.md) | 📋 | can't be safely memoized (side effects); needs purity metadata or prompt-level consolidation — the A/B's actual finding, deliberately NOT force-fixed |
| **Anthropic prompt caching** (`cache_control` on the system prompt) | [opencode-critique](opencode-critique-eval.md) | 📋 | real gap — Claude path caches nothing today. Seam `orchestrator.py:1191`; apply AFTER `prepare_context` (list-vs-string), **verify live cache hits** before flip |
| **Measure cache-hit rate + re-weigh `PROMPT_SELECTIVITY` cross-turn** | [opencode-critique](opencode-critique-eval.md) | 📋 | selectivity (recommended-on) likely defeats cross-turn OpenAI caching; the flag campaign measured per-call tokens, NOT cache reuse — instrument, then A/B on *total* cost |
| **System-prompt ordering: stable-first, volatile-last** (extend cached prefix) | [opencode-critique](opencode-critique-eval.md) | 📋 | hints sit after volatile temporal/memory; reorder is behavior-adjacent → flag+eval |

## The standing structural gaps (tracked in depth elsewhere — pointer, not a re-list)

The [2026 agentic-landscape sweep](agentic-landscape-2026-sweep.md) found the field's only genuine leads over Prax are three, and they remain the highest-priority *structural* work:

1. **Indirect prompt-injection / lethal-trifecta defense + injection evals** — partially shipped (lethal-trifecta guard); the **injection eval coverage** (AgentDojo-style) is still the #1 gap and **gates** the email channel above. Also the OpenClaw security taxonomy self-audit ([nemoclaw](nemoclaw-openclaw.md), 10 vuln classes).
2. **Durable checkpoint-resume** — `CHECKPOINT_BACKEND`/`CHECKPOINT_RESUME_ENABLED` exist (unit-tested); the adopt step is the eval-gated flip.
3. **In-context memory management** (compaction / tool-result clearing) — ongoing.

See that doc's 16-item prioritized punch-list for the full accounting; this tracker deliberately does not duplicate it.

---

**Maintenance rule.** When a research assessment lands an adopt-candidate, add a row here in the same PR (the assessment argues *why*; this row tracks *whether/when*). When an item ships, flip it ✅ and point at the code. A parked (⏸) item names the decision or precondition it waits on, so "parked" never quietly means "forgotten."
