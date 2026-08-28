# deepseek-harness — a model vendor ships a harness, and it has no governance layer

**Source:** <https://github.com/deepseek-ai/deepseek-harness> (`dsh`) — ~36.8k
stars, 2.8k forks, ~12,293 commits, MIT, TypeScript/Node. Built on **Cordis**,
a plugin framework whose design paper is *"A Programming Paradigm for
Spatiotemporal Composability"*. Self-described: **"Everything is a Plugin."**
Explicitly a **developer preview** with *"THERE WILL BE COMPATIBILITY-BREAKING
CHANGES."*

**Verdict: document-don't-adopt — but this is the most useful competitive data
point in weeks, because of what it *doesn't* have.**

## The finding: no governance layer

The README describes plugins, composition, a web UI on `127.0.0.1:3080`, and a
build stack. On permissions, sandboxing, approval workflows, capability
ceilings, audit, or any safety mechanism: **nothing.**

I want to be precise about the strength of that claim, because absence of
evidence in a preview README is not proof of absence in the codebase. What I
can say is that a 36.8k-star general agent platform from a frontier lab does
not consider governance worth mentioning in its front-page description, which
is itself the signal. Compare Prax, where `governed_tool.py` wraps *every*
tool with a risk tier and an audit record, plugins run behind a capability
gateway with trust tiers, and the lethal-trifecta guard exists as a named
component.

This is the third independent measurement of the same thing. The
[Agent Harness survey](agent-harness-engineering-survey.md) found **Observability
and Governance thinnest in open source** (O: 15, G: 14 projects, against L: 47)
and mostly living inside commercial platforms.
[Crush](crush-charm-coding-agent.md) ships `--yolo`, a single flag that voids
its whole permission perimeter. Now the largest new entrant ships without a
governance story at all.

**Prax's differentiator is not a claim we make about ourselves — it is a gap
other people keep leaving.** That is worth knowing precisely because it is
uncomfortable to rely on: a differentiator nobody else wants may be a
differentiator nobody else *needs*. The honest reading is that governance costs
effort and buys nothing on a benchmark, so it loses every time the scoreboard
is the goal — which is exactly why it has to be a thesis rather than a feature.

## Why the timing matters

The live box moved its `low`/`base` tier to `deepseek/deepseek-v4-flash-0731`
on 2026-08-09. So DeepSeek now supplies both a model Prax runs and a competing
harness to run it in.

That makes them a **vendor harness** in the sense
[Niklaus measured](crush-charm-coding-agent.md): every model-vendor harness in
that comparison **dropped** on the small model (Codex 2nd→9th, Claude Code
3rd→7th, Qwen Code 4th→6th) while model-agnostic ones climbed. If that pattern
holds, `dsh` is optimised around DeepSeek's own models and would be the wrong
thing to benchmark Prax against — and equally, Prax's model-agnostic shape is
the side of that split that travelled well.

Unverified and worth stating: nobody has run `dsh` against anything, ourselves
included. There are **no benchmarked claims in the repo**.

## What "everything is a plugin" costs

Prax already has a plugin system, deliberately narrower: plugins declare
capabilities, run behind a gateway, and `permissions.md` is a
framework-enforced ceiling. The difference is not architectural taste, it is
where the trust boundary sits. "Everything is a plugin" makes the *core*
extensible; Prax's version makes the *periphery* extensible while keeping the
core — governance, routing, memory — non-pluggable on purpose.

A system where everything is replaceable has nothing that cannot be replaced,
including the parts that constrain it. For a chatbot framework lineage (Cordis
comes from that world) that is the right call. For a harness whose thesis is
that the scaffolding is what makes an agent trustworthy, it is not.

## Not adopted, and why not each

- **Cordis / spatiotemporal composability** — the paper is referenced but the
  concept is not explained in the preview material, so I am not going to
  summarise it from its title. If someone reads the paper and it names a real
  capability, reassess then.
- **The plugin-everything architecture** — declined above on trust-boundary
  grounds, not on quality.
- **TypeScript/Node stack** — irrelevant; Prax is Python and that is settled.
- **`dsh` as a benchmark target** — declined for now. Comparing harnesses is
  genuinely valuable ([harness_lift](crush-charm-coding-agent.md) is the
  instrument nobody else computes), but a developer preview warning of
  compatibility-breaking changes is a moving target, and the comparison would
  measure their preview rather than their design.

## The one thing worth watching

If `dsh` later adds a governance layer, that is a signal the market decided
safety scaffolding matters, and Prax's head start becomes worth something
commercially rather than only ethically. If it ships 1.0 without one and keeps
growing, that is evidence the market does not price it — which does not make
the thesis wrong, but does mean it has to be justified on its merits rather
than on adoption.

Either outcome is informative. Neither is a reason to change course now.

## Adopt-tracker rows

| Item | Status |
|---|---|
| Governance-gap observation — 3rd independent measurement (harness survey, Crush `--yolo`, now `dsh`) | ✅ recorded; no work, sharpens positioning |
| Cordis / spatiotemporal composability | ⏸ parked — concept not explained in available material; do not summarise from a title |
| `dsh` as a harness-lift comparison target | ⏸ parked — developer preview, moving target |
| Plugin-everything architecture | ❌ declined — a system where everything is replaceable can replace the parts that constrain it |
