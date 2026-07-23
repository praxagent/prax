# Capy (Scrapybara) — cloud multi-agent SWE platform — assessment

**Source:** [capy.ai](https://capy.ai/) — "The best AI software engineer / the IDE for
the parallel age," built by **Scrapybara, Inc.** (San Francisco, YC-backed). Commercial,
closed platform. Claims 50k+ engineers, customers incl. Trigger.dev / Exa / RunPod;
Pro $20–2,000/mo + enterprise.
**Verdict:** **Document — don't adopt the product** (closed, commercial, coding-vertical,
swarm-parallelism = a deliberate Prax non-goal). It's a well-executed *commercial* point
in the same design space as Prax, so most of it **reinforces directions Prax already
shares** (specialized-agent orchestration, agentic PR review, model routing, sandbox
isolation). **Bank one concrete adopt-candidate:** *coding-agent eval coverage*
(Terminal-Bench / SWE-bench) — Capy competes on it, Prax's eval matrix lacks it, and Prax
already owns the sandbox to run it. That's the "option-3 marquee bench" the maintainer
already wanted, with a name attached.

## What Capy is

A cloud platform that orchestrates a **fleet** of cloud coding agents inside the tools
teams already use:
- **Three specialized agents:** *Captain* (orchestrates multi-step tasks), *Build*
  (writes code, opens PRs), *Review* (auto-reviews every PR).
- **Per-task isolation at scale:** each feature/bug/refactor task gets its **own branch +
  sandboxed VM** (Docker, snapshotting), so "hundreds of parallel agents" run
  independently. This is Scrapybara's own computer-use/VM infra as the substrate.
- **Meet-devs-where-they-are:** native GitHub / Slack / Linear integration + a native
  PR-review UI ("never context-switch to GitHub again"), REST API, cron/trigger
  automation, Sentry/GitHub import.
- **Model-agnostic routing ("Flex"):** routes each request to the right model across
  GPT/Claude/Gemini to cut cost while holding quality; BYOK / on-prem; SOC2, SSO/SAML,
  audit logging.
- Competes publicly on **Terminal-Bench** (long-horizon terminal/coding tasks).

## The honest comparison to Prax

Capy is what a **commercial, coding-vertical** take on Prax's architecture looks like.
Point by point:

| Capy | Prax equivalent | Read |
|---|---|---|
| Captain / Build / Review specialized agents | orchestrator + spokes (hub-and-spoke) | **convergent** — same shape Prax already runs |
| Review agent on every PR | `/code-review` (+ `ultra`), the solo-dev agentic-review flow | **convergent** — Prax already treats review-as-agent as *the* review |
| Model-agnostic "Flex" routing | model tiers + difficulty routing + Thompson-sampling per component + cross-provider failover ([reliable-agentic-systems](reliable-agentic-systems-bayer.md)) | **convergent** — Prax already has this, arguably deeper |
| Per-task branch + sandboxed VM, **hundreds parallel** | per-user git workspaces + one persistent sandbox container; **bounded** concurrency (~4 sub-agents) | **deliberate divergence** — see below |
| Native GitHub/Slack/Linear, "never context-switch" | its own **TeamWork** shared workspace + Discord/SMS multi-channel | **strategic contrast** — worth noting |
| Scrapybara computer-use/browser VMs | `prax-sandbox` (own browser/desktop/computer-use container) | **document-don't-adopt** — Prax owns the analog |

**The deliberate non-goal (same as [matrix.build](matrix-autonomous-company.md)):**
"hundreds of parallel agents" / swarm throughput is exactly the shape Prax's reliability
thesis rejects — tool-selection accuracy collapses past ~50 tools and sub-agent returns
diminish hard past ~4 concurrent (the README's key takeaways). Prax bounds parallelism on
purpose; Capy sells unbounded parallelism as the headline. Don't chase it — but note Capy
is a *coding-only* vertical where per-task isolation makes wide parallelism safer than it
is for a general agent touching shared user state.

## What's genuinely worth taking

1. **Coding-agent eval coverage — Terminal-Bench / SWE-bench (the concrete adopt).** Capy
   markets on Terminal-Bench; Prax's [eval matrix](../guides/eval-matrix.md) has 15
   benchmarks but **no coding-agent bench** — the sandbox-dependent "marquee" tier the
   maintainer flagged as the next eval gap. Prax already has the sandbox to run real
   repo-level tasks, so a **`terminal_bench` and/or `swe_bench_verified` adapter**
   (sandbox-scored, deterministic pass/fail on the hidden tests) drops into the existing
   adapter seam. This is the highest-value takeaway — it's measurement, not imitation, and
   it's un-spikeable by construction. 📋 (folds into the existing SWE-bench-lite intent).
2. **"Review agent on every PR" as a first-class surface.** Prax does agentic review via
   `/code-review`, but Capy makes it an *always-on* pipeline step. Low-lift idea: an
   opt-in review pass the solo-dev flow can auto-invoke on a diff (Prax's PR template
   already reminds you to run it). Convergent, worth formalizing. 📋 small.
3. **The "meet users in their existing tools" lesson — as a documented tension, not an
   adopt.** Capy's whole wedge is *zero context-switch* (live in GitHub/Slack/Linear).
   Prax bets the other way — a **shared human+agent workspace** (TeamWork) plus
   multi-channel (Discord/SMS). Both are defensible; Capy is evidence that the
   integration surface *is* the product for the coding vertical. Keep Prax's TeamWork
   thesis, but let this sharpen the "why our own workspace vs. living in their tools"
   argument. Reference, not a build.

## Bottom line

A polished commercial validation that Prax's core architecture (specialized-agent
orchestration + agentic review + model routing + sandbox isolation) is the right shape —
Capy productizes the same ideas for cloud software engineering. The un-adoptable parts are
the closed platform, the coding-only vertical, the Scrapybara VM infra Prax already
mirrors, and the swarm-parallelism Prax deliberately bounds. **The one thing to actually
take is a coding-agent benchmark (Terminal-Bench / SWE-bench) into the eval matrix** — it
closes Prax's most visible eval gap and Prax uniquely already has the sandbox to run it.
Complements [matrix.build](matrix-autonomous-company.md) (swarm = non-goal) and
[sierra](sierra-agent-platform.md) (commercial-agent-platform lessons); the benchmark
adopt sits alongside the [ARC](arc-agi-3-schema-harness.md) / executable-world-models eval
lane. Caveat: signal is a marketing site (Terminal-Bench "superior" claim is unverified
vendor marketing — treat as a *pointer to the benchmark*, not evidence of ranking).
