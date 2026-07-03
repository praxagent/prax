# AutoResearch / labless.dev — the self-improvement loop Prax is already building toward

[← Research](README.md)

Reference note on **[labless.dev](https://labless.dev/)** ("Live collaborative
autoresearch for nano projects") and the pattern it productizes,
**[Karpathy's AutoResearch](https://github.com/karpathy/autoresearch)** (AI agents
autonomously improving a single-GPU nanochat training run overnight).

**Verdict: document + adopt the *loop pattern* — it is the concrete mechanism for
Prax's self-regeneration direction — but NOT the product or the nanochat target.**
The sharper finding: Prax has spent this whole arc building the **safety
preconditions** that make such a loop trustworthy. The loop is the next step once
the fitness function is un-gameable.

## What it is

**AutoResearch** is a *hill-climbing algorithm for knowledge work*: an agent
edits a training file, launches a **fixed ~5-minute experiment**, **measures the
result against one clear metric**, **keeps or discards** the change, and repeats —
overnight, unattended — driven by instructions in a **`program.md`** file rather
than manual iteration. Deliberately "nano": one GPU, one file, one metric, so the
scope stays tractable for an agent to iterate on. It is *narrow* — autonomous code
improvement against a fixed measurable objective, not a general coding agent.

**labless.dev** wraps that loop in a **live-collaborative** surface — multiple
humans (and/or agents) watching and steering the autonomous research as it runs,
scoped to small "nano projects." (The site is early/thin; the substance is the
AutoResearch loop underneath.)

## Why this is directly relevant to Prax

This is not a new idea to chase — it's the **named, validated shape** of three
things already in motion:

| AutoResearch element | Prax equivalent (already shipped / in-progress) |
|---|---|
| Hill-climb against a fixed metric | The new **capability / harness-lift / GAIA** eval suites are the metric |
| Run a fixed experiment, keep/discard | **self_improve_*** tools (start/deploy/rollback) + git-backed workspace |
| Overnight, unattended, small/local setup | The **resumable batch runner** + CPU/ds4 overnight execution built this session |
| `program.md` instruction file | The orchestrator's `agent_plan` + system-prompt steering |
| Edit→train→measure on a real model | **fine-tune service** (LoRA/Unsloth) + vLLM rails |
| Live collaborative watching/steering | **TeamWork** "watch-the-work" (execution graph, terminal/browser screencast, graded autonomy) |

So Prax has the **substrate** (edit + experiment + measure + keep/discard + watch).
What AutoResearch adds is the **explicit outer loop** that ties them into
autonomous, metric-driven iteration — which is exactly
[`prax-self-regeneration`](../IDEAS_BACKLOG.md) (#29): recursive self-improvement
of the harness.

## The load-bearing insight — Prax built the safety first

An AutoResearch loop is a **benchmark maximizer**, and its dominant failure mode is
**reward-hacking / overfitting the eval** (spike the number, don't get better). The
[`CLAUDE.md`](../../CLAUDE.md) "**never spike benchmarks**" rule names this exact
risk. Everything Prax has been hardening is the precondition that makes the loop
*safe to close*:

- **Deterministic / verifiable grading** (`verify` regex, GAIA exact-match,
  capability checks) — a metric you can't sweet-talk.
- **maker ≠ checker** — the high-tier supervising auditor
  ([`diffuse-ai-control-judge-robustness.md`](diffuse-ai-control-judge-robustness.md))
  that vetoes impressive-but-vacuous wins.
- **"Abstraction, not example"** — fixes must generalize the problem class, so a
  loop can't memorize the eval set.
- **Independent accept signal** — cost-per-accepted-change loop health
  ([`loops-explained`](../IDEAS_BACKLOG.md) #22).

The self-regeneration memory already says it: *"an un-gameable fitness function is
the precondition; the eval-hardening work IS the RSI substrate."* AutoResearch is
the external confirmation that the loop on top is the right next move.

## Transferable seeds (prioritized)

1. **Adopt the explicit overnight hill-climb loop (HIGH)** — wrap the eval suites
   as the fitness function: propose a harness/prompt change → run
   `eval-harness-lift` (or a capability subset) → keep iff it improves *and* the
   auditor doesn't veto *and* it's an abstraction → else discard. Resumable batch +
   CPU/local already make it overnight-able. This operationalizes #29.
2. **A `program.md`-style charter (MEDIUM)** — a reviewable instruction file
   scoping what the self-improvement loop may touch and how it scores itself
   (composes with graded-autonomy gates).
3. **"Nano" discipline (MEDIUM)** — keep each experiment small, fixed-budget, and
   single-metric so a weak/local model can drive it (matches the
   [`evals-world-class-initiative`](../IDEAS_BACKLOG.md) CPU-first stance).

## Document-don't-adopt

- **labless.dev the product** — an early collaborative SaaS for nanochat-style ML
  research; Prax's autoresearch target is **its own harness**, not LLM pre-training,
  and TeamWork already provides the collaborative watch/steer surface.
- **The nanochat training target** — Prax improves scaffolding (routing, tools,
  grounding, recovery), measured by agentic evals; raw model training is out of scope.

## Sources

- [labless.dev](https://labless.dev/) · [karpathy/autoresearch](https://github.com/karpathy/autoresearch)
- [DataCamp guide](https://www.datacamp.com/tutorial/guide-to-autoresearch) · [freeCodeCamp walkthrough](https://www.freecodecamp.org/news/build-an-ai-agent-that-runs-its-own-llm-experiments-with-autoresearch)
- Related Prax notes: [grounding](grounding.md) · [error-metacognition](error-metacognition.md) · [diffuse-ai-control-judge-robustness](diffuse-ai-control-judge-robustness.md) · [prax-benchmarks](prax-benchmarks.md)
