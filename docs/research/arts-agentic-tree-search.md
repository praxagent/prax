# ARTS (UCSB) — agentic tree search for automated discovery

**Assessed:** 2026-07-14 (TJ dropped the link: "document this and see if we can improve Prax with it").
**Source:** [github.com/UCSB-AI/arts](https://github.com/UCSB-AI/arts) (MIT) · paper *"Learning the ARTS of Search for Automated Discovery"* ([arXiv 2606.21891](https://arxiv.org/abs/2606.21891), Juneja/Jain/Nathani/W. Wang/X. Wang — UCSB + Mila/Université de Montréal) · [project page](https://automating-discovery.github.io/arts/).

**Verdict: document-don't-adopt the system; extract one pattern.** ARTS is a GPU-bound automated-ML-**research** agent whose headline mechanism (test-time GRPO training of an open "scientist" model) is structurally incompatible with Prax's hosted-LLM harness — Prax orchestrates models it does not train. The one genuinely transferable idea is **failure-provenance diagnosis**: before retrying, decide whether a failure was a *bad hypothesis* or a *bad implementation*. That maps onto self-regeneration [#29](../IDEAS_BACKLOG.md) / orchestrator replan and onto the eval engine's "don't penalize a correct plan for a transient execution error" lens. Nothing to import (and the GPU/flash-attn stack makes code reuse impractical regardless).

---

## What ARTS is

**ARTS = Agentic Reasoning for Tree Search**, aimed at automated scientific / ML-research discovery — framed as "an iterative search over the space of hypotheses and experiments." It operates on **ML-engineering tasks** (train/tune models, write experiment code) evaluated in MLGym + MLEBench environments — *not* general agent tool-use or RAG. Two stated pain points with prior MCTS-style discovery agents (AI-Scientist-v2 / AIRA):
1. Heuristic search **conflates hypothesis merit with execution quality** — a good idea with rough code gets ranked below a mediocre idea with polished code.
2. Prior methods **prune search logs** when accumulated history overflows the context window, discarding useful signal.

**Method (a two-model split):**
- A **"scientist"** (a reasoning LLM — the paper tests o3, and a trained Qwen3-4B) inspects the experiment tree, prior logs, code, and training curves, **diagnoses whether a failure came from a bad hypothesis or a bad implementation**, and issues high-level directions. It writes no code.
- An **"executor"** (a code model) turns directions into code, runs it in containers, reports scores.
- **Test-time training (TTT) — the headline.** When search history outgrows the context window, ARTS **folds the tree into the scientist's weights via GRPO** (using `prime-rl`) rather than pruning logs. This is an inference-time RL weight-update on GPU during the search.
- Reward = task scores from executed experiments; an ablation (`..._nosignal.sh`) isolates the score signal's contribution.

**Self-reported results** (across 22 MLGym + MLEBench tasks): >15.3% relative improvement in normalized score over leading algorithms (AIRA/MCTS, MLEvolve, LLM-guided); with TTT, a Qwen3-4B scientist matches Gemini-3-Pro / o3-reasoning at up to 5× lower inference cost, and rediscovers a human-best recurrent-memory solution that heuristic search prunes away.

## Evidence quality — weak; treat as a preprint claim

Single-commit code drop (created 2026-05-28, entire codebase landed 2026-06-24), **~20 stars, no independent reproduction, not peer-reviewed.** The project's own surfaces disagree on the numbers (abstract "22 tasks / 15.3%"; README/project-page summaries variously "19 tasks", "16 of 22") — used the abstract as authoritative, but the inconsistency is itself a signal. License is clean **MIT**, so reuse would be legally fine — but the practical barrier is the hard **Linux + NVIDIA GPU + flash-attn + prime-rl + MLGym** stack. This is a GPU research harness, not a library.

## Why direct adoption is a no (the house constraints that bind)

- **Keyless CI**: ARTS needs GPU + flash-attn + prime-rl + API keys to do anything — it cannot run in Prax's key-free `make ci`.
- **Hosted-model harness**: ARTS's core value is *weight-updating an open model mid-search*. Prax's reasoning models are hosted APIs it does not train; the finetune spoke is a separate GPU-gated LoRA lane for a *local* model, not the orchestrator's brain (same wall the [lm-sleep](lm-sleep-consolidation.md) TTT idea hit).
- **Un-gameable eval / never-spike-benchmarks**: ARTS's whole objective is climbing MLGym/MLEBench scores via score-driven tree search — a benchmark-maximizer loop. That is the ethos Prax's "never encode an eval example / abstract the problem class" rule exists to resist. Any borrowed idea must be abstracted to a problem class, never wired to a scoreboard (cf. [aide2](aide2-recursive-self-improvement.md), which taught the same lesson from the *good* side: the fitness function is the product).
- **The "wall"**: Prax's `agent_plan` is deliberately ephemeral and shallow; adopting MCTS/tree-search-over-hypotheses would be a large architectural departure against that design, not a drop-in.

## The one transferable pattern: failure-provenance diagnosis

ARTS's scientist step — **"was this failure a bad plan or a bad implementation?" before deciding what to retry** — is a genuinely good idea that needs none of the GPU machinery. It's the useful, abstractable core, and it lands in two places Prax already has:

1. **Orchestrator replan / self-regeneration #29.** Today Prax's recursion-thrash handling escalates tier and retries (auto-tier-escalation) or fails gracefully — it does not first *classify* the failure. A reflexion-style check ("is the plan wrong, or did a tool just transiently fail?") could route bad-plan failures to a *replan* and bad-execution failures to a *retry* — a more surgical version of the escalate-and-retry loop, and exactly the discrimination #29's "notice → diagnose → propose" step wants. Would ship as a system-prompt / spoke-internal heuristic, flag-gated default-off, abstracted as a general failure-classification rule — never the tree-search or GRPO.
2. **Eval scoring lens.** ARTS's "separate hypothesis merit from execution quality" is a real insight for grading agent attempts: a correct plan that hit a transient execution error shouldn't score the same as a wrong plan. Worth keeping in mind for the goldens' rubric design (and it composes with the public/private split — you want the *held-out* signal to reward the right thing, not punish flaky execution).

Neither is a build recommendation today; both are parked ideas with a concrete home, tracked in the adopt-tracker.

## Honest caveats

- Evidence is weak/self-reported (see above) — cite nothing as established fact.
- A few mechanism details (the exact scientist=o3 / executor split, "verbalized sampling") came from project-page/README summaries, corroborated by the code dirs (`reflexion.py`, `ttt/`, `search/`, `prime_rl_*.toml`) but not read line-by-line in the paper body — confirm against the paper before quoting specifics.
- MIT license makes reuse legal; the GPU/GRPO stack makes it impractical.

## Sources
- [github.com/UCSB-AI/arts](https://github.com/UCSB-AI/arts) · [arXiv 2606.21891](https://arxiv.org/abs/2606.21891) · [project page](https://automating-discovery.github.io/arts/)
