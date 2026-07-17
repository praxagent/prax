# External eval review (July 2026) — verdict, and what we changed

**Source.** An independent GPT-5.6-Sol-Pro deep-research review of Prax's first
benchmark shakedown (2026-07-17). Following the house pattern, this records the
verdict honestly and what we adopted. Companion to
[prax-benchmarks.md](prax-benchmarks.md) and
[benchmark-scan-2026-adopt.md](benchmark-scan-2026-adopt.md).

## The verdict (which we accept)

> **Respectable as an internal smoke test; NOT sufficient for a strong July-2026
> agentic-harness claim.** GSM8K + HumanEval are saturated; TruthfulQA is
> variant-ambiguous; only MMLU-Pro / GPQA-Diamond / MATH-500 still carry closed-book
> signal — and those read as "strong 2024–early-2025 frontier," not July-2026 SOTA.
> Sample sizes (40/benchmark; 5/5 HumanEval) are too small for ranking claims. And
> these are **closed-book capability probes, not agent evaluations** — they say
> nothing about tool use, planning, memory, safety, or efficiency.

This is correct, and it largely **validates our own framing**: we ran these as a
*shakedown*, on the *cheapest* model (DeepSeek-V4-Flash), at 40-case slices, for
cost control — never as headline numbers. The review's own caveat ("if cheap,
single-shot, no hidden test-time compute, the judgment improves") is our case.

**The deepest point — closed-book ≠ agentic — is exactly why ARC-AGI-3 is our
flagship** (interactive, exploration-plus-planning, world-model, memory). The
critique reinforces the pivot rather than redirecting it.

## What we changed (this PR)

**Statistical honesty**
- **Wilson 95% CIs on every pass rate** (`prax/eval/stats.py`) — e.g. `80.0%
  (n=40, 95% CI 65.2–89.5%)`. Behaves at extremes (40/40 → [91.2%, 100%], not
  [100%, 100%]). The single most defensible fix to the small-sample critique.
- **Seeded random subsets** (`PRAX_EVAL_SAMPLE_SEED`) instead of first-N — several
  source sets are ordered (MMLU-Pro by category, MATH by topic), so first-N was a
  *biased* slice. Now representative + reproducible; vary the seed for variance.

**Reporting discipline** (the review's checklist)
- Every summary carries a **`protocol`** block: task **variant** + scoring rule,
  **attempts** (pass@1/pass@2), real-vs-seed dataset, sampling seed — plus the
  existing **config snapshot** (model / all flags / git commit / cost / tokens).
  TruthfulQA is now explicitly labelled **MC1** (the review's specific complaint).

**Coverage** (the two biggest missing axes, as deterministic keyless adapters)
- **`longcontext`** — synthetic needle-in-haystack (MRCR-style), multiple context
  lengths so the summary shows *degradation with length*.
- **`agentsafety`** — harmful-request refusal (AgentHarm-style), deterministic
  refusal proxy. Complements existing `injecagent` (injection) + `sycophancy`.

## Coverage matrix — where Prax stands vs the review's asks

| Axis | Have (runnable adapters) | Missing / deferred |
|---|---|---|
| Closed-book reasoning | gsm8k, mmlu_pro, gpqa, math, truthfulqa | — (de-emphasize gsm8k as headline) |
| Instruction / tool-calling | ifeval, **bfcl** | τ-bench (multi-turn Pass@k) |
| Factuality / hallucination | simpleqa, halueval | FACTS |
| Injection / adversarial | **injecagent**, sycophancy | AgentDojo at scale |
| **Safety (harmful refusal)** | **agentsafety** *(new)* | Agent-SafetyBench, JailbreakBench |
| **Long-context / memory** | **longcontext** *(new)* | LongMemEval, MRCR-v2 (real) |
| End-to-end tool use | **GAIA** (`eval-gaia`) | WebArena, WorkArena, AppWorld |
| Software engineering | humaneval *(saturated)* | **SWE-bench Verified**, LiveCodeBench, Terminal-Bench |
| Efficiency | cost/token per run (tracked) | success-under-budget curves |

**Deferred honestly:** the heavy end-to-end agentic benchmarks (SWE-bench,
WebArena, WorkArena, AppWorld) need real environments (repos, browsers, app
sandboxes) + real compute we don't currently budget for. GAIA is our agentic
anchor; the rest are tracked, not run. On a constrained budget the right allocation
is **rigor now (cheap) + the agentic proof via ARC** (in progress), not a full HELM
battery.

## Honest positioning (the claim we will and won't make)

- ✅ *"The base model isn't weak and the harness runs end-to-end, deterministically
  scored, with per-run reproducibility."*
- ❌ *"Prax is a strong agent"* — not established by closed-book probes. That claim
  is earned on GAIA + ARC + the tool-use/safety/memory axes, at real sample sizes.

The reporting discipline here doubles as **ARC-writeup infrastructure** — the ARC-2
Grand Prize + Paper Track reward *completeness* and *rigor* on identical criteria.

## Takeaways

1. **Report the interval, not just the point** — small subsets demand CIs.
2. **Sample randomly + seed it** — first-N of an ordered dataset is biased.
3. **Disclose the protocol** — variant, attempts, dataset, model, flags, commit.
4. **Closed-book ≠ agentic** — the agentic proof is ARC + GAIA, not more quizzes.
5. **Spend rigor cheaply; spend compute on the flagship** — don't polish the wrong
   axis on a tight budget.
