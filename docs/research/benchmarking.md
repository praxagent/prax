# Benchmarking

[← Research](README.md)

> **Looking for the practical plan?**  This file covers *why*
> external benchmarking matters (§19).  For the concrete catalog of
> benchmarks to run — GAIA, τ-bench, SWE-bench, BrowseComp, etc. —
> with phased adoption, cost estimates, and harness design
> principles, see [**Prax Benchmarks — Agentic Harness Evaluation
> Plan**](prax-benchmarks.md).

### 19. Agentic Benchmarking — External Validation

**Finding:** Internal evaluation — integration tests, A/B experiments, trace analysis — validates architectural decisions within the system's own operating envelope but does not establish competitive positioning. External benchmarks provide standardized, reproducible comparisons against other agent systems on tasks designed to expose capability gaps that internal testing may not cover. An agent that passes all its own tests but fails on community benchmarks has optimized for its test distribution rather than for general capability.

#### Benchmark landscape and Prax mapping

The agentic AI research community has converged on several benchmarks that test distinct capability dimensions. The following table maps each benchmark to the Prax capabilities it would exercise and identifies the infrastructure required to participate.

| Benchmark | What It Tests | Prax Mapping | Infrastructure Gap |
|---|---|---|---|
| **SWE-bench** (Jimenez et al., 2024) | Resolve real GitHub issues — clone, edit, run tests | Sandbox spoke + codegen sub-agent | Evaluation harness to feed issues and verify patches |
| **GAIA** (Mialon et al., 2023) | Multi-step reasoning with tools — search, browse, calculate | Orchestrator + browser spoke + search tools | Closest fit — evaluation harness only |
| **τ-bench** (Yao et al., 2024) | Tool-augmented task completion with realistic tool APIs | Direct tool use via orchestrator | Environment setup + API mocks |
| **WebArena** (Zhou et al., 2024) | Web browsing tasks on real websites | Browser spoke (Playwright + CDP) | WebArena environment deployment |
| **HumanEval** / **MBPP** | Code generation accuracy | Sandbox spoke code execution | Simple — just feed problems and check outputs |

#### Recommended starting point: GAIA

GAIA (General AI Assistants) is the recommended first external benchmark for Prax, for four reasons:

1. **Broadest capability coverage.** GAIA tasks require search, multi-step reasoning, tool orchestration, and synthesis — exercising the orchestrator, browser spoke, search tools, and plan-verify-synthesize loop in combination. A single benchmark run surfaces weaknesses across multiple capability dimensions simultaneously.
2. **Public evaluation set with automated scoring.** The GAIA evaluation set is publicly available and scoring is deterministic (exact-match or within-tolerance for numerical answers). This eliminates subjective evaluation and enables fully automated benchmark runs.
3. **Direct mapping to Prax's natural operating mode.** GAIA tasks resemble the multi-step research and tool-use scenarios Prax already handles: "find information X, combine it with information Y, compute Z." No significant architectural adaptation is required — only an evaluation harness that feeds GAIA questions and captures answers.
4. **Community baseline availability.** Published results for GPT-4, Claude, and other foundation models (without agent scaffolding) provide baselines. Demonstrating that Prax with orchestration outperforms the bare model on GAIA would be direct evidence that the agent architecture adds value.

#### SWE-bench: highest visibility, higher cost

SWE-bench is the most prominent agentic benchmark in the research community and receives significant attention in industry. However, it requires substantially more infrastructure than GAIA: the evaluation harness must clone target repositories, apply the agent's proposed patches, run the repository's test suite, and verify that the specific failing test now passes without breaking other tests. This maps to Prax's sandbox spoke capabilities but requires extending the sandbox to handle arbitrary repository setup, dependency installation, and test execution — a non-trivial engineering investment.

SWE-bench is recommended as a second-phase benchmark target, after GAIA validates the core orchestration and tool-use pipeline.

#### Prax implementation

The integration test infrastructure (`tests/integration/`) provides the evaluation harness foundation. Existing test scenarios already define structured tasks with expected outcomes; this pattern can be adapted to GAIA's question-answer format with minimal modification. SWE-bench integration requires extending the sandbox spoke to clone target repos, apply patches, and run test suites — capabilities that are independently valuable for Prax's code generation workflows.

**References:**
- Jimenez, C. E. et al., "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" ICLR 2024 — [arXiv:2310.06770](https://arxiv.org/abs/2310.06770)
- Mialon, G. et al., "GAIA: A Benchmark for General AI Assistants," ICLR 2024 — [arXiv:2311.12983](https://arxiv.org/abs/2311.12983)
- Zhou, S. et al., "WebArena: A Realistic Web Environment for Building Autonomous Agents," ICLR 2024 — [arXiv:2307.13854](https://arxiv.org/abs/2307.13854)
- Yao, S. et al., "τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains," 2024 — [arXiv:2406.12045](https://arxiv.org/abs/2406.12045)
