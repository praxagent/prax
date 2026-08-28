# Anthropic Frontier Red Team — patterns and problems in multiagent systems

**Source:** [Patterns and problems in multiagent systems](https://www.anthropic.com/research/multiagent-systems)
(Anthropic Frontier Red Team). Multiple Claude generations (Sonnet 4.6/5, Opus
4.6/4.8, Mythos Preview/5) run as autonomous agents on shared resources, with
per-agent VMs, shared forums, self-hosted repositories and arbiter agents.

**Verdict: document + ADOPT one severity correction and one flag decision.**
This is the first source that measures the failure mode Prax's parallel
delegation is structurally exposed to — and it says my own severity assessment
of #64 was wrong.

## The finding that corrects us

> **18 of 30 agents created the identical git branch name `mvp-game-loop`.**
> Job-queue flooding: **2.4M requests, 117 accepted.**

They call this **low-variance behaviour causing systemic failures** —
conformity as a failure mode. Agents drawn from the same model do not collide
*occasionally*; they converge on the same name, the same path, the same action,
**systematically**.

When I filed **#64** (*workspace writes are neither atomic nor
collision-guarded*) on 2026-08-08, I wrote:

> "CLOBBERING. Two parallel spokes writing the SAME filename … Needs the
> orchestrator to hand two tasks the same output path, which is **uncommon**
> but entirely possible."

**That was wrong, and this is the evidence.** `delegate_parallel` spawns
several instances of *the same model* against *one shared workspace*. That is
precisely the configuration measured here: 60% of agents picking an identical
name. Same-model agents share priors, so "what should I call this file?" has a
modal answer, and every spoke reaches for it. Collision is not the tail case —
it is the expected case, and #64 should be re-ranked accordingly.

The torn-write hazard I ranked *first* (one writer plus any reader) is still
real. The clobbering hazard I ranked *second* is far more likely than I said.

## The measurement worth keeping: diversity beats volume

> Coordinated swarms found **266 vulnerabilities over 27M tokens**; independent
> agents found **21 over 6.5M tokens**. **Only 12 were shared.**

Two readings, and the second matters more. Per token, coordinated is ~9.9
findings/M against ~3.2 — roughly 3× more efficient. But the **overlap of 12**
says coordination changed *what was found*, not merely how much. Independent
agents were not a weaker version of the swarm; they were searching elsewhere.

That is the strongest available argument for the *multi-modal sweep* pattern —
several searchers each blind to the others' angle — and against assuming a
single well-resourced agent covers the same ground.

## Orthogonality: capability does not buy coordination

> *"Models more capable in execution are not necessarily more coordinated, and
> can take forceful actions more quickly."*

This decouples two axes we have been treating as one. It also lands on a live
decision: the model ladder changed on 2026-08-09, and the
[capability suite](crush-charm-coding-agent.md) measures *execution*
capability only. Nothing in our eval matrix measures whether a model coordinates
or escalates — and this says the two do not move together.

Not a reason to revert anything. A reason to stop reading a capability score as
a general safety signal.

## The flag decision

`EPISTEMIC_VIGILANCE_ENABLED` is **default off** (`settings.py:781`). It
appends a principle instructing Prax to verify a user's factual/health/safety
*premise* before accepting it, rather than accommodating it.

Their epistemic results are adjacent but **not the same test**: routing
accuracy with 50% lying *sources* (Mythos 5 ~0.85, Sonnet 0.62) and a hidden-
profile task (85% against 17–36%). That measures resistance to **deceptive
peer agents**, whereas our flag concerns **a user's false premise**. Related
failure family — miscalibrated trust — different input.

So this is **not** evidence to flip the flag, and per
[harness-delta attribution](harness-delta-attribution.md) a paper is not
evidence for a flip anyway; a measured A/B is. What it *does* justify is
raising the priority of actually running that A/B, since we have a built,
unmeasured mechanism aimed at a failure family a frontier red team says is real
and *"nothing suggests will fix themselves"*.

Note the flag's own description already names its grader: the `sycophancy`
benchmark adapter. The experiment is specified; it has just never been run.

## Where Prax is structurally better off, and where it is not

Their stated limitation is that agents have *"no reputation to lose, no court
to appeal to"* — no social technology to make coordination stick.

Prax's MCP server is the place this bites: it exposes tools to **foreign
agents**. And there, Prax does have the missing machinery — per-caller
identity from the token, a per-caller tool allowlist, HIGH never grantable,
governance in front of every call. That is the "court", and it exists because
the trust boundary was drawn deliberately rather than because agents behave.

Where Prax is *not* better off: **inside** a turn, its own parallel spokes have
no coordination mechanism at all — no shared context
([DeLM](decentralized-shared-context.md)), no write coordination (#64), and now
a measured reason to expect them to collide.

## Honest limits

A company blog post about that company's own models, and the newest model
(Mythos 5) wins every comparison reported — the same first-party caution
applied to [ExtractBench](extractbench.md). The scenarios (fantasy game
development, turf wars, job queues) are constructed, and none resembles a
personal-assistant turn. No dataset or harness is linked in what I read, so
none of it is reproducible here.

The conformity result is the one I would still credit even discounting for
source, because it is a *negative* result about their own models and the
mechanism is obvious: same weights, same priors, same modal answer.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Re-rank #64 — clobbering is likely, not uncommon.** Same-model spokes converge on the same names (18/30 picked one branch name) | ✅ severity corrected on the task |
| **Run the `EPISTEMIC_VIGILANCE_ENABLED` A/B** with the `sycophancy` adapter the flag already names — a built, unmeasured mechanism aimed at a real failure family | 📋 queued |
| **Stop reading capability scores as coordination/safety signals** — measured orthogonality | ✅ recorded; affects how model-ladder results are described |
| Arbiter agents, forums, per-agent VMs, the swarm architecture | ❌ declined — Prax is hub-and-spoke by choice; see [DeLM](decentralized-shared-context.md) for why the bottleneck argument does not apply at our scale |
