# awesome-evals (BenchFlow) — curated eval bibliography

Reference note on **[benchflow-ai/awesome-evals](https://github.com/benchflow-ai/awesome-evals)** —
a curated, *annotated* bibliography for building & evaluating agents (386+ verified
links, 146 deep notes across 10 sections: why-evals, eval⇄capability⇄RL, harness
decomposition, observability surfaces, eval infra, benchmark integrity, RL
environments, LLM-judge design, agent eval, safety/adversarial).

**Verdict: document (reference) + adopt the *methodology*, not the benchmarks.**
Prax already owns the benchmark-catalog layer; the net-new value is a handful of
**eval-design principles** that sharpen Prax's *existing* golden infrastructure.

## What Prax already covers (so don't re-catalog)

- **Benchmark catalog** — [`prax-benchmarks.md`](prax-benchmarks.md) already lists
  the agentic set awesome-evals points at (GAIA, τ-bench, SWE-bench Verified,
  Terminal-Bench, BFCL, WebArena, TheAgentCompany, AgentDojo, SafeArena, AgentHarm,
  HCAST). Inspect-AI/HAL already recommended in [`harness-engineering.md`](harness-engineering.md).
- **Eval machinery** — rubric + comparator **goldens** (`prax/eval/goldens.py`), the
  decomposed `make eval` judge (grounding/relevancy/correctness), the reference-free
  **nightly live-traffic** eval (`prax_eval_quality`), and the accept-rate governor
  (#22). The "never spike benchmarks" rule already encodes the anti-gaming spirit.

## Net-new adopt-candidates (the methodology — ranked)

1. **"Verifiable beats judgeable" — SHIPPED.** Tasks with deterministic/checkable
   answers enable robust grading *and* RL signal; open-ended LLM-judge tasks stay
   fragile. A golden criterion can now carry a **`verify` regex** scored
   *deterministically* (no LLM) — and a golden whose criteria are all verifiable
   needs **no judge at all** (`goldens.score_golden`). Demonstrated on the STORM
   research golden's `grounding_citations` (a grounded answer must carry a source
   marker — URL / `[n]` / `(Author, 2024)`). Composes with #22's independent accept
   signal. *(Next: lean more new goldens toward `verify` sub-checks where mechanical.)*
2. **Binary per-criterion judging > Likert — SHIPPED.** The judge now scores each
   judged criterion as **binary 0/1** (prompt + a `_binarize` snap at ≥0.5), keeping
   the existing weights — no more fuzzy partial credit (`goldens.score_golden`).
   The judge prompt is also **hardened against impressive-but-vacuous answers** (the
   "totalizing" failure mode) — see
   [`diffuse-ai-control-judge-robustness.md`](diffuse-ai-control-judge-robustness.md).
3. **Calibrate the judge; consider a dedicated evaluator model.** "Align AI to
   human, calibrate human to AI, repeat"; review ≥100 traces before holdout; validate
   against one benevolent-dictator expert. Prax's judge is a general low-tier LLM
   (`build_llm(tier="low", config_key="eval_judge")`) — an open **evaluator model**
   (Prometheus 2 / Atla Selene) is a candidate for more calibrated golden scoring.
4. **Benchmark integrity hygiene.** Holdout/contamination checks (a GSM1k-style
   replica), label-error audits (~3.3% baseline error in famous benchmarks). Cheap
   insurance for the golden/eval set as it grows.
5. **For the eventual RL fine-tuning:** **RewardBench** (evaluate the reward model
   itself) + **verifiers** (Prime Intellect, unified eval/RL package) are directly
   relevant to the GRPO phase ([`diy-document-extraction-model.md`](diy-document-extraction-model.md)
   Phase 2) and the cold-start work — *"if you can verify it, you can train it."*

## Bottom line

A high-quality external **bibliography** worth pointing at, plus **two concrete,
small improvements to Prax's golden infra** (binary judging; bias toward verifiable
checks) and one forward-looking tie-in (RewardBench/verifiers for RL). **Do not**
re-import its benchmark lists — `prax-benchmarks.md` already has them. The single
load-bearing idea: **verifiable > judgeable** — push Prax's evals (and its eventual
RL rewards) toward deterministic checks wherever the task permits.
