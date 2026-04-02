# Production Patterns

[← Research](README.md)

### 8. Framework Patterns from Production Systems

Several production agent frameworks have converged on similar architectural patterns:

| Pattern | Used By | Prax Equivalent |
|---------|---------|-----------------|
| Centralized orchestrator + specialized workers | AutoGen, CrewAI, MetaGPT | `ConversationAgent` + `delegate_task` |
| Plan → Execute → Verify loop | Devin, OpenHands, SWE-Agent | `agent_plan` → tools → `agent_step_done` |
| Persistent workspace with git backing | Devin, OpenHands | `workspace_service` + git commits |
| Tool result validation before response | Copilot Workspace | Claim audit + plan enforcement |
| Scratchpad / working memory files | SWE-Agent, Voyager | Workspace notes + instruction persistence |
| Bounded retry with rollback | OpenHands, LangGraph | `CheckpointManager` + `_invoke_with_retry` |
| Complexity-triggered planning | ADaPT | `_classify_complexity` + orchestrator hints |
| Deterministic workflow graphs with structured step boundaries | [acpx](https://github.com/openclaw/acpx/tree/main) | Content Editor pipeline (research → write → publish → review) |

### 9. Tool Overload and Selection Degradation

**Finding:** Giving an LLM access to too many tools simultaneously degrades tool selection accuracy, increases hallucinated tool calls, and wastes context on definitions the agent rarely uses. Every major LLM provider has documented this effect, and multiple academic benchmarks confirm it.

**Industry guidance:**

- **OpenAI** notes that o3/o4-mini handle up to ~100 tools "in-distribution," but performance still degrades as tool count grows: "longer lists mean the model has more options to parse during its reasoning phase... tool hallucinations can increase with complexity" ([o3/o4-mini Prompting Guide](https://developers.openai.com/cookbook/examples/o-series/o3o4-mini_prompting_guide)). Their [Function Calling Guide](https://developers.openai.com/api/docs/guides/function-calling) recommends keeping tool definitions concise.
- **Anthropic** reports that tool selection accuracy "degrades significantly once you exceed 30–50 available tools" ([Tool Search Tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)). In their advanced tool use study, loading 58 tools from 5 MCP servers consumed ~55K tokens before the conversation even started. Implementing on-demand tool search improved Claude Opus 4 accuracy from 49% to 74% ([Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use)).

**Academic evidence:**

- **RAG-MCP** (Gan & Sun, 2025) measured tool selection accuracy at just 13.62% when all tool definitions are present, vs. 43.13% with RAG-based pre-filtering — a 3× improvement from simply not showing irrelevant tools ([arXiv:2505.03275](https://arxiv.org/abs/2505.03275)).
- **ToolLoad-Bench** (Wang et al., AAAI 2026) formalized "cognitive load" for tool-using agents and found "distinct performance cliffs" as load increases. GPT-4o achieved 68% overall with graceful degradation, while open-source models collapsed to 17% ([arXiv:2601.20412](https://arxiv.org/abs/2601.20412)).
- **ToolLLM** (Qin et al., ICLR 2024) showed that using an API retriever to pre-filter relevant tools from a pool of 16,464 APIs actually *outperformed* giving the model the ground-truth API set, because a smaller search space reduces confusion ([arXiv:2307.16789](https://arxiv.org/abs/2307.16789)).
- **Gorilla** (Patil et al., NeurIPS 2024) demonstrated that LLMs hallucinate wrong API calls when exposed to large tool sets, and that retrieval-augmented tool selection outperforms GPT-4 by 20.43% on API call accuracy ([arXiv:2305.15334](https://arxiv.org/abs/2305.15334)).
- **BFCL** (Patil et al., ICML 2025) found that even top models "still stumble when they must remember context, manage long conversations, or decide when not to act" — performance degrades as tool selection difficulty increases ([proceedings](https://proceedings.mlr.press/v267/patil25a.html)).

**Prax implementation:** Hub-and-spoke architecture — the orchestrator holds ~10 core tools and delegates domain-specific work to focused sub-agents (media, browser, sandbox, workspace, scheduler, codegen), each with a curated tool set of 7–15 tools. If delegation fails, Prax can fall back to reading a generated tool catalog and calling any tool directly.

### 10. Multi-Agent Content Pipelines and Iterative Refinement

**Finding:** Multi-agent pipelines with dedicated reviewer agents and iterative revision loops produce significantly higher-quality content than single-pass generation. Diverse agents (different models or providers) outperform homogeneous ones.

**Key results:**

| Study | Finding | Source |
|-------|---------|--------|
| **Self-Refine** (NeurIPS 2023) | ~20% average improvement over single-pass; most gains in first 1-2 iterations | [arXiv:2303.17651](https://arxiv.org/abs/2303.17651) |
| **MAR: Multi-Agent Reflexion** (2024) | Multi-critic debate: HumanEval 76.4% → 82.6% vs single-agent reflexion | [arXiv:2512.20845](https://arxiv.org/abs/2512.20845) |
| **ICLR 2025 Review Study** | 45,000 reviews; 89% of updated reviews improved after AI feedback | [arXiv:2504.09737](https://arxiv.org/abs/2504.09737) |
| **Diverse-Model Debate** (2024) | 91% on GSM-8K (beats GPT-4); same-model debate only 82% | [arXiv:2410.12853](https://arxiv.org/abs/2410.12853) |
| **STORM** (Stanford) | 25% improvement in organization, 10% in coverage vs baselines | [arXiv:2402.14207](https://arxiv.org/abs/2402.14207) |
| **Google Agent Scaling** (2024) | +80.9% on parallelizable tasks; -39% to -70% on sequential reasoning | [arXiv:2512.08296](https://arxiv.org/abs/2512.08296) |
| **Writer-R1** (2026) | Generation-Reflection-Revision loop; 4B model outperforms 100B+ baselines | [arXiv:2603.15061](https://arxiv.org/abs/2603.15061) |
| **CycleResearcher** (ICLR 2025) | Research-Review-Refinement cycle; reviewer achieves 26.89% reduction in MAE | [arXiv:2411.00816](https://arxiv.org/abs/2411.00816) |

**Proven orchestration patterns** (from Google ADK and [multi-agent collaboration survey](https://arxiv.org/html/2501.06322v1)):

| Pattern | How It Works | Best For |
|---------|-------------|----------|
| Sequential Pipeline | A → B → C → D | Simple content, each stage independent |
| Evaluator-Optimizer Loop | Writer → Reviewer → Writer (until threshold) | Iterative refinement |
| Parallel Fan-Out/Gather | Multiple agents in parallel, then merge | Research-heavy tasks |
| Generator-Critic | One creates, another tears it apart | Quality assurance |

**Sequential prompting beats monolithic prompting (priming effect):** A related insight from production workflow systems: putting all steps in a single prompt at the start of context generally gives suboptimal results compared to revealing intent step by step. LLMs are subject to *priming* — a front-loaded mega-prompt causes the model to latch onto the most salient instructions while diluting later steps (consistent with the Lost in the Middle finding, §5). Sequential prompting within the same session gives each step a fresh "attention budget" while preserving accumulated context from prior stages. This is the same principle behind Prax's spoke delegation — each agent gets exactly the context it needs for its stage, nothing more. [**acpx**](https://github.com/openclaw/acpx/tree/main) demonstrates this pattern in production: its "Agentic Graphs" feature drives coding agents through deterministic node-based workflows on top of the Agent Client Protocol (ACP), with structured JSON boundaries between steps that provide observability, checkpointing, and type-safe contracts. The acpx PR triage workflow (extract intent → cluster → assess quality → review → refactor → resolve conflicts) processes 300–500 PRs/day on the OpenClaw repo, with each step emitting structured data for dashboard monitoring — a concrete validation of sequential pipeline orchestration at scale.

**Optimal revision cycles:** 2-3 rounds.  Self-Refine showed most gains in passes 1-2.  Multi-agent debate peaks at round 3, can degrade at rounds 4-5.

**Prax implementation:** The Content Editor spoke (`prax/agent/spokes/content/`) runs a 5-phase pipeline: Research → Write (MEDIUM tier) → Publish → Review (HIGH tier, different provider when available) → Revise (up to 3 cycles).  The Reviewer visually inspects the rendered Hugo page via the Browser Agent and gives structured adversarial feedback.  The Writer and Reviewer use different LLM providers when multiple are configured (e.g. Claude reviews GPT's writing), leveraging the diverse-agent improvement.

**References:**
- Madaan et al., "Self-Refine," NeurIPS 2023 — [arXiv:2303.17651](https://arxiv.org/abs/2303.17651)
- Du et al., "Improving Factuality and Reasoning via Multi-Agent Debate," ICML 2024 — [arXiv:2305.14325](https://arxiv.org/abs/2305.14325)
- Shao et al., "STORM," 2024 — [arXiv:2402.14207](https://arxiv.org/abs/2402.14207)
- Huang et al., "Google Agent Scaling," 2024 — [arXiv:2512.08296](https://arxiv.org/abs/2512.08296)
- Gu et al., "Writer-R1," 2026 — [arXiv:2603.15061](https://arxiv.org/abs/2603.15061)
- Li et al., "CycleResearcher," ICLR 2025 — [arXiv:2411.00816](https://arxiv.org/abs/2411.00816)
- Multi-Agent Collaboration Survey — [arXiv:2501.06322](https://arxiv.org/html/2501.06322v1)
- Google ADK Patterns — [developers.googleblog.com](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/)
