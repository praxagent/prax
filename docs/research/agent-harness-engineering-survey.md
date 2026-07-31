# Agent Harness Engineering: A Survey (TMLR submission, OpenReview 3hXEPbG0dh)

**Li, Xiao, Zhang, Liu, et al. (CMU / UAB / Tulane / Yale / Stanford / Amazon /
Rutgers / Virginia Tech; 17 authors, under review at TMLR 2026).** Project
page: picrew.github.io/LLM-Harness. Thesis in one line: **"the harness is
becoming the binding constraint"** — production reliability depends less on
the model than on the infrastructure wrapping it. Proposes the **ETCLOVG**
seven-layer taxonomy and maps 110+ papers / 23+ open-source systems onto it.

**Verdict: document + adopt as the standing self-audit lens for Prax — this
is the first survey whose unit of analysis IS Prax — plus ONE genuinely new
adopt-candidate: adaptive simplification (scaffolding must be periodically
re-justified as models improve).** No architecture to import; Prax already
occupies all seven layers. The value is a shared vocabulary, an honest gap
map, and a positioning fact worth knowing.

---

## The taxonomy, and where Prax sits in each layer

Four structural layers (what an agent *can* do) + three control-plane layers
(*how safely* it operates):

| Layer | Prax's implementation | Honest gap |
|---|---|---|
| **E** Execution — where code runs, sandbox constraints | `prax-sandbox` (container, Chromium CDP, noVNC desktop), sandbox-only browsing, local-or-remote daemon | **No per-tenant isolation** — one container, one bind-mounted workspace (the benchmark-pollution incident). microVMs parked on hardware ([agentenv](agentenv-microvm-sandboxes.md)) |
| **T** Tooling — description, discovery, protocols | ~97 tools behind `governed_tool`; MCP server (default-off, per-caller identity + allowlists); plugin capability gateway; spoke placement keeping the orchestrator under ~50 tools | A2A not implemented; tool *selection* is prompt-driven, not learned |
| **C** Context & memory | Overflow ladder (`clear_old_tool_results` → `compact_history` → `truncate_history`), per-space progress files, two-layer memory (Qdrant + Neo4j), `agent_plan` | **Weakest layer**: agent-initiated compaction unbuilt ([acm](acm-agentic-context-management.md)), memory consolidation is the standing weak cell, instruction-preserving compaction now a [CRUX](crux-shadow-evals.md) requirement |
| **L** Lifecycle & orchestration | Hub-and-spoke orchestrator + 15 spokes through the single `build_agent_loop` seam; task runner; heartbeat/timeout guards; durable checkpoints (flagged) | Durable resume still gated; multi-agent handoff is Prax-centric |
| **O** Observability | Execution graphs + `trace.py`, OTel→LGTM, `trace_search`, audit log, **per-trace cost accounting** (shipped this week) | Trace clustering unbuilt ([mastra](mastra-trace-intelligence.md)) |
| **V** Verification | `prax/eval/`: goldens + public/private split, benchmark adapters + matrix, multiturn pass^k, dual-axis trace grading, `prereg.py`, `accept_change` | Significance testing unbuilt ([harness-generalization](harness-generalization.md)); coding-agent bench now wired but unswept |
| **G** Governance | Risk tiers + lethal-trifecta guard, plugin trust tiers with `permissions.md` as a hard ceiling, approval gates with action fingerprints, keyless secrets-proxy | Perimeter-only trust (the `fable_feedback.md` root cause) |

## The positioning fact

Their ecosystem mapping finds **Observability and Governance are thin in open
source and mostly live inside commercial platforms** — and their per-layer
project counts show it (L: 47 projects, E: 20, V: 21, but O: 15, G: 14, C: 9).
Prax is an open-source harness whose *differentiator* is precisely the control
plane: governance-first, audit-by-default, verification as a first-class
subsystem. That is not a claim we invented for a README; it is the gap an
independent survey of 23 systems just measured. Worth saying plainly in
positioning material — with the caveat below.

## The one new idea: adaptive simplification

Of their five open challenges — hardening execution environments, reliable
state in long-running agents, trace-native failure diagnosis, standard
handoffs across agents/tools/humans, and **adaptive simplification as models
improve** — the first four are already Prax punch-list items under other
names. The fifth is not, and it is the one that bites us specifically:

**Scaffolding accretes and nobody removes it.** Prax has ~30 reliability
flags, an overflow ladder, retry ladders, tier escalation, guards on guards —
each justified against the model of the day. As models improve, some scaffold
stops paying for itself, and a few actively *hurt* (the flag-eval campaign
already rejected intent-clarification and deny-by-default on measured
evidence — that was simplification, done once, by accident of a campaign).
The adopt is to make it routine: **when the default model tier changes,
re-run the eval gate on the scaffolding it was justified against, and delete
what no longer earns its keep.** The machinery exists (flag A/B + prereg +
the significance row); what is missing is the *trigger* and the disposition
to remove rather than add. Complexity is a safety surface, not just a
maintenance cost.

## Honest limits — including one about how I read this

Under review, not accepted; a survey, so its contribution is organization
rather than evidence — the "harness is the binding constraint" thesis is
argued from cited work, not measured here.

**A sourcing caveat I hit directly:** OpenReview is behind a browser check, so
I could not read the PDF; this assessment is built from the **authors' own
project page** plus search results. A widely-linked third-party summary of
this survey attributes to it **nine** challenges and a set of striking
numbers (sandbox escape rates, a tool-format SWE-bench jump, a
benchmark-vs-merge-rate gap) that do **not** appear on the authors' page,
which lists **five** challenges and no such figures. I have not quoted those
numbers and neither should we — this is the audit-the-source rule that
[eval-rigor](eval-rigor-review.md) and the [openai-arc3](openai-arc3-harness-settings.md)
note keep earning: secondary summaries embellish, and a number with no
verifiable primary is not a number. If the PDF becomes readable, verify the
challenge list before citing anything from it.

Also worth reading alongside: *Stop Comparing LLM Agents Without Disclosing
the Harness* ([arXiv 2605.23950](https://arxiv.org/pdf/2605.23950)), which is
the same lesson as the ARC-AGI-3 reprice post from the eval-methodology side.

## Related

- [openai-arc3-harness-settings.md](openai-arc3-harness-settings.md) — the
  harness-decides-the-score result this survey generalises.
- [crux-shadow-evals.md](crux-shadow-evals.md) — where harness engineering
  stops helping (open-ended judgment).
- [nooa-object-oriented-agents.md](nooa-object-oriented-agents.md) — an
  E/T/C-layer alternative design.
- [agentenv-microvm-sandboxes.md](agentenv-microvm-sandboxes.md) — the
  E-layer hardening candidate their challenge #1 describes.
