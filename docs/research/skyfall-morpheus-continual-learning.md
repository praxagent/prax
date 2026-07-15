# Skyfall MORPHEUS — "LLMs are not continual learners" — the eval-side attack on Prax's memory claim

**Assessed:** 2026-07-14 (TJ dropped the link asking what to learn/adopt; the primary post was 403-blocked to automated fetch, so TJ pasted the full text — numbers below are from the **primary blog**).
**Source:** [skyfall.ai/blog/llms-are-not-continual-learners](https://skyfall.ai/blog/llms-are-not-continual-learners) (primary, read in full) + the MORPHEUS project page ([morpheus.skyfall.ai](https://morpheus.skyfall.ai)) and OpenReview whitepaper (`forum?id=31P1VAfLkJ`).

**Verdict: document-don't-adopt the mechanism; bank one sharp eval-design lesson.** Like [lm-sleep](lm-sleep-consolidation.md), this attacks continual learning from the *parametric / RL* side — a lane Prax deliberately doesn't occupy — so there's nothing to import. But it hands Prax a genuinely useful caution about its *own* honesty: **stable eval scores under changing conditions can be an illusion of adaptation** ("coverage, not learning"), and the only honest test of whether Prax's non-parametric memory *actually* adapts is a **non-stationary, no-reset, curve-not-point eval** measuring adaptation/recovery/forgetting — not absolute task score. That's a tracked methodology hook, not a build.

---

## What it actually argues (narrower than the title)

The headline overreaches; the real claim is sharp and defensible: **frontier LLMs are fixed policies with large pre-training distributions — when that distribution covers the environment they perform competitively, and when it doesn't they fail in volatile, unattributable ways.** The tested notion of "continual learning" is **continual reinforcement learning (CRL)**: an agent adapting its *policy* to a non-stationary environment over a long, un-reset horizon. It is a **parametric / RL-agent** claim; it says nothing about in-context learning, RAG, or harness-memory adaptation. A casual reader taking "LLM agents can't improve over time" from the title would be over-reading it.

**MORPHEUS** carries the argument: a persistent enterprise-ops simulator grounded in the "Big World Hypothesis," where the world never resets, past decisions compound, objectives shift via structured configuration changes, and consequences are delayed by several simulated days. Two tasks (Dynamic Resource Allocation; Scheduling Under Drift) × two environments (process-outbound, process-inbound) = four conditions, 5 seeds, models used **off-the-shelf, no fine-tuning**, prompted with full world state + logs each step.

**The four findings (primary numbers):**
1. **Stable score = coverage, not adaptation.** Task 1 raw reward held flat — Gemini 3.1 Pro 0.899/0.853/0.852 (**0.864** overall), GPT-5.5 0.920/0.920/0.914 (**0.918** overall), adaptation-speed 1.0 for both. The flatness means *the same fixed heuristic* scored adequately across intervals, i.e. the configs stayed inside pre-training coverage — not that anything was learned.
2. **Context-window bottleneck on harder tasks.** On the longer-consequence-chain *inbound* task, GPT-5.5 showed detection lag **>25 steps** at the second configuration shift and **never recovered** — the change-signal fell outside its effective context.
3. **Pre-trained heuristics, not reward-optimal strategies.** GPT-5.5 diversifies (~2.11 queues), Gemini concentrates (~1.34) — different pre-training priors, neither derived from the reward signal; performance gaps to the optimum persist (never closed) because nothing optimises against the reward.
4. **Opaque, unattributable failures.** On Task 2, GPT-5.5 inbound repeatedly **collapses to 0 reward** with no recoverable pattern; Gemini outbound falls **21%**, Gemini inbound **collapses 95%**. With frozen weights there's no "forgetting" to measure — context overflow, pre-training gap, and reward misalignment all produce identical-looking curves.

**The starkest summary:** on Task 1, LLMs leave **8–13%** of available reward unrealised (looks like tolerable suboptimality); on Task 2, **93–98%** unrealised — categorical failure.

## The honest read on the RL contrast (this is where the framing matters)

The blog's whole force comes from the RL comparison, and it must be read carefully: **PPO, the *weakest* RL baseline, closes 99.3% of the gap on Task 1 and 99.96% on Task 2, from random initialisation, learning purely from the reward signal** (the best CRL method roughly halves PPO's adaptation time, 21.8 vs 45.2 steps). So the RL baselines **succeed dramatically where the LLMs fail** — RL is the positive contrast, not a co-failure. (An earlier draft of this note, reconstructed from secondary coverage, wrongly said the RL baselines "also fail"; the primary corrects that.)

That contrast is real, but it is also **not apples-to-apples, and it is precisely Skyfall's product thesis**: a frozen model given *no learning signal* is compared against methods *trained from scratch on the environment's own reward*. Of course the reward-trained method closes the reward gap. The blog is candid that "the model capacity difference is significant" and frames competitive LLM performance as the finding — but the setup structurally favors the conclusion Skyfall sells (continual RL / enterprise world models). Read MORPHEUS as a **genuine, well-instrumented benchmark advanced in service of a vendor position**: the negative result on LLMs is real and the metric suite is substantive; the "therefore you need continual RL" is the pitch.

## Why there's no mechanism to adopt

Prax's "learning" is **non-parametric**: Qdrant vectors + Neo4j concept graph, `progress_read`/`progress_append`, `trace_search`, experience-reuse, skill capture — around a **frozen hosted model** Prax does not train. MORPHEUS's entire construction assumes adaptation must live *in the weights* via RL, and its whole point is that a *fixed policy* (= a frozen model) inevitably fails under drift. That is the lane Prax deliberately does not occupy (the GPU-gated finetune spoke is default-off, a separate local-model concern — same as [lm-sleep](lm-sleep-consolidation.md)). So the benchmark can't be run "against Prax" as-is, and its RL recipes are irrelevant to a harness that trains nothing. Prax's answer to non-stationarity is **external state the harness controls** — which MORPHEUS neither models nor tests.

## The one lesson worth banking: don't mistake coverage for adaptation

This is the honest sting, aimed at Prax's *own* claims. Prax asserts its memory stack lets it improve across sessions. MORPHEUS is a clean argument that **a flat/stable score under changing conditions can be a fixed heuristic coasting inside its coverage boundary, not evidence of learning** — and, crucially, that adaptation metrics computed on a fixed-weight system are "formally computable but mechanistically meaningless." Applied to Prax, this says: to *prove* the non-parametric memory adapts (rather than the underlying model coasting on pre-training), an eval must

- introduce **structured non-stationarity with no resets** (the world changes mid-run and stays changed), and
- measure **adaptation speed / recovery / forgetting** of the *harness-controlled state* (does retrieval/reuse actually change behaviour?), not just absolute task score — the delta and recovery curve are the signal; a high flat line is not.

That is the same shape [edge-bench](edge-bench-learning-curves.md) gave Prax (a learning-*curve* metric; experience-reuse-beats-resampling), and it complements [lm-sleep](lm-sleep-consolidation.md): MORPHEUS is the *problem statement* ("frozen policies can't adapt") to which Prax's design is the rebuttal ("put adaptation in harness memory, not the frozen policy") — but the rebuttal only holds if Prax can *demonstrate* the memory adapts under drift. It belongs in the same eval-methodology bucket as the τ-bench multi-turn and learning-curve items: a **long-horizon, non-stationary, no-reset, curve-not-point** golden that would catch a "false resilience" result *in Prax itself*. Not a build now — a tracked eval-design principle, because building the maximizer before the honest metric is the exact trap the self-regen safety rules warn against.

## Honest caveats

- **Vendor-pitch overlay is real and the RL contrast is not apples-to-apples** (frozen, untrained LLM vs reward-trained RL) — the negative LLM result is genuine, but the "you need continual RL" conclusion is the product thesis, and the comparison is structured to reach it.
- **Scope is narrow** — one enterprise-ops simulator, two tasks, two live environments (ERP/manufacturing "planned"), released 2026-07-13, no independent replication yet. It does not license the broad "LLM agents can't improve over time" reading.
- **Provenance note:** the primary blog is hard-blocked to automated fetch (403); this assessment's numbers come from the full text the maintainer supplied, cross-checked against the project page. The OpenReview whitepaper remains the most authoritative artifact for methodology details.

## Sources
- [skyfall.ai/blog/llms-are-not-continual-learners](https://skyfall.ai/blog/llms-are-not-continual-learners) (primary) · [morpheus.skyfall.ai](https://morpheus.skyfall.ai) (+ OpenReview `31P1VAfLkJ`)
