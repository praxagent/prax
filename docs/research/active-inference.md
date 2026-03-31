# Active Inference

[← Research](README.md)

### 16. Self-Verification Before Delivery

**Finding:** Agents that verify their own outputs before presenting them to users catch 60–90% of errors that would otherwise reach the user. Verification is far cheaper than re-generation.

- **Wang et al. (2023)** introduced self-consistency: sampling multiple reasoning paths and selecting the most consistent answer. The key insight is that verification (checking answers) is easier and more reliable than generation (producing answers).
- **Chen et al. (2024)** showed that "self-debugging" — where the model generates test cases for its own code, runs them, and fixes failures — improves code generation accuracy by 2–12% on standard benchmarks, with the biggest gains on harder problems.

**Prax implementation:** `verify_workspace_file()` and `verify_delegation_result()` (`prax/agent/verification.py`) run automated checks after workspace saves and sub-agent delegations: file existence, minimum content length, expected patterns, error indicator detection, and minimum word count. The workspace_save tool integrates verification and appends warnings when checks fail. ATLAS ([itigges22/ATLAS](https://github.com/itigges22/ATLAS)) demonstrated self-test generation for internal verification before presenting results to users.

**References:**
- Wang et al., "Self-Consistency Improves Chain of Thought Reasoning in Language Models," ICLR 2023 — [arXiv:2203.11171](https://arxiv.org/abs/2203.11171)
- Chen et al., "Teaching Large Language Models to Self-Debug," ICLR 2024 — [arXiv:2304.05128](https://arxiv.org/abs/2304.05128)
- Chen et al., "CodeT: Code Generation with Generated Tests," ICLR 2023 — [arXiv:2207.10397](https://arxiv.org/abs/2207.10397) — self-test generation and dual execution agreement for ranking candidates

### 17. Active Inference, Extrinsic Uncertainty Measurement, and the Harness as Markov Blanket

**Finding:** Large language models are systematically miscalibrated — they cannot reliably self-assess uncertainty or self-correct reasoning without external grounding. Agent harnesses that rely on the model to report its own confidence are architecturally unsound. The Free Energy Principle (FEP) from computational neuroscience provides a principled framework for measuring agent uncertainty *extrinsically*, through prediction errors, behavioral variance, and token-level entropy, bypassing the model's calibration failures entirely.

#### The calibration problem

Huang et al. (2023) demonstrated that LLMs cannot self-correct reasoning without external feedback. Models subjected to RLHF exhibit an "alignment tax" of overconfidence: they are fine-tuned to produce authoritative, helpful-sounding responses, which systematically suppresses expressions of uncertainty. When prompted to evaluate their own confidence (e.g., "rate your certainty from 1–10"), models produce poorly calibrated estimates that do not correlate with actual accuracy. This finding has been replicated across model families and scales.

The implication for agent harnesses is direct: any architecture that gates destructive actions on the model's self-reported confidence (e.g., "are you sure you want to delete this file?") provides no meaningful safety guarantee.

#### The Free Energy Principle and Active Inference

Friston's Free Energy Principle (FEP) posits that biological agents maintain internal generative models of their environment and act to minimize *variational free energy* — a mathematical proxy for prediction error, or "surprise." An agent that encounters unexpected sensory input must either update its internal model (learning) or act on the environment to bring observations back in line with expectations (agency). This dual mechanism — perception and action unified under a single objective — is formalized as *Active Inference* (Friston, 2010).

Maier (2025) extends this framework to multi-agent systems, formalizing the *Markov Blanket* as the statistical boundary separating an agent's internal states (beliefs) from external states (the environment). The agent interacts with the world exclusively through *sensory states* (inputs) and *active states* (outputs). This boundary is not merely a metaphor — it is a mathematical structure that determines what information crosses the agent-environment interface and in what form.

Duraisamy (2025) proposes applying Active Inference to scientific discovery, arguing for a dual-mode cognitive architecture: a *counterfactual sandbox* for hypothesis generation ("thinking") and a *deterministic validation layer* for hypothesis testing ("reasoning"). He further advocates for uncertainty-aware knowledge representations that distinguish between established facts and untested hypotheses — a departure from standard retrieval-augmented generation, which treats all retrieved content as equally authoritative.

#### Physics-informed invariants and the hallucination of structure

A parallel line of research (Cranmer, 2024) demonstrates that standard neural networks, operating in unconstrained function spaces, reliably "hallucinate physics" — they produce outputs that violate conservation laws, symmetry constraints, and causal structure because no such invariants are encoded in the architecture. The proposed remedy is to embed known structural invariants directly into the model or its execution environment, constraining the hypothesis space to physically or logically valid solutions.

For agent harnesses, the analogy is precise: an LLM proposing code changes operates in an unconstrained space of "plausible edits." Without architectural enforcement of software invariants (type checking, linting, test passage), the agent will propose structurally invalid changes with high confidence. The harness must supply the invariants the model lacks.

#### Applying Active Inference to agent harness design

Translating FEP into engineering yields four concrete mechanisms for extrinsic uncertainty measurement:

**1. Forced prediction error (expected observation matching).** The agent is required to declare its expected outcome *before* each tool call. The harness executes the action and computes the delta between the predicted and actual observation. A high prediction error — the agent predicted "tests pass" but received a stack trace — is a direct, model-independent measure of uncertainty. Accumulated prediction errors can trigger automatic mode transitions (e.g., revoking write-tool access until predictions realign with observations).

This is the Active Inference loop made concrete: the agent maintains a generative model (its prediction), takes an action, receives a sensory signal (the tool output), and experiences quantifiable surprise. The harness, not the model, performs the comparison.

**2. Semantic entropy via parallel sampling.** When the agent must choose a high-stakes action, the harness queries the model multiple times (e.g., *k* = 3–5, temperature > 0) and compares the proposed tool calls. If all samples converge on the same action, behavioral variance is low — the model's distribution is concentrated. If samples diverge (one proposes a file edit, another a web search, a third a deletion), the entropy of the action distribution is high, indicating genuine uncertainty regardless of the confidence expressed in any individual sample.

This technique treats the model as a stochastic process and measures the *spread* of its output distribution, which is a valid uncertainty estimate even when the model's verbalized confidence is miscalibrated. The cost is linear in *k* and should be reserved for actions whose risk warrants the additional inference.

**3. Conditional logprob thresholding.** When the underlying API exposes token-level log-probabilities (e.g., OpenAI's `logprobs` parameter), the harness can inspect the probability distribution over tokens that constitute critical action arguments — file paths, command strings, variable names. A flat distribution (low max-logprob, high entropy across candidates) indicates the model is distributing probability mass across multiple alternatives, even if the sampled token appears in isolation as a confident choice.

This signal is provider-specific: OpenAI exposes logprobs; Anthropic and most local inference engines do not provide equivalent access. Accordingly, logprob thresholding should be implemented as a *conditional enhancement* — active when the provider supports it, gracefully absent otherwise — rather than a load-bearing architectural component. The baseline (prediction error) must function without it.

**4. Epistemic state tracking (read-before-write invariant).** The harness maintains a ledger of what the agent has actually observed in the current session. Files, database state, API responses — each is tracked with a binary "verified" flag. Destructive actions (file writes, deletions, database mutations) are gated on the target having been read in the current session. This is a deterministic invariant: the model's confidence is irrelevant if it has not observed the artifact it proposes to modify.

This mechanism corresponds to the FEP notion that an agent cannot have a well-calibrated generative model of states it has not sensed. The harness enforces this constraint architecturally rather than relying on the model to self-regulate.

#### The harness as Markov Blanket

In Maier's formalization, the Markov Blanket mediates all information flow between internal and external states. Mapping this to an agent harness: the LLM (internal states / generative model) should never interact with tools, APIs, or file systems directly. The harness intercepts all outgoing actions and all incoming observations, applying transformations at the boundary:

- **Outgoing (active states):** The model emits a structured action with an expected observation. The harness validates the action against policy (risk tier, epistemic ledger, prediction error history) before executing.
- **Incoming (sensory states):** The harness does not return raw tool output verbatim. It computes the prediction error, summarizes the observation to the relevant signal, and injects only the *informational delta* — what was surprising — back through the boundary.

This boundary discipline serves two purposes: it prevents context window pollution from large raw outputs (a known cause of performance degradation; see §5, Lost in the Middle), and it gives the harness a natural interception point for all four uncertainty measurement mechanisms described above.

#### Existing Prax alignment

Several of Prax's existing architectural decisions already implement aspects of Active Inference, though they were motivated by engineering pragmatism rather than FEP theory:

| Prax Feature | Active Inference Analog |
|---|---|
| `action_policy.py` risk tiers | Invariant enforcement — destructive actions gated by deterministic policy, not model confidence |
| Hub-and-spoke delegation | Markov Blanket boundaries between orchestrator and spokes |
| Sandbox code execution | Counterfactual "thinking" environment (Duraisamy's dual-mode architecture) |
| Plan-verify-synthesize loop | Prediction → action → observation → update cycle |
| Workspace context re-injection every turn | Sensory state refresh — prevents drift from stale internal model |
| Thompson Sampling tier selection | Bayesian belief updating based on observed outcomes, not self-reported quality |

#### Proposed implementation path

1. **Expected observation field** — Add an optional `expected_observation` field to the tool call schema. Compute prediction error on every tool invocation. Track cumulative error per session. This is the minimal viable Active Inference loop and provides the data foundation for all subsequent mechanisms.
2. **Epistemic ledger** — Track read/verified state for workspace files and external resources. Gate write-actions on prior read verification. Approximately 50–100 lines in the orchestrator.
3. **Conditional logprob enhancement** — When the provider is OpenAI (or any API exposing `logprobs`), inspect token entropy on critical action arguments (file paths, command strings) for MEDIUM and HIGH risk actions. Fall back to prediction error when logprobs are unavailable. This preserves Prax's model-agnostic design.
4. **Semantic entropy (selective)** — For HIGH-risk actions only, sample *k* = 3 parallel completions and measure action-level divergence. Reserve for destructive operations where the inference cost is justified by the risk.

#### Epistemic validation

Any claimed improvement from these mechanisms must be empirically verified, not assumed. Prax's existing integration test and A/B replay infrastructure (see §12, Thompson Sampling) provides the evaluation framework:

- **Prediction error correlation:** Log `expected_observation` and actual outcomes across integration test scenarios. Compute rank correlation between prediction error magnitude and actual task failure. If prediction error does not predict failure, the mechanism adds overhead without value.
- **Intervention efficacy:** Run paired scenarios: one with epistemic gating active (auto-downshift to read-only on high prediction error), one without. Compare task completion rate, destructive action frequency, and total tool calls.
- **Logprob signal quality:** On OpenAI-routed runs, log token entropy for critical arguments alongside outcome quality. If high-entropy arguments do not correlate with errors, the signal is not informative for this use case.

The burden of proof is on each mechanism to demonstrate measurable improvement on Prax's own task distribution. Theoretical elegance is insufficient justification for architectural complexity.

**References:**
- Friston, K., "The Free-Energy Principle: A Unified Brain Theory?" Nature Reviews Neuroscience, 2010 — [doi:10.1038/nrn2787](https://doi.org/10.1038/nrn2787)
- Huang, J. et al., "Large Language Models Cannot Self-Correct Reasoning Yet," 2023 — [arXiv:2310.01798](https://arxiv.org/abs/2310.01798)
- Duraisamy, K., "Active Inference AI Systems for Scientific Discovery," 2025 — [arXiv:2506.21329](https://arxiv.org/abs/2506.21329)
- Maier, M., "From Artificial Intelligence to Active Inference: The Key to True AI and 6G World Brain," 2025 — [arXiv:2505.10569](https://arxiv.org/abs/2505.10569)
- Cranmer, M., "This physics idea might be the next generation of machine learning," 2024 — [YouTube](https://www.youtube.com/watch?v=MqDdYybN8o0)
- Friston, K., "Karl Friston Explains Active Inference & AI Breakthroughs" — [YouTube](https://www.youtube.com/watch?v=Q2O1iNCQadI)
- Kadavath, S. et al., "Language Models (Mostly) Know What They Know," 2022 — [arXiv:2207.05221](https://arxiv.org/abs/2207.05221)
- Kuhn, L. et al., "Semantic Uncertainty: Linguistic Invariances for Uncertainty Estimation in Natural Language Generation," ICLR 2023 — [arXiv:2302.09664](https://arxiv.org/abs/2302.09664)

### 18. Epistemic Proof Strategy — Validating Active Inference Mechanisms

**Finding:** Any claimed architectural improvement must be empirically validated against the system's own task distribution. Theoretical elegance is insufficient justification for complexity. The mechanisms proposed in §17 — prediction error tracking, epistemic gating, logprob entropy — each add code, latency, and cognitive overhead. Before any of them graduate from logging to enforcement, they must demonstrate measurable improvement through a graduated validation strategy that proceeds from cheapest to most rigorous.

#### Layer 1: Correlation mining (offline trace analysis)

After accumulating prediction error logs from real usage, the first validation step is purely offline. Trace files already contain structured events; the analysis requires no architectural changes.

- Extract `PREDICTION_ERROR` events from workspace trace logs and correlate with subsequent retries, errors, or successful completions within the same task.
- Compute Spearman rank correlation: does prediction error > 0.6 predict task failure significantly better than chance?
- Segment by tool type — prediction errors on `file_write` may be informative while prediction errors on `web_search` may be noise.
- If the correlation is weak (ρ < 0.3, p > 0.05), the mechanism is measuring noise rather than meaningful uncertainty. The correct response is to remove it rather than adding complexity to compensate.

This layer is essentially free: it operates on data that already exists (or will exist once Phase 1 logging is active) and requires only a post-hoc analysis script. It should be the first gate any mechanism passes before receiving further investment.

#### Layer 2: A/B integration testing

Once correlation mining establishes that a signal is informative, the next step is causal validation through controlled experimentation. Prax's existing A/B replay framework (from Thompson Sampling, §12) provides the infrastructure.

- **Control condition:** Epistemic gating OFF — prediction errors are logged but do not influence agent behavior. The agent operates with full tool access regardless of prediction error history.
- **Treatment condition:** Epistemic gating ON — high cumulative prediction error triggers automatic downshift (e.g., revoking write-tool access until predictions realign with observations, as described in §17).
- **Metrics:** Task completion rate (primary), retry count, total tool calls, destructive action frequency, time-to-completion.
- **Statistical power:** Approximately 10–20 runs per scenario per condition are required for meaningful effect sizes, given the high variance inherent in LLM-driven task execution. Use paired scenarios (same task, same initial state) to reduce between-run variance.
- **Decision rule:** If the treatment condition does not improve task completion rate by at least 5% absolute, the gating mechanism adds friction without value. Log-only mode is sufficient.

The A/B framework also enables testing interactions between mechanisms — for example, whether epistemic gating combined with logprob thresholding outperforms either alone, or whether the combination produces excessive conservatism (blocking valid actions due to compounded uncertainty signals).

#### Layer 3: Self-monitoring feedback loop

The final validation layer closes the loop between measurement and adaptation, creating a system that improves its own uncertainty calibration over time.

- Extend `prax_doctor` or introduce a dedicated `prediction_accuracy` tool that reads accumulated `PREDICTION_ERROR` trace events across recent sessions.
- Compute per-tool prediction accuracy: for each tool, what fraction of predictions fell within acceptable error bounds? Which tools exhibit systematic over- or under-prediction?
- Identify systematic misprediction patterns — for example, the agent may consistently underestimate the output length of search tools, or overestimate the success rate of code execution. These patterns are not random noise; they reflect stable biases in the model's generative process.
- Record detected metacognitive patterns via `MetacognitiveStore`, the same persistence layer used for solution archiving and failure memory.
- On future runs, the metacognitive injection surfaces relevant warnings: "Historical data shows prediction accuracy for `sandbox_exec` is 0.43 — consider verifying sandbox state before executing." This adjusts agent behavior without modifying the model's weights.
- This closes the Active Inference loop: prediction error → pattern detection → metacognitive warning → behavioral adjustment → lower prediction error. The system's uncertainty measurement improves through its own operation.

#### Prax implementation

Phases 1–4 from §17 (prediction error, epistemic ledger, logprob entropy, semantic entropy) are fully implemented and log to workspace traces as `PREDICTION_ERROR`, `EPISTEMIC_GATE`, `LOGPROB_ENTROPY`, and `SEMANTIC_ENTROPY` events respectively. The A/B replay infrastructure from Thompson Sampling (§12) provides the experiment framework for Layer 2. Self-monitoring integration with `MetacognitiveStore` enables the closed-loop learning described in Layer 3. The critical architectural constraint is that all three validation layers operate on the same trace format, enabling a unified analysis pipeline.

**Empirical validation status (as of 2026-03-30):**

All four Active Inference phases have been validated through both deterministic e2e tests (ScriptedLLM, 13 tests) and live LLM integration tests (7 tests, real API calls). Key empirical observations from real LLM runs:

- **Schema augmentation confirmed:** All governed tools successfully expose the `expected_observation` field. The LLM fills this field unprompted when the system prompt instructs it to do so (observed in trace: `expected_observation: 'search results with key...'`).
- **Epistemic gate fires correctly:** Write-before-read triggers the gate and appears in audit traces. The agent self-corrects by reading first on subsequent attempts. New file creation (no prior content to read) passes the gate as expected.
- **Read-then-write passes gate:** When the agent reads a file before modifying it, the epistemic ledger records the read and the subsequent write proceeds without blocking.
- **Prediction tracking coexists with budget tracking:** Active Inference tracking adds no measurable overhead to tool call counts — simple tasks still complete in 1 LLM call with no budget exhaustion.
- **Trace events are well-formed:** All four event types (`PREDICTION_ERROR`, `EPISTEMIC_GATE`, `LOGPROB_ENTROPY`, `SEMANTIC_ENTROPY`) drain correctly to workspace trace logs at the end of each orchestrator turn.

These results establish that the Active Inference mechanisms are *wired correctly* — the infrastructure works. The next validation step (Layer 1: correlation mining) requires accumulating production trace data to determine whether prediction error magnitude *predicts* task failure, which will determine whether the mechanisms should graduate from logging to enforcement.

**References:**
- Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning," NeurIPS 2023 — [arXiv:2303.11366](https://arxiv.org/abs/2303.11366)
- Madaan et al., "Self-Refine: Iterative Refinement with Self-Feedback," NeurIPS 2023 — [arXiv:2303.17651](https://arxiv.org/abs/2303.17651)
- Thompson Sampling tier selection framework (§12) as the evaluation vehicle for A/B experimentation
