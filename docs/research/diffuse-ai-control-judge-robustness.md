# Judge robustness on fuzzy tasks (Diffuse AI Control)

Reference note on **"Diffuse AI Control on Fuzzy Tasks"** (Terekhov, Gulcehre,
Hebbar, Benton — [arXiv 2606.08892](https://arxiv.org/abs/2606.08892),
[Anthropic Alignment companion](https://alignment.anthropic.com/2026/diffuse-ai-control/)).
Read **for the evaluation-robustness method, not the AI-control threat model.**

## Two layers — only one is Prax's

- **The AI-control / safety thesis (out of scope for Prax).** The paper's frame is
  an *untrusted, misaligned* model that deliberately **sandbags** — produces work
  that scores well under a weak overseer but is actually poor, accumulating harm
  over a long deployment ("diffuse" threat). Prax does **not** run an adversarial
  agent it must police; it evaluates *its own* outputs. So the blue-team/red-team
  control framing doesn't map onto Prax's threat model — document, don't adopt.
- **The LLM-judge-robustness finding (genuinely Prax-relevant).** Strip the
  adversary and the result stands on its own: **weak LLM judges on fuzzy/subjective
  tasks are systematically gameable by impressive-but-vacuous outputs**, and a
  *hardened scoring prompt* resists it. That is exactly Prax's situation.

## Why this is Prax's situation [verified]

- After the `verify` work ([`awesome-evals.md`](awesome-evals.md)), Prax's
  *mechanical* golden criteria are scored deterministically — but the
  **irreducibly judgeable** ones (research quality, proactive judgment, skill
  capture) can't be, and those are precisely the paper's "fuzzy tasks."
- **Prax's golden judge is a LOW-tier model** (`build_llm(tier="low",
  config_key="eval_judge")` in `prax/eval/goldens.py`) — the paper's "weak scorer,"
  which it shows is the *most* exploitable.
- The paper's named attack, **"totalizing"** — sweeping unjustified claims dressed
  in borrowed vocabulary, looking rigorous through pattern-matching — is a failure
  mode LLM judges actively *reward*. A weak judge will hand a 1 to confident vacuity.

## What was adopted (shipped)

The paper's defensive-prompt findings, generalized (no task-specific spiking — the
abstraction rule), folded into the binary judge prompt (`_SCORE_PROMPT`,
`prax/eval/goldens.py`):

- **Score on substance, not polish.** Sweeping claims / borrowed jargon / templated
  structure with no specific, concrete, verifiable content → score 0.
- **Reward task-specific specificity** — concrete evidence, named sources,
  implementable detail; penalize generic confident assertions.
- **"If you can't point to the span that satisfies it, it isn't satisfied."**

This pairs with the two prior eval upgrades: deterministic `verify` where the
criterion is mechanical, a *hardened* binary judge where it's irreducibly fuzzy.

**Supervising auditor (also shipped).** Beyond hardening the *weak* judge's prompt,
a high-tier **auditor** now re-checks only the criteria the cheap judge *passed* —
the gaming direction — and may veto (1→0): the paper's strong-vs-weak scorer made
concrete as maker≠checker on the judge itself. Opt-in via `EVAL_AUDITOR_ENABLED`
(eval-time only). See `goldens.score_golden(audit=…)` and IDEAS_BACKLOG #26 for the
follow-ons (cross-provider auditor, "gameable criterion" signals). It's a **better
proxy, not ground truth** — `verify` + #22 remain the floor.

## What was NOT adopted (and why)

The paper's best prompts came from an **adversarial optimization loop against a
ground-truth scorer** — which Prax lacks for its fuzzy goldens (the whole reason
they're judged, not verified). So Prax takes the **static defensive hardening**,
not the red/blue optimization machinery. Honest limitation, same as the paper's own.

## Bottom line

A safety paper whose *method* — not its threat model — upgrades Prax's eval path:
weak LLM judges reward impressive vacuity on fuzzy tasks, so the judge prompt is now
hardened to grade substance over polish. Complements `verify` (deterministic where
possible) and #22 (an independent accept signal, because even a hardened judge
isn't ground truth).
