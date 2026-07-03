# Replicating expert judgment (Thinking Machines) — why Prax can't do this *yet*

[← Research](README.md)

Reference note on **[Learning to Replicate Expert Judgment in Financial Tasks](https://thinkingmachines.ai/news/learning-to-replicate-expert-judgment-in-financial-tasks/)**
(Thinking Machines Lab). Read for what it says about Prax's **fine-tune spoke +
eval discipline**.

**Verdict: track (fine-tune-spoke advancement) + adopt ONE principle now (a
disagreement-driven data-curation loop). Prax has the substrate but not the
recipe — a fine-tuning-sophistication gap, not a fundamental one.**

## What it is

Frontier models (GPT-5.5, Claude Opus 4.8, Gemini 3.1) hit only **~78%** on
financial document-triage tasks (below the ~80% "workplace trust" bar). Thinking
Machines **fine-tuned Qwen3-235B** to **84.7%** at **~13.8× cheaper** inference —
"29.8% fewer mistakes than the best frontier model." The thesis: **custom models on
high-quality expert-labeled data beat frontier prompting**, because prompting can't
articulate *tacit* expert judgment. Two parts matter:

- **The training recipe** (GRPO 73.5% → 84.7%): *interleaved (round-robin) task
  batching* (+12.1%), *CISPO loss with asymmetric clipping* (+10.1%), and
  *on-policy distillation* toward **teacher checkpoints promoted only when
  validation hits a new high**, penalizing student drift from the teacher (+3.1%).
- **The data-curation loop** (the real crux): vendor **non-expert labels failed**.
  So they trained on them, found examples where the **model's prediction diverged
  from the label**, sent *those* to expert investors to re-judge, and cleaned the
  set. Disagreement → expert relabel → better data.

## Why can't Prax do this *yet* — the honest answer

Not a capability ceiling — a **fine-tuning-sophistication + data-pipeline gap**:

| Needed | Prax today |
|---|---|
| Advanced recipe (interleaved batching, CISPO, on-policy distillation w/ teacher promotion) | The fine-tune spoke does **MVP LoRA SFT** (Unsloth, ~60 steps) — no RL/distillation recipe |
| Expert-disagreement data-curation loop | Has the **failure journal + trajectory export + feedback loop**, but no *disagreement→relabel* curation |
| 235B-scale training compute | **CPU / cheap-first**; the fine-tune targets **Qwen3-8B** (`FINETUNE_BASE_MODEL`) |

But the **substrate is already Prax's**: the fine-tune spoke, the **Qwen3 backend**
(literally `LOCAL_MODEL=Qwen/Qwen3-8B`), the eval goldens/capability suite, and the
maker≠checker discipline. And the **methodology is the same shape as the
self-regeneration loop** — improve against a fitness signal — just applied to
**weights** instead of the **scaffold**. (The landscape sweep already tracks this
as **SEAL**, gated on catastrophic forgetting.) So "Prax replicating expert
judgment" is an *investment in the fine-tune recipe + a curation loop*, not a
re-architecture.

## Adopt NOW — the disagreement-driven data-curation loop

The result hinged on **data quality, not model size** (non-expert labels *failed*;
cleaning them made the difference). That's a principle Prax can use **today** to
harden the un-gameable fitness function the self-regen loop depends on:

> Use the **maker≠checker auditor as the disagreement detector**: where the
> model's answer and a golden's label/verifier diverge, that example is a
> *candidate mislabel* — surface it for human re-judgment instead of trusting the
> label. Clean the goldens the way they cleaned the training set.

This directly improves eval-golden label quality (composes with the
[awesome-evals](awesome-evals.md) "label-error hygiene" note and the self-regen
overseer), and it's the same "verify against a trusted signal, escalate
disagreements" pattern as the just-shipped honesty guard.

## Track — the fine-tune-spoke recipe + "differentiated intelligence"

If Prax pursues weights-level improvement (per SEAL / #14): the recipe here —
interleaved batching, CISPO, on-policy distillation with new-high teacher promotion
— is a concrete reference for upgrading the fine-tune spoke beyond LoRA SFT. And
the headline (**custom small model on tacit-judgment data beats frontier prompting,
cheaper**) validates Prax's **local-model + fine-tune** direction and the
"personal SLM" idea from [Personal.ai](agentic-landscape-2026-sweep.md) — with the
honest ordering: **retrieval-grounded memory first, per-user fine-tunes later.**

## Sources

- [Learning to Replicate Expert Judgment in Financial Tasks (Thinking Machines)](https://thinkingmachines.ai/news/learning-to-replicate-expert-judgment-in-financial-tasks/)
- Related: [agentic-landscape-2026-sweep](agentic-landscape-2026-sweep.md) (SEAL / Personal.ai) · [awesome-evals](awesome-evals.md) · [learning-to-theorize](learning-to-theorize.md)
