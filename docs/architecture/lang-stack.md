# The lang* Stack — How Prax Uses LangChain & LangGraph

[← Architecture](README.md)

Prax is built on the LangChain 1.x / LangGraph 1.x stack, used **deliberately
shallowly**: we take the layers that are genuine leverage (the multi-provider
model layer, the tool contract, the prebuilt ReAct loop, checkpointing) and we
own every seam around them, so that loop-level change — enabling middleware,
adopting a new major, or one day swapping in an owned loop — is a one-module
event, not a codebase sweep. This doc is the contract for keeping it that way.

## What we use, from where

| Package | What we use | Where (the ONLY places) |
|---|---|---|
| `langchain` | `create_agent` (prebuilt ReAct loop) | `prax/agent/agent_loop.py` |
| `langchain` | `AgentMiddleware` (in-loop hooks) | `prax/agent/loop_middleware.py` |
| `langgraph` | Checkpointers (`InMemorySaver`, optional `SqliteSaver`) | `prax/agent/checkpoint.py` (behind `CheckpointManager`) |
| `langchain_core` | `@tool` / `BaseTool` — the tool contract | everywhere tools are defined (~60 modules; this is the deep, keep-forever coupling) |
| `langchain_core` | message types, callbacks (tracing) | agent + services layers |
| `langchain-openai` / `-anthropic` / `-google-vertexai` / `-ollama` | provider chat models | `prax/agent/llm_factory.py` (`build_llm()`) |
| `langchain-community` | `DuckDuckGoSearchRun` | `prax/helpers_functions.py` |

**What we deliberately do NOT use:** custom `StateGraph`s, nodes/edges,
`Send`, subgraph composition, or any LangGraph platform/server surface. The
loop is a *replaceable component*, not the architecture. Adopting deeper
graph-shaped business logic would flip that and must clear the same bar as a
new dependency.

## The construction seam (layer rule 4)

Every agent loop in Prax — orchestrator, spokes, one-shot sub-agents — is
built by **one function**:

```python
from prax.agent.agent_loop import build_agent_loop

graph = build_agent_loop(llm, tools)                        # spokes / sub-agents
graph = build_agent_loop(llm, tools, checkpointer=saver)    # orchestrator
```

`scripts/check_layers.py` (rule 4, runs in `make ci`) enforces that **only**
`agent_loop.py` + `loop_middleware.py` import `langchain.agents.*`, and only
those two plus `checkpoint.py` import `langgraph*`. Do not add imports
elsewhere; if you think you need to, the thing you're building belongs in one
of those modules.

Why this matters: before the seam existed there were 14 scattered
`create_agent` call sites across ten modules, and the loop was effectively
unswappable. With the seam, middleware rollout, a LangChain 2.x migration, or
an owned-loop experiment are all changes to one module with the eval suite as
the referee.

## In-loop middleware (`AGENT_MIDDLEWARE_ENABLED`, default off)

Prax enforces safety at two layers, and they are complements, not rivals:

| Concern | Perimeter (`governed_tool.py`, always on) | In-loop (`loop_middleware.py`, flag-gated) |
|---|---|---|
| Risk classification + HIGH confirmation | ✅ authoritative gate | — |
| Audit log, turn budgets, trifecta latch | ✅ | — |
| Epistemic reliability tagging (`_tag_result`) | ✅ | — |
| **Provenance-taint untrusted content before it re-enters model context** | ✗ structurally can't (never sees the message stream) | ✅ `UntrustedContentTaint` |
| **Liveness signal per model step** | ✗ (watches from outside the invoke) | ✅ `LoopHeartbeat` |

The middleware stack (LangChain 1.x `AgentMiddleware`, hooks like
`wrap_tool_call` / `before_model` / `after_model`) is assembled in
`loop_middleware.default_middleware()` and attached by `build_agent_loop()`
only when `AGENT_MIDDLEWARE_ENABLED=true`. Off (the default), no `middleware`
kwarg is passed at all — the compiled graph is identical to prior behaviour
and keyless CI stays green.

Current stack when enabled:

- **`UntrustedContentTaint`** — results of untrusted-source tools (the
  trifecta "untrusted" leg: browser, fetch, search, RSS, transcripts, PDF/web
  summaries, news…) get a provenance banner *before* they re-enter the
  model's context, framing embedded directives as data, not instructions.
  String content only (provider-native list content passes through),
  idempotent, fails open. **Known limitation:** classification is
  `trifecta.is_untrusted_source()` — a name-substring list. When adding ANY
  tool that turns a URL or external medium into model-visible text, add its
  name pattern to `_SRC_NAMES` (there's a coverage test in
  `tests/test_agent_loop.py`); the long-term fix is an explicit
  `ingests_external_content` capability flag at tool registration.
- **`LoopHeartbeat`** — touches the orchestrator's `TraceHeartbeat` around
  every model call from *inside* the loop (bound via a ContextVar in the
  invoke worker thread), upgrading stall detection from "did the invoke
  start" to "is the loop still stepping". Implemented with `wrap_model_call`,
  not `before_model`/`after_model` — see house rule 4.

### House rules for middleware

1. **One module.** All middleware lives in `loop_middleware.py`; upstream
   hook-signature churn lands there and nowhere else.
2. **Fail open.** A middleware bug must degrade to the unmodified result plus
   a log line — never kill a turn.
3. **Additive only.** Middleware may add provenance/telemetry; the
   *authoritative* gates (confirmation, budgets, trifecta) stay in
   `governed_tool.py`. Don't fork enforcement into two places.
4. **Only define the hooks you use — and prefer `wrap_*` hooks.**
   `before_model`/`after_model`/`before_agent`/`after_agent` each add a graph
   node per cycle, which counts against `recursion_limit` and silently
   shrinks every loop's effective tool-call budget. `wrap_model_call` /
   `wrap_tool_call` run inside existing nodes and are budget-neutral
   (`test_loop_heartbeat_uses_wrap_not_node_hooks` pins this). If you truly
   need a node hook, scale `get_recursion_limit()` accordingly.
5. **Flag-gated + eval-refereed.** New middleware ships default-off and flips
   only after `make eval-benchmark BENCH=injecagent LIFT=1` /
   `BENCH=sycophancy LIFT=1` and `make eval-benchmark BENCH=all` show no
   regression (note: lift is computed per-benchmark; `all` runs accuracy
   only). Key-free tests in `tests/test_agent_loop.py` are mandatory.
6. **`extra_middleware` is caller-owned, not flag-gated.** A call site
   passing middleware explicitly is code-level intent; the
   `AGENT_MIDDLEWARE_ENABLED` flag gates only the *default* stack. Extra
   middleware classes must not reuse a default-stack class name —
   `create_agent` rejects duplicate-named middleware.

### Stock middleware: evaluate, don't assume

LangChain ships stock middleware overlapping things Prax hand-rolled at the
perimeter before middleware existed: `ModelFallbackMiddleware` (vs our
`_bind_provider` failover), `ModelRetryMiddleware`/`ToolRetryMiddleware` (vs
`_invoke_with_retry`), `ModelCallLimitMiddleware`/`ToolCallLimitMiddleware`
(vs turn budgets), `SummarizationMiddleware`/`ContextEditingMiddleware` (vs
`prepare_context`). Each is a *candidate* to retire hand-rolled code — adopted
one at a time, flag-gated, and only where the cost axis
(`pass_per_1k_tokens`) says stock ≥ ours. Ours sometimes wins (the arg-bound
trifecta confirmation latch is sharper than stock human-in-the-loop).

## Version policy

- **Floors are tested versions.** `pyproject.toml` pins `>=` to the exact
  versions `make ci` last ran green against. Bump floors *with* the lock, not
  ahead of it.
- **Supply-chain buffer.** `[tool.uv] exclude-newer = "7 days"` means the
  resolver never takes a release younger than a week — a routine
  `uv lock --upgrade-package langchain --upgrade-package langgraph …` lands on
  the newest *seasoned* release. Don't fight it; it once kept a day-old
  release out of the tree by design.
- **Cadence.** Minor bumps are routine maintenance gated by `make ci`.
  Watch upstream deprecation signals for the 2.x major: because of the seam,
  a major is an `agent_loop.py`/`loop_middleware.py` event plus a lock bump —
  budget it as such, not as a rewrite.
- **Known deliberate gap.** `langgraph-checkpoint-sqlite` is *not* a
  dependency: `CHECKPOINT_BACKEND=sqlite` currently degrades gracefully to
  in-memory (see `checkpoint.py`). Add the package deliberately when durable
  resume ships — and keep the persistence format behind `CheckpointManager`
  so the resume store stays ours even if the runtime state is LangGraph's.
- **Deprecation watch.** `langchain-google-vertexai` 3.x deprecates
  `ChatVertexAI` (removal signalled for 4.0) and renamed `request_timeout` →
  `timeout` (the old kwarg was silently swallowed — already fixed in
  `llm_factory.py`). Budget the class migration before taking the 4.x major.

## The durable-resume decision gate

Durable resume across process restarts (reliability roadmap) is the deepest
lock-in commitment available on this stack: it would serialize the loop's
internal state as our persistence format. Order of operations, on purpose:

1. Run the middleware rollout first (the flag + eval gate above) — it proves
   whether in-loop needs are met *without* owning the loop.
2. Only then choose the durable-resume substrate, and keep it behind
   `CheckpointManager` either way.

## History

This doc distills the 2026-07 LangChain/LangGraph coupling review, which
measured the pre-seam state (langgraph imported in exactly one file; 14
scattered `create_agent` call sites across ten modules, passing 3 of its 14
parameters; zero middleware usage on a 1.x engine) and set the direction
codified here: shallow, seamed, measured — the stack as leverage, never as
architecture.
