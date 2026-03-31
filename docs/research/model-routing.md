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

**Prax implementation:** `estimate_difficulty()` (`prax/agent/difficulty.py`) uses signal fusion — message length, hard/easy keyword patterns, multi-step markers, URL count, question complexity — to classify incoming messages as EASY/MODERATE/HARD. The orchestrator injects difficulty context into the system prompt and uses it to seed the Thompson Sampling bandit. ATLAS ([itigges22/ATLAS](https://github.com/itigges22/ATLAS)) demonstrated a similar signal-fused difficulty estimator integrated with bandit-based tier routing.

**References:**
- Graves, A., "Adaptive Computation Time for Recurrent Neural Networks," 2016 — [arXiv:1603.08983](https://arxiv.org/abs/1603.08983)
- Raposo et al., "Mixture-of-Depths: Dynamically Allocating Compute in Transformer-Based Language Models," 2024 — [arXiv:2404.02258](https://arxiv.org/abs/2404.02258) — extends adaptive computation to transformers via top-k routing
- Snell et al., "Scaling LLM Test-Time Compute Optimally Can Be More Effective Than Scaling Model Parameters," 2024 — [arXiv:2408.03314](https://arxiv.org/abs/2408.03314)
