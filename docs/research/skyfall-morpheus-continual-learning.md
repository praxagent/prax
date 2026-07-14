# Skyfall MORPHEUS — "LLMs are not continual learners" — the eval-side attack on Prax's memory claim

**Assessed:** 2026-07-14 (TJ dropped the link asking what to learn/adopt).
**Source:** [skyfall.ai/blog/llms-are-not-continual-learners](https://skyfall.ai/blog/llms-are-not-continual-learners) — **the primary post returns HTTP 403 to automated fetch and was not read directly.** This assessment is reconstructed from fetchable secondaries dated 2026-07-13/14, all mutually consistent: [MarkTechPost](https://www.marktechpost.com/2026/07/13/skyfall-ai-releases-morpheus-a-persistent-enterprise-simulation-benchmark-that-makes-continual-reinforcement-learning-necessary-under-structured-non-stationarity/), [The AI Journal](https://aijourn.com/skyfall-ais-morpheus-benchmark-reveals-llms-arent-actually-learning/), the [morpheus.skyfall.ai](https://morpheus.skyfall.ai) project page, and the whitepaper on OpenReview (`forum?id=31P1VAfLkJ`, not fetched). **Numbers/quotes below are secondhand — verify against the whitepaper before quoting as fact.**

**Verdict: document-don't-adopt the mechanism; bank one sharp eval-design lesson.** Like [lm-sleep](lm-sleep-consolidation.md), this attacks continual learning from the *parametric* side — a lane Prax deliberately doesn't occupy — so there is nothing to import. But it hands Prax a genuinely useful caution about its *own* honesty: **stable eval scores under changing conditions can be an illusion of adaptation** ("coverage, not learning"), and the only honest test of whether Prax's non-parametric memory *actually* adapts is a **non-stationary, no-reset, learning-curve eval** — not absolute task score. That's a methodology hook, tracked, not a build.

---

## What it actually argues (narrower than the title)

The headline overreaches; the real claim is sharp and defensible: **frontier LLMs do not genuinely adapt to changing conditions — their apparent stability under change is pre-training coverage, not learning.** The load-bearing line (via the trade write-ups): *"Stable absolute performance across configuration intervals is not evidence of robustness. It is evidence of a fixed policy operating within its coverage boundary."* The tested notion of "continual learning" is **continual reinforcement learning (CRL)** — an agent adapting its *policy* to a non-stationary environment over a long, un-reset horizon. It is a **parametric / RL-agent** claim; it says nothing about in-context learning, RAG, or harness-memory adaptation. A casual reader taking "LLM agents can't improve over time" from the title would be over-reading it.

**MORPHEUS** is the benchmark carrying the argument: a persistent enterprise-ops simulator built on the "Big World Hypothesis," with three properties engineered to defeat any fixed policy — **persistence** (decisions compound, no episode resets), **non-stationarity** (a failure-injection engine at 5/8/15/30% rates + a configuration-shift controller that changes regimes at fixed timestamps), and **operational complexity** (no optimal fixed policy exists). It measures six things beyond reward: **adaptation speed, forgetting, recovery time, stability, performance gap.**

Reported findings (secondhand): GPT-5.5 averaged ~0.918 on task 1 then **collapsed to zero reward** on a drift task; Gemini 3.1 Pro ~0.864 but converged on a fixed heuristic. Four named failure modes — **False Resilience** (scores stable only because conditions stayed in-distribution), **Context-Window Limitation** (couldn't detect change once the signal ran past its memory window), **Fixed Heuristics** (didn't learn from the reward signal at all), **Opaque Failures** (collapses with no interpretable degradation, unlike classical RL). Notably the **classical RL baselines (PPO/HER/EWC) also failed** in later regimes — so the benchmark is hard for purpose-built RL too, which cuts against pure cherry-picking of LLMs.

## Who Skyfall is / what this is

Skyfall.ai is an enterprise-AI company (founders from the **Maluuba** team, Microsoft-acquired) building "Enterprise World Models" for autonomous operations; commercially early. **The post is a genuine research artifact wrapped in a strategic marketing frame** — the benchmark, code, metrics, and whitepaper are real and substantive, but the "frontier LLMs fail → you need continual RL / world models" narrative is precisely Skyfall's product thesis. Read it as research-grade evidence advanced in service of a vendor position. (The negative result on classical RL baselines is the part that most argues it isn't just a hit piece.)

## Why there's no mechanism to adopt

Prax's "learning" is **non-parametric**: Qdrant vectors + Neo4j concept graph, `progress_read`/`progress_append`, `trace_search`, experience-reuse, skill capture — around a **frozen hosted model** Prax does not train. MORPHEUS's entire construction is about a *fixed policy* (= a frozen model) inevitably failing under drift, with adaptation expected to live *in the weights* via RL. That is the lane Prax deliberately does not occupy (the GPU-gated finetune spoke is default-off and a separate local-model concern, same as in [lm-sleep](lm-sleep-consolidation.md)). So the benchmark cannot be run "against Prax" as-is, and its RL recipes are irrelevant to a harness that trains nothing. Prax's answer to non-stationarity is **external state the harness controls** — which MORPHEUS neither models nor tests.

## The one lesson worth banking: don't mistake coverage for adaptation

This is the honest sting, and it's aimed at Prax's *own* claims, not the frontier models'. Prax asserts its memory stack lets it improve across sessions. MORPHEUS is a clean argument that **a flat/stable score under changing conditions can be a fixed heuristic coasting inside its coverage boundary, not evidence of learning.** To actually *prove* Prax's non-parametric memory adapts — rather than the underlying model coasting on pre-training — an eval must:

- introduce **structured non-stationarity with no resets** (the world changes mid-run and stays changed), and
- measure **adaptation speed / recovery / forgetting**, not just absolute task score — the delta and the recovery curve are the signal, a high flat line is not.

This is the same shape [edge-bench](edge-bench-learning-curves.md) gave Prax (a learning-*curve* metric; experience-reuse-beats-resampling), and it complements [lm-sleep](lm-sleep-consolidation.md): MORPHEUS is the *problem statement* ("frozen policies can't adapt") to which Prax's design is the rebuttal ("so put adaptation in harness memory, not the frozen policy") — but only if Prax can *demonstrate* the memory adapts under drift. Concretely, this belongs in the same eval-methodology bucket as the τ-bench multi-turn and learning-curve items: **long-horizon, non-stationary, no-reset, curve-not-point** golden(s) that would catch a "false resilience" result in Prax itself. Not a build now — a tracked eval-design principle, because building the maximizer before the honest metric is the exact trap the self-regen safety rules warn against.

## Honest caveats

- **Primary source unread (403)** — all specifics are secondhand; the OpenReview whitepaper is authoritative and was not fetched. Verify the numbers (0.918/0.864, the 5/8/15/30% rates, the six metrics) before quoting.
- **Vendor-pitch overlay is real** — MORPHEUS is the evidentiary spearhead for Skyfall's commercial thesis; the "frontier LLMs fail" framing is directionally motivated (mitigated by the classical-RL baselines also failing).
- **Scope is narrow** — one enterprise-ops simulator, two evaluated tasks, two live environments (ERP/manufacturing "planned"), released 2026-07-13, no independent replication yet. It does not license the broad "LLM agents can't improve over time" reading.

## Sources
- [skyfall.ai](https://skyfall.ai/) (primary blog 403) · [MarkTechPost](https://www.marktechpost.com/2026/07/13/skyfall-ai-releases-morpheus-a-persistent-enterprise-simulation-benchmark-that-makes-continual-reinforcement-learning-necessary-under-structured-non-stationarity/) · [The AI Journal](https://aijourn.com/skyfall-ais-morpheus-benchmark-reveals-llms-arent-actually-learning/) · [morpheus.skyfall.ai](https://morpheus.skyfall.ai) (+ OpenReview `31P1VAfLkJ`)
