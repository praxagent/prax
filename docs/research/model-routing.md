# Model Routing

[← Research](README.md)

### 12. Adaptive Tier Selection via Thompson Sampling

**Finding:** Static model-tier assignment wastes budget (over-provisioning easy tasks) or degrades quality (under-provisioning hard ones). Multi-armed bandit algorithms — especially Thompson Sampling — adaptively learn which tier works best for each component and difficulty level.

- **Thompson (1933)** introduced the probability-matching algorithm: sample from each arm's posterior distribution and pick the arm with the highest draw. This naturally balances exploration and exploitation without a tunable epsilon parameter.
- **Chapelle & Li (2011)** provided the first large-scale empirical evaluation of Thompson Sampling in a production setting (online advertising), showing it matches or outperforms UCB1 and epsilon-greedy while being simpler to implement.
- **Russo et al. (2018)** published the definitive tutorial on Thompson Sampling, covering Beta-Bernoulli bandits, contextual bandits, and the theoretical foundations (Bayesian regret bounds).

**Prax implementation:** `TierBandit` (`prax/agent/tier_bandit.py`) maintains a Beta(α, β) posterior for each (component, difficulty, tier) triple. On each run, it samples from all posteriors, divides by a cost weight (low=1×, medium=4×, high=16×, pro=64×), and picks the tier with the highest cost-weighted efficiency. Difficulty constraints penalize mismatches (e.g., high tier on easy tasks gets a 0.3× multiplier). Posteriors persist to `prax/data/tier_bandit_state.json` so learning carries across restarts. The ATLAS project ([itigges22/ATLAS](https://github.com/itigges22/ATLAS)) demonstrated a similar Thompson Sampling router with cost-weighted efficiency and difficulty-binned posteriors in an open-source agentic framework.

**References:**
- Thompson, W.R., "On the Likelihood that One Unknown Probability Exceeds Another in View of the Evidence of Two Samples," Biometrika, 1933
- Chapelle & Li, "An Empirical Evaluation of Thompson Sampling," NeurIPS 2011 — [paper](https://proceedings.neurips.cc/paper/2011/hash/e53a0a2978c28872a4505bdb51db06dc-Abstract.html)
- Agrawal & Goyal, "Analysis of Thompson Sampling for the Multi-armed Bandit Problem," COLT 2012 — [proceedings](https://proceedings.mlr.press/v23/agrawal12.html) — first modern finite-time regret analysis proving logarithmic regret
- Russo et al., "A Tutorial on Thompson Sampling," Foundations and Trends in ML, 2018 — [arXiv:1707.02038](https://arxiv.org/abs/1707.02038)
- Chen et al., "FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance," 2023 — [arXiv:2305.05176](https://arxiv.org/abs/2305.05176) — LLM cascade routing for cost optimization

### 13. Difficulty-Driven Routing

**Finding:** Allocating more compute (larger models, longer inference) to harder problems improves efficiency without sacrificing quality on simple tasks. This is analogous to adaptive computation time in neural networks.

- **Graves (2016)** introduced Adaptive Computation Time (ACT) for RNNs — the network learns how many computation steps to perform per input, spending more time on harder examples. This was the first formal treatment of "think longer on harder problems" in deep learning.
- **Snell et al. (2024)** showed that scaling test-time compute (more tokens, more passes) can be more effective than scaling model parameters for a given inference budget. On math benchmarks, optimally allocating compute at inference time outperformed a 14× larger model with fixed compute.

**Prax implementation (current state — verified):** `estimate_difficulty()` (`prax/agent/difficulty.py`) uses signal fusion — message length, hard/easy keyword patterns, multi-step markers, URL count, question complexity — to classify incoming messages as EASY/MODERATE/HARD. The orchestrator injects a difficulty **hint into the system prompt** (`difficulty_context_for_prompt`, `orchestrator.py`). Note the scaffolding is **not fully wired**: the orchestrator currently *discards* the returned difficulty value (it calls `estimate_difficulty()` for the prompt hint but does not route on it), and the Thompson Sampling bandit (`prax/agent/tier_bandit.py`) is **implemented but dormant** — its `select_tier`/`record_outcome` are not called on the live path, so the learning loop never closes and difficulty does **not** seed it yet. So today this is a *prompt nudge*, not learned/adaptive tier selection; wiring `difficulty → bandit → tier` (behind a flag, with the loop closed) is the open work. ATLAS ([itigges22/ATLAS](https://github.com/itigges22/ATLAS)) demonstrated a signal-fused difficulty estimator integrated with bandit-based tier routing — the target shape. See also the [provider-independence note](provider-independence-export-control.md) (the shipped terminal-failure denylist + the still-dormant bandit).

**References:**
- Graves, A., "Adaptive Computation Time for Recurrent Neural Networks," 2016 — [arXiv:1603.08983](https://arxiv.org/abs/1603.08983)
- Raposo et al., "Mixture-of-Depths: Dynamically Allocating Compute in Transformer-Based Language Models," 2024 — [arXiv:2404.02258](https://arxiv.org/abs/2404.02258) — extends adaptive computation to transformers via top-k routing
- Snell et al., "Scaling LLM Test-Time Compute Optimally Can Be More Effective Than Scaling Model Parameters," 2024 — [arXiv:2408.03314](https://arxiv.org/abs/2408.03314)

### 14. Logprob "entropy feature" and the Responses-API incompatibility

**What the "entropy feature" is.** For OpenAI models, `llm_factory.build_llm`
requests **token log-probabilities** (`logprobs` + `top_logprobs`) and attaches a
callback (`prax/agent/logprob_analyzer.py`, *"Phase 3 — logprob entropy analysis"*).
From those logprobs it computes the **entropy of the tokens in a tool call's
arguments**: a high-entropy / low-confidence argument sequence (logprob below
`-2.0`, entropy above `0.4`) is a signal the model is **guessing** — an early,
cheap, *uncertainty / hallucination-risk* indicator at the point of action. That is
why it's called the "entropy feature": logprobs → token entropy → a confidence
signal feeding the hallucination-guard family (the `semantic_entropy` lane of
`prax_hallucination_guard_total`, see [observability](../infrastructure/observability.md)).
It degrades gracefully — the callback no-ops when a response carries no logprob data.

**The bug it caused.** The logprob request was injected **unconditionally** for
every OpenAI model. But the **"pro" / reasoning models** (e.g. `gpt-5.4-pro`, the
`o*` series) route through OpenAI's **Responses API**, which does **not** accept
`logprobs`/`top_logprobs` and raises:

> `Responses.create() got an unexpected keyword argument 'logprobs'`

This **crashed the spoke outright**. Observed in trace `fbf008f0…` ("teach me bias
vs variance"): `delegate_professor` (gpt-5.4-**pro**) failed twice with this error;
the lesson never happened. (The same incompatibility also surfaces as the
`top_logprobs`/`logprobs` `UserWarning` in test output.)

**The fix** (`llm_factory.py`, openai branch): detect reasoning/Responses-API
models (model name contains `-pro`, starts with `o1`/`o3`/`o4`, or starts with
`gpt-5.5`) and for them **skip the logprobs entropy machinery** *and* pass only the
default temperature (these models also reject a custom temperature). Chat-Completions
models (gpt-5.4 nano/mini/full) keep the entropy feature unchanged.

> **`gpt-5.5` is in the denylist for a verified reason.** When the HIGH tier moved
> to `gpt-5.5` (2026-06), an empirical 1-token probe returned
> `400 'logprobs' is not supported with this model` — i.e. `gpt-5.5` (full, not
> just `-pro`) is a reasoning model that rejects logprobs, so without the gate
> **every HIGH-tier turn would have crashed** exactly like the professor. `gpt-5.4`
> and below still support logprobs. **This is a name denylist and therefore
> fragile** — the robust long-term fix is to catch the 400 once and **auto-disable
> logprobs for that model id** thereafter, so a new rejecting model degrades
> instead of crashing. Until then, add new reasoning-model markers here as they ship.

> **Two follow-ons this exposed, tracked elsewhere:** (1) the orchestrator marked
> the failed delegations "done" and told the user *"Done"* — a false-completion
> (system-prompt guard added; the durable fix is the accept-gate, IDEAS_BACKLOG #22);
> (2) raw ```mermaid was then dumped into Discord, which can't render it — the
> proper fix is **sandbox-side rendering** (IDEAS_BACKLOG #24).

### 15. Mixture-of-Agents (runtime ensembling at the model-provider layer)

**Source:** Nous **Hermes Agent — Mixture of Agents (MoA)**
([docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents)).

**What it is.** A *"virtual model provider"*: for one model call, several **reference
models** run first — cheaply, with **no tool schemas** and stripped context (just the
conversation) — and an **aggregator** (the acting model) consumes their outputs as
private guidance, then writes the response **and** emits tool calls. It ensembles
*perspectives at the model-call layer*, transparent to the agent loop (no broken
prompt caching, no separate toolset). Reported +6 pts on HermesBench (Opus
aggregating a GPT-5.5 reference: 0.82 vs 0.76 solo).

**Why it's relevant to Prax.** It is the *systematic, transparent* version of what
Prax already gestures at — `multi_model_query`, the diverse-reviewer pattern,
cross-provider. Two ideas worth taking:
- **Integrate at the model-provider layer, not the orchestration layer.** A MoA
  "virtual provider" inside `llm_factory` would let the orchestrator just *pick a
  model* that happens to be an ensemble — cleaner than the current explicit
  `multi_model_query` *tool*, and it preserves the agent loop / caching.
- **The reference/aggregator cost split.** Reference models run **without** tool
  schemas + stripped context (cheap); only the aggregator does the expensive
  tool-calling. That's the trick that makes ensembling affordable.

**The catch — difficulty-gate it, never always-on.** N models per turn collides with
Prax's cost discipline (#22) and the tier system. MoA should be the **escalation
target for *hard* turns**, not a default — i.e. an alternative to "escalate to a
higher *tier*": escalate to a MoA *ensemble*. That ties it directly to the dormant
**difficulty routing / tier-bandit** (§12–§13, IDEAS_BACKLOG #18), governed by the
accept-rate metric (#22) so it only fires where it pays for itself.
