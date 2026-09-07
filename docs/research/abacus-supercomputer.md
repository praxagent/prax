# Abacus AI SuperComputer — always-on agent hosting at $10/month — assessment

**Source:** [supercomputer.abacus.ai](https://supercomputer.abacus.ai/) +
[SuperComputer FAQ](https://abacus.ai/teams/supercomputer_faq) +
[Abacus help docs](https://abacus.ai/help/chatllm-ai-super-assistant/supercomputer),
by **Abacus.AI**. Commercial, closed. Assessed 2026-09-03 from a link the maintainer
dropped (`/ltf` is a campaign token, not a product name — it is defined nowhere on
the page).

**Calibration first, because it changes how much weight this deserves: this is a
pricing page, not a paper.** No benchmarks, no architecture, no technical claim to
evaluate, and every number below is vendor marketing that nobody here has verified
by running it. This is a **build-vs-buy note filed in the research index**, not a
research assessment, and it should be read at that confidence.

**Verdict: document — don't adopt for the daily driver.** The spec is genuinely
attractive and the trust story is genuinely wrong for what Prax's live box holds.
**Bank one market signal** (always-on agent hosting is being commoditised, so
hosting is not a moat) and **one conditional adopt** (a disposable eval box, where
there is no secret and no user data to leak).

## What it is

A managed always-on Linux instance aimed at people who want to host an agent
without running infrastructure:

- **2 vCPU, 8 GB RAM, persistent disk**, Ubuntu, **root**, SSH from a local machine,
  browser-based shell.
- S3-style object storage, a database, **inbound HTTPS**.
- Bundled: ChatLLM Teams, the Abacus DeepAgent, and "100+ AI models."
- AWS + GitHub integration; one-click deploy of open-source models and agents.
- **$7 first month, then $10/month.** No higher tier is published.
- Stated posture: "SOC 2 compliance," "dedicated, isolated instance and is not
  shared with other users," "encrypted at rest and in transit."

## The number that matters

**2 vCPU / 8 GB is precisely the Lightsail box's spec** — the machine that runs
Prax's real Discord/SMS/TeamWork traffic — at roughly a quarter of the price. That
is the whole reason this link is worth reading, and it is why the rest of this
assessment has to be careful rather than dismissive.

## Three reasons to say no anyway

**1. It does not break the GPU wall.** The help docs mention "optional GPU access,"
but heavy training routes to the broader Abacus.AI platform, not to the instance.
The $10 tier is CPU-only. This is the **seventh sighting** of the same wall
([rlm-harness-lid](rlm-harness-lid.md), [lm-sleep](lm-sleep-consolidation.md),
[arts](arts-agentic-tree-search.md), [acm](acm-agentic-context-management.md),
[sdft](sdft-continual-learning.md), [aide²](aide2-recursive-self-improvement.md)),
and it would be very easy to misread a cheap "SuperComputer" as the unlock those
assessments keep deferring. It is not. Nothing in the someday-GPU finetune lane
moves because of this.

**2. The FAQ is silent on exactly the fields that decide it.** Stated: SOC 2,
isolation, encryption. **Not stated anywhere:** whether Abacus staff can access the
instance, backup policy, data retention, uptime SLA, bandwidth limits, fair-use or
abuse terms. For a general-purpose VPS that silence is ordinary. For the box that
holds real user conversations, git-backed per-user workspaces, and **the secrets
proxy with the real provider keys**, "who can read this disk" is not a footnote —
it is the question.

**3. It inverts the thesis.** Prax's differentiator is that a compromised Prax has
nothing to steal: keyless operation, keys held by a separate isolated service. That
argument is about *the host boundary*. Moving the host onto a vendor whose access
policy is unstated does not weaken the secrets proxy's design — it relocates
the suite review's **perimeter-only trust** root-cause finding
one layer down, from the application to the infrastructure, where Prax has no
instrumentation at all. Same objection applies to the bundled "100+ AI models" as an
OpenRouter substitute: it only fits the architecture behind the proxy, which removes
the convenience that made it attractive.

If the actual goal is cutting the Lightsail bill, a plain VPS at comparable cost
gets the saving without the ambiguity, and keeps the trust boundary where the
architecture already assumes it is.

## What's genuinely worth taking

**The market signal, not the product.** Always-on agent hosting — a persistent box,
root, SSH, inbound HTTPS, storage, an agent already on it — is now a **$10/month
commodity**. Prax's live deployment is that shape, hand-built: systemd units, a
Lightsail instance, a tailnet, and `deploy/update.sh`. Two readings, both worth
holding at once:

- **Validation** — the "an agent needs a persistent home" premise is real enough
  that it is being productised and priced.
- **Warning** — the hosting layer is *not* a moat and will not become one. Prax's
  defensible surface has to stay the governance / eval / trust layer: governed
  tools, the capability gateway, the eval engine, the honest verification ledger.
  Nothing in this product touches any of those, which is the most useful thing it
  tells us.

This is a different category from the other commercial-convergence sightings —
[Capy](capy-swe-agent-platform.md) and Buzz (assessed in the TeamWork repo,
`teamwork/docs/comparisons/buzz.md`) are rivals
*at* the agent/workspace layer; this one sits *underneath* the agent entirely.

## The one conditional adopt

**A disposable third box for eval runs — where there is no secret and no user
data.** This is the single place the cheap spec is straightforwardly attractive:

- It extends the containment that already exists. The two-machine split (dev box vs.
  Lightsail daily driver) was created precisely because benchmark runs were writing
  into TJ's real workspace through the sandbox's fixed bind-mount. A third machine
  that holds *nothing worth leaking* is the same reasoning applied once more.
- Eval runs are **API-bound, not CPU-bound** — the work is waiting on model calls —
  so 2 vCPU is plausibly adequate. The Lightsail box runs the entire stack on the
  same spec, which is the existence proof.
- The trust objection largely evaporates: a box with no real keys (proxy token only,
  or a scoped throwaway), no user workspaces, and re-fetchable public datasets has
  little to steal.

**Conditions before spending anything:** the gated/held-out eval data must never
land on it (the contamination firewall in `prax-evals` / `prax-eval-battery` is
non-negotiable and outranks the convenience), and it needs a live trial before
belief — "SOC 2 + isolated" is a claim, and per the house rule an unverified
integration gets a
[`VERIFICATION_LEDGER`](../VERIFICATION_LEDGER.md) row, not an assumption.

## Declined

- **Moving the daily driver.** Reasons 2 and 3 above.
- **The bundled model gateway** as an OpenRouter substitute — architecturally it
  only works behind the secrets proxy, at which point it buys nothing.
- **DeepAgent / ChatLLM** as anything to learn from here. They may be worth their own
  assessment someday; this page says nothing evaluable about them.
- **Reading "SuperComputer" as compute relief.** It is a 2-vCPU VPS with a
  confident name.
