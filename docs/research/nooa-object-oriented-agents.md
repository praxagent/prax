# NOOA — NVIDIA Object-Oriented Agents (arXiv 2607.20709)

**Furgale, Klingler, Nolan, et al. (NVIDIA-NeMo labs, 15 authors, 2026-07-22.**
Open source: github.com/NVIDIA-NeMo/labs-OO-Agents, ~500 stars in a week,
actively pushed.) A model-agnostic Python framework whose whole thesis is "an
agent is a Python object": methods are the actions, fields are the state,
docstrings are the prompts, type annotations are the I/O contracts. Six
model-facing capabilities: typed I/O, pass-by-reference over live objects,
code-as-action (CodeAct REPL), programmable loop engineering, explicit object
state, and model-callable harness APIs (the agent queries and edits its own
context). Reported results are serious: **SWE-bench Verified 82.2%**
(GPT-5.5 xhigh; beats OpenCode 78.6%), **Terminal-Bench 2.0 73.0%**,
CyberGym L1 86.8%, and **ARC-AGI-3 50.2% RHAE** with GPT-5.5 — **85.1% with
GPT-5.6-sol at <$20/game** — with an ablated **+11.8 points from the memory
subsystem alone**.

**Verdict: document + adopt TWO bounded ideas (context blocks as
model-callable API → strengthens the ACM row; ACT-R-style activation
retrieval → names our memory-consolidation gap); treat the paradigm itself as
the strongest entry yet in the code-as-action lane we already track as a
narrow experiment, not a rewrite.** Their acknowledged sandboxing gap is
Prax's existing answer, not ours to adopt.

---

## Why this one matters more than the usual framework paper

Numbers first: these are the best published ARC-AGI-3 results we have seen
(85.1% RHAE crosses OpenAI's own 38.3% same-week post and the estimated 48%
human tester average), from a framework whose agent for SWE/Terminal tasks is
253 lines. And it is open source with a benchmark harness, so the claims are
checkable in principle — unlike Schema's self-reported 99%
([arc-agi-3](arc-agi-3-schema-harness.md)).

The frame fits a thread we already run: RLM's code-REPL orchestration
([rlm-harness](rlm-recursive-language-models.md)) is tracked as a narrow
experiment via `run_python`. NOOA is that idea productionized — the model
writes Python that calls typed methods on live objects instead of emitting
JSON tool calls. Their Table-7 sweep (14 frameworks; "no other system
combines all six ideas") places LangChain/LangGraph — Prax's substrate — at
"partial" on every axis. That is a fair description of the ceiling we chose:
`build_agent_loop` + governed tools is message-passing, not code-over-objects.

## What Prax should take (bounded, no rewrite)

1. **Context as a model-callable API.** NOOA's static/dynamic context blocks +
   typed event log, queryable and editable by the agent, is the
   [ACM](acm-agentic-context-management.md) adopt-row generalized: not just
   "agent decides when to compact" but "context regions are first-class
   objects the agent can inspect". Strengthens the design for
   `context_compact`/`context_recall`; no new row — the ACM row gains this as
   its second independent sighting (after OpenAI's compaction numbers).
2. **Activation-based memory retrieval + async consolidation.** Their
   MemoryManager (seven self-curated tools, ACT-R activation — recency ×
   frequency × association — with background consolidation) is a concrete,
   evidence-backed (+11.8 RHAE ablated) design for exactly the cell every
   survey pass flags as Prax's weakest
   ([self-improving-survey](self-improving-agents-survey.md): memory
   consolidation). Adopt row: evaluate ACT-R-style activation as the ranking
   function for `memory_stm_*` / Qdrant recall, behind a flag, gated on the
   memory eval battery — not a port of their manager.

## What validates Prax without action

- **Their #1 acknowledged limitation is our architecture.** "NOOA executes
  model-written code in the agent's own process. The validator protects the
  agent loop, not the host." Prax runs model code in `prax-sandbox` — the
  boundary NOOA tells its own users to add via containers. Same for their
  workspace-discarded-at-task-boundary memory-transfer problem: that is what
  `progress_read/append` and the two-layer memory already do across sessions.
- **Convergence, again**: typed contracts (governed tools' schemas), loop
  engineering in plain code (`build_agent_loop` callers), explicit state
  (agent_plan, progress files). The six capabilities are a sharper vocabulary
  for things Prax mostly has in message-passing form.

## Honest limits

Benchmark numbers are NVIDIA's own runs, not independently reproduced;
framework papers benchmark their home turf with tuned harnesses (the
[openai-arc3](openai-arc3-harness-settings.md) lesson cuts both ways — their
73.0% TB and our future number will differ partly by harness, and NOOA's
"harness APIs" ARE the product being measured). Code-as-action widens the
injection blast radius precisely where Prax's provenance-tainting middleware
is thinnest (arbitrary code vs. schema'd tool calls) — any Prax experiment in
this lane stays inside the sandbox and behind governance, which costs some of
the latency the paradigm buys. License in-repo is non-standard (NOASSERTION
per GitHub API) — check before lifting code, though we are adopting ideas,
not code.

## Related

- [rlm-recursive-language-models.md](rlm-recursive-language-models.md) — the
  code-REPL orchestration lane this strengthens.
- [acm-agentic-context-management.md](acm-agentic-context-management.md) —
  context-as-API, second sighting.
- [self-improving-agents-survey.md](self-improving-agents-survey.md) — the
  memory-consolidation weakest cell the ACT-R idea targets.
- [arc-agi-3-schema-harness.md](arc-agi-3-schema-harness.md) — the ARC-3
  bar this resets (and a checkable-open-source contrast to Schema).
- [capy-swe-agent-platform.md](capy-swe-agent-platform.md) — prior
  commercial-convergence sighting; NOOA is the research-lab version.
