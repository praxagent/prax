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
| **RSI ladder framing** (delegation→net-positive→ignition→inflection) | [aide2](aide2-recursive-self-improvement.md) | ✅ | in the aide2 doc + #29 |
| **Disagreement-driven golden curation** (auditor↔judge disagreement = mislabel) | [expert-judgment](expert-judgment-finetune.md) | ✅ | `run_golden_curation` in `prax/eval/goldens.py` |
| **Supervising auditor** (high-tier re-checks the cheap judge's passes) | [diffuse-ai-control](diffuse-ai-control-judge-robustness.md) | ✅ | `score_golden(audit=…)`, `EVAL_AUDITOR_ENABLED` (#26) |
| **Substance-over-polish hardened judge prompt** | [diffuse-ai-control](diffuse-ai-control-judge-robustness.md) | ✅ | `_SCORE_PROMPT` |
| **Binary per-criterion judging (not Likert)** | [edge-bench](edge-bench-learning-curves.md) | ✅ | `_binarize` in `score_golden` |
| **"Verifiable beats judgeable"** (deterministic regex criteria) | [edge-bench](edge-bench-learning-curves.md) | ✅ | `RubricCriterion.verify` |
| **P1 plugin micro-loop** (notice→propose→isolate→verify→canary→rollback) | [autoresearch](autoresearch-labless.md) + [aide2](aide2-recursive-self-improvement.md) | ⏸ TJ | #29 P1 — de-risked by aide2 evidence; ~3–5 days; **gate ready after the split lands** |

## Capability candidates

| Item | From | Status | Notes |
|---|---|---|---|
| **Lean toolchain in sandbox + governed `lean_check`** (+ axiom-audit trust gate) | [cdc-lean](cdc-lean-teach-prax-lean.md) | ⏸ TJ | branch `feat/lean-check-tool` exists (empty); toolchain-only ~1–2 GB, flag `LEAN_TOOLS_ENABLED`. **Decision pending: build now vs doc-only** |
| **Lean eval adapter** (miniF2F/PutnamBench; keyless seed + sandbox-scored halves) | [cdc-lean](cdc-lean-teach-prax-lean.md) | ⏸ | after `lean_check`; datasets stay in `PRAX_EVAL_DIR` |
| **Persona/user-simulator multi-turn evals, deterministic Pass@k** (τ-bench/τ²-bench) | [sierra](sierra-agent-platform.md) | 📋 | the biggest gap vs the single-turn capability suite; CPU-cheap (deterministic state grading) |
| **Reproducibility-artifact bundling** (output + code + env + message history) | [claude-science](claude-science-workbench.md) | 📋 | extends trace records |
| **Traceability reviewer** (numbers/figures/citations trace to source) | [claude-science](claude-science-workbench.md) | 📋 | extends `claim_audit` |
| **Email as a channel** (OSS transport: Postal/Cloudflare) | [agentmail](agentmail-email-as-a-channel.md) | ⏸ | **ship only after** the lethal-trifecta guard — inbound email is a prime injection vector |
| **Mixture-of-Agents hard-task escalation** (`MOA_ENABLED`, default off) | [mixture-of-agents](mixture-of-agents.md) | ⏸ | gate rollout on the HAL `pass_per_1k_tokens` cost axis |

## Memory / learning (needs infra or eval coverage first)

| Item | From | Status | Notes |
|---|---|---|---|
| **Scheduled "sleep phase"** (offline replay → re-distill/prune memories) | [lm-sleep](lm-sleep-consolidation.md) | 💤 | task_runner-shaped; **gated on held-out retrieval-probe evals** (don't build the maximizer before the un-gameable metric) |
| **SEAL → Sleep recipe** for the local-model finetune lane | [lm-sleep](lm-sleep-consolidation.md) + [expert-judgment](expert-judgment-finetune.md) | 💤 | GPU-gated; the recipe to reach for when the finetune lane opens |
| **Learning-curve harness-lift + experience-reuse ablation** | [edge-bench](edge-bench-learning-curves.md) | 📋 | an eval *method*, not the gated task set |

## The standing structural gaps (tracked in depth elsewhere — pointer, not a re-list)

The [2026 agentic-landscape sweep](agentic-landscape-2026-sweep.md) found the field's only genuine leads over Prax are three, and they remain the highest-priority *structural* work:

1. **Indirect prompt-injection / lethal-trifecta defense + injection evals** — partially shipped (lethal-trifecta guard); the **injection eval coverage** (AgentDojo-style) is still the #1 gap and **gates** the email channel above. Also the OpenClaw security taxonomy self-audit ([nemoclaw](nemoclaw-openclaw.md), 10 vuln classes).
2. **Durable checkpoint-resume** — `CHECKPOINT_BACKEND`/`CHECKPOINT_RESUME_ENABLED` exist (unit-tested); the adopt step is the eval-gated flip.
3. **In-context memory management** (compaction / tool-result clearing) — ongoing.

See that doc's 16-item prioritized punch-list for the full accounting; this tracker deliberately does not duplicate it.

---

**Maintenance rule.** When a research assessment lands an adopt-candidate, add a row here in the same PR (the assessment argues *why*; this row tracks *whether/when*). When an item ships, flip it ✅ and point at the code. A parked (⏸) item names the decision or precondition it waits on, so "parked" never quietly means "forgotten."
