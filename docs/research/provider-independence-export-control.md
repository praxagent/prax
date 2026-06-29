# Provider-independence & the Fable/Mythos export-control case

Reference note on **why cross-provider resilience isn't abstract**, and the
terminal-failure handling it motivated in Prax. (This note replaces an earlier
one centered on Sakana **Fugu** — see "Correction" at the bottom for why that
framing was pulled.)

## The motivating real-world event

On **2026-06-12** a US export-control directive led Anthropic to suspend access to
its **Fable 5 and Mythos 5** frontier models for all users
([anthropic.com/news/fable-mythos-access](https://www.anthropic.com/news/fable-mythos-access)).
A top model can become **permanently unavailable by government action**, not just
rate-limited. That is the concrete case provider-independence exists for. (It's
also a "verify, don't assume" lesson — the names post-date the Jan-2026 knowledge
cutoff, so they read as garbled if you trust priors over a lookup.)

## What this shipped (independent of any vendor's claims)

Prax's failover was tuned for **transient** errors (rate-limit / 5xx / overload)
and would *optimistically reset to the primary every turn* — so a **permanently**
removed model (revoked key, unpaid bill, export pull) got re-hit forever and the
user was never told why. Now addressed (flag-gated under `LLM_FALLBACK_ENABLED`,
kill-switch `LLM_PROVIDER_DENYLIST_ENABLED`, default on within that path):

- `classify_provider_error` (`llm_fallback.py`) splits **terminal** failures
  (auth / billing / access / decommissioned) from transient ones.
- On a terminal failure the orchestrator **denylists** that provider from the pool
  (so it isn't hammered every turn; auto-re-probed after a cooldown) and **tells
  the user the likely cause** — e.g. "dropped **openai** … a billing/quota error …
  check the provider's billing dashboard; continuing on **anthropic**" — so they
  can fix the root problem. The notice carries only the exception *type name*,
  never the raw message (which can echo the API key).

## The still-open direction: learned routing (sourced to Prax's own code, not Fugu)

Prax's delegation is **heuristic** (orchestrator LLM tool-choice over a fixed
spoke/category map) and tier selection is **static**. The learned-routing
scaffolding exists but is **dormant**: `prax/agent/tier_bandit.py` (Thompson
sampler) never has `select_tier`/`record_outcome` called on the live path, and
`difficulty.py`'s estimate is discarded. Closing that loop — behind a flag,
measured against static routing (never spike — `CLAUDE.md`) — is the open work,
tracked as [`../IDEAS_BACKLOG.md`](../IDEAS_BACKLOG.md) #18. The target shape is
ATLAS ([itigges22/ATLAS](https://github.com/itigges22/ATLAS)), which demonstrates
a signal-fused difficulty estimator integrated with bandit-based tier routing.

## Correction — why the earlier "Fugu validation" framing was pulled

This note originally presented Sakana's **Fugu** (and its **Conductor** /
**Trinity** papers) as *external validation* of Prax's hub-and-spoke, multi-vendor
architecture, and cited orchestration-benchmark superiority. A **community note**
flagged that Fugu's benchmark comparisons **mix Sakana's self-reported Fugu scores
with vendors' published scores under different conditions** — not a like-for-like
comparison. On SWE-Bench-class evals, **scaffold differences alone can swing scores
10–20 points**, so cross-source, cross-scaffold numbers can't carry a "validation"
claim. Treating that as independent confirmation was a mistake.

What does **not** depend on those benchmarks, and is kept above: the real
**export-control event** (Anthropic-published) and the **terminal-failure denylist
it motivated** (shipped, verifiable in `llm_fallback.py`), plus the learned-routing
direction, which stands on **Prax's own dormant `tier_bandit.py` + ATLAS**. Prax no
longer cites Fugu as evidence. The lesson generalizes: **don't bank a vendor's
self-reported, cross-scaffold benchmark as architectural validation** — it's the
same "never spike benchmarks / verify the comparison is fair" discipline applied to
*external* claims.
