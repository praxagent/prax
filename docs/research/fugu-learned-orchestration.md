# Fugu — learned model orchestration (Sakana AI)

Reference note. **Verdict: document, don't adopt** — but unusually close to home:
Fugu is the *trained-router* version of what Prax already is (a hub-and-spoke
orchestrator over a multi-vendor model pool). The product is a closed API (adopting
it would nest Prax under Fugu and re-create the single-vendor lock-in it claims to
solve); the **value is the two papers + one validated direction + a concrete gap it
surfaced** (now partly addressed — see "What this shipped").

- Product: [Sakana Fugu beta](https://sakana.ai/fugu-beta/) ([release](https://sakana.ai/fugu-release/)) — multi-agent orchestration as a foundation model; OpenAI-compatible API; Fugu Mini (speed) / Fugu Ultra (hard tasks); routes across OpenAI/Google/Anthropic.
- Papers (ICLR 2026): **Conductor** — [Learning to Orchestrate Agents in Natural Language](https://sakana.ai/learning-to-orchestrate/); **Trinity** — lightweight evolutionary coordinator.
- Coverage: [VentureBeat — how Sakana trained a 7B model to orchestrate GPT-5 / Claude Sonnet 4 / Gemini 2.5 Pro](https://venturebeat.com/orchestration/how-sakana-trained-a-7b-model-to-orchestrate-gpt-5-claude-sonnet-4-and-gemini-2-5-pro).

## What it is

A language model **trained to orchestrate other models** rather than answer directly:

- **Conductor** — a **7B model trained with RL** that, per turn, designs the
  *communication topology* between worker agents and *prompt-engineers* focused
  instructions to each worker to exploit their individual strengths. Hit SOTA on
  GPQA-Diamond and LiveCodeBench by orchestrating frontier LLMs instead of solving
  the problem itself.
- **Trinity** — a **tiny coordinator (<20k learnable params, trained by a
  derivative-free evolutionary algorithm)** that assigns a pool of *frozen* LLMs to
  three roles — **Thinker / Worker / Verifier** — over multiple turns. No worker
  weights change; the only learned thing is the routing/role policy.
- **Fugu (product)** = these, hardened into a closed, OpenAI-compatible routing API.
  Pitch: provider-independence — route across vendors, hedge single-vendor and
  **export-control** risk.

## The motivating real-world event (why provider-independence isn't abstract)

On **2026-06-12** a US export-control directive led Anthropic to suspend access to
its **Fable 5 and Mythos 5** frontier models for all users
([anthropic.com/news/fable-mythos-access](https://www.anthropic.com/news/fable-mythos-access)).
Fugu's own page notes those models "aren't in the agent pool" — i.e. it had to
**drop a frontier model from its pool overnight** and route around it. So a top model
can become *permanently unavailable by government action*, not just rate-limited.
That is the concrete case provider-independence exists for. (It's also why I shouldn't
have dismissed "Fable 5 / Mythos" as garbled names — they post-date the Jan-2026
knowledge cutoff; verify, don't assume.)

## Where it maps in Prax (verified)

| Axis | Prax today | Fugu |
|---|---|---|
| Hub-and-spoke orchestration | ✅ `orchestrator.py` + spokes via `delegate_*` | ✅ |
| **How it delegates** | **Heuristic** — orchestrator LLM tool-choice over a fixed spoke/category map (`subagent.py`) | **Learned** (RL policy) |
| Provider-independence | ✅ **shipped** cross-provider failover, flag-gated (`llm_fallback.py`, `_maybe_failover`) + now terminal-failure **denylist + user notice** | ✅ (its headline) |
| Learned model routing | ⚠️ **coded but dormant** — Thompson bandit `tier_bandit.py` not wired (selection/outcome uncalled); difficulty estimator's value discarded | ✅ the whole point |

## The value, ranked

1. **Strong external validation** of Prax's architecture (hub-and-spoke + multi-vendor
   + provider-independence). Sakana commercialized exactly this shape.
2. **Two reference-worthy techniques** — the frontier of Prax's own routing research:
   - **Trinity** is the more interesting for Prax: a *cheap* (tiny, evolutionary,
     zero worker-fine-tuning) **role-coordinator** (Thinker/Worker/Verifier) over a
     **frozen** pool — which maps onto Prax's existing diverse-reviewer /
     `multi_model_query` / verifier patterns. A learnable routing layer over the
     tier/provider pool Prax already has, *without* training the workers.
   - **Conductor** = the RL-trained-delegation direction Prax's heuristic
     tool-choice would evolve toward (designs topology + per-worker prompts).
3. **It surfaced a concrete latent gap** (like the [ARD note](agentic-resource-discovery.md)
   surfaced two security gaps): Prax's learned-routing scaffolding (`tier_bandit.py`)
   is fully coded but unwired, and `model-routing.md` overstated it. Fixed: the doc
   now states the real (dormant) state.

## What this assessment shipped

Reviewing Fugu's export-control framing exposed a real hole in Prax's failover: it
was tuned for **transient** errors (rate-limit / 5xx / overload) and would
*optimistically reset to the primary every turn* — so a **permanently** removed model
(revoked key, unpaid bill, export pull) gets re-hit forever and the user is never told
why. Now addressed (flag-gated under `LLM_FALLBACK_ENABLED`, kill-switch
`LLM_PROVIDER_DENYLIST_ENABLED`, default on within that path):

- `classify_provider_error` (`llm_fallback.py`) splits **terminal** failures
  (auth / billing / access / decommissioned) from transient ones.
- On a terminal failure the orchestrator **denylists** that provider from the pool
  (so it isn't hammered every turn; auto-re-probed after a cooldown) and **tells the
  user the likely cause** — "dropped **openai** … a billing/quota error … check the
  provider's billing dashboard; continuing on **anthropic**" — so they can fix the
  root problem (a late bill, a revoked key, lost access). The notice carries only the
  exception *type name*, never the raw message (which can echo the API key).

This is Prax doing, at the *failover* level, what Fugu does by dropping an
export-controlled model from its pool — degrade gracefully and surface why.

## If we adopted the ideas (sketch — not built)

A learned routing layer over the *existing* pool, not a Fugu dependency: start with a
**Trinity-style role-coordinator** (Thinker/Worker/Verifier across current tiers/
providers, evolutionary or bandit-tuned) by finally **wiring `tier_bandit.py`** (close
the loop: `select_tier` at component entry, `record_outcome` at turn end), behind a
flag, measured against static routing. Conductor-style RL delegation is a larger,
later step. Keep it provider-agnostic so the export-control resilience compounds.

## Bottom line

Don't adopt the product — it's closed and would invert Prax's architecture. Do treat
**Conductor/Trinity as reference techniques** for evolving Prax's heuristic delegation
toward learned routing (starting by wiring the dormant bandit), and bank the concrete
win this review already produced: **terminal-failure denylist + user notification**.
See also [model-routing.md](model-routing.md) (the dormant bandit, now honestly
documented), [orchestration.md](orchestration.md) (hub-and-spoke), and
[reliable-agentic-systems-bayer.md](reliable-agentic-systems-bayer.md) (the failover
flag's default-off contract).
