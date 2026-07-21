# FailureAtlas — silent failure modes in multi-provider LLM gateways (2026-07-21)

**Source:** [Vishal-sys-code/failure-atlas](https://github.com/Vishal-sys-code/failure-atlas)
(MIT, early-stage — ~7 commits, paper "coming soon"). An open **taxonomy + curated
catalog** of failure modes specific to multi-provider LLM *serving infrastructure* —
the gateway/proxy tier (LiteLLM, Portkey, OpenRouter, and now **our own
[`prax-secrets-proxy`](https://github.com/praxagent/prax-secrets-proxy)**).

**Verdict: document + partially-adopt the *lens*, not the code.** The repo itself is
immature (few catalog entries, no released paper, no tests we'd depend on). But its
central framing is sharp and *timely* — we just shipped a proxy that sits in exactly
this tier, and Prax runs a cross-provider failover path
([`prax/agent/llm_fallback.py`](../../prax/agent/llm_fallback.py),
`LLM_FALLBACK_ENABLED`). We adopt its **two-axis taxonomy as a hardening/test lens**
for both, and turn its named failure modes into a concrete "tests the proxy should
pass" checklist. See the [adopt-tracker](adopt-tracker.md) rows.

## The one idea worth internalizing

> **The most operationally severe failures in a multi-provider LLM architecture are
> *silent*: the gateway returns `HTTP 200 OK` while the *semantic* payload is
> corrupted.**

Traditional monitoring (APM, 5xx alerts, latency graphs) is blind to this class by
construction — the bytes flowed, the status was 200, the dashboard is green, and the
*content* is wrong. This is the same "audit the check, not just the output" instinct
that runs through our eval-rigor work: a green signal that measures the wrong thing
is worse than a red one. For a credential-injecting proxy the stakes are higher than
for a plain gateway — a corruption that crosses a request boundary can cross a
*trust* boundary.

## The taxonomy (this is the part to keep)

Two orthogonal axes — classify every gateway failure by both:

1. **Layer (origin):** Network/Transport · Streaming/Protocol · State/Session ·
   Model Behavior · Governance/Cost.
2. **Detectability:** **Loud** (HTTP-observable, caught by ordinary monitoring) vs.
   **Silent** (requires *semantic* evaluation to detect).

The catalog's named modes, mapped to that grid and then to Prax:

| Failure mode | Layer | Loud/Silent | Mechanism | Prax / proxy exposure |
|---|---|---|---|---|
| **Context bleeding** | State/Session | 🔇 Silent | one request's data leaks into another via shared/mutable proxy state | **Low by construction** — `prax-secrets-proxy` is *stateless per request* (no cache, no history, per-call `inject_auth`). A cross-tenant leak here would also be a *credential/trust* leak → the highest-value test to keep passing. |
| **Tool-call index collisions** | Streaming/Protocol | 🔇 Silent | concurrent requests misalign function-call indices → the *wrong tool* runs | Real risk in any layer that reassembles streamed tool-call deltas by index. The proxy passes bytes through untouched (safe); **Prax's own streaming assembly** is where to look. |
| **Conversation-history mutation** | State/Session | 🔇 Silent | multi-turn context corrupts/reorders across requests | Low in the proxy (stateless); relevant to Prax's conversation store + context assembly. |
| **KV-cache mutation** | State/Session | 🔇 Silent | corruption in a cached key-value store | N/A to the stateless proxy; a caution *if* we ever add prompt-caching in front of it. |
| **Silent model substitution** | Governance/Cost | 🔇 Silent | a provider serves a *different model* than requested, no notification | **Directly relevant to failover** — `llm_fallback.py` swaps providers on error; a third-party base URL (OpenRouter/DeepSeek via the proxy's `PROXY_OPENAI_BASE_URL`) can also substitute. We should *record which model actually answered*, not which we asked for. |
| **SSE chunking errors** | Streaming/Protocol | 🔇 Silent | Server-Sent-Event frames malform mid-stream; the client sees a truncated-but-200 answer | The proxy streams via `iter_content` passthrough (doesn't reframe → safe), but a truncated upstream stream still returns 200. Worth a **completion sentinel** check. |
| **Retry storms** | Governance/Cost | 🔊 Loud-ish | retry logic cascades into a request flood | The proxy has **no retry logic** (deliberate). Retries live in Prax's failover — bound them (caps + backoff + jitter) so a provider blip can't fan out. |
| **Rate-limit deadlocks** | Governance/Cost | 🔊 Loud | mutual blocking under rate-limit backpressure | Failover-adjacent; ensure a 429 fails *over*, never *blocks*. |
| **Sync-blocking-async** | Network/Transport | 🔊 Loud | synchronous code stalls an async event loop | The proxy is sync `requests` under threaded gunicorn (`-k gthread`), so a slow upstream ties up a *thread*, not an event loop — acceptable at our scale, but the reason we chose threaded workers, not a single async loop. |
| **Race conditions** | State/Session | 🔇 Silent | non-atomic state mutation under concurrency | The proxy holds no per-request mutable state → immune by construction. A property to *preserve* if the proxy ever grows features. |

## What this validates about what we already built

The exercise is reassuring more than alarming: **`prax-secrets-proxy` is stateless
and pass-through by design**, which makes it immune-by-construction to the *worst*
(silent, State/Session) half of the atlas — context bleeding, KV-cache/history
mutation, race conditions. That was an implicit design choice; the atlas makes it an
*explicit, testable invariant*: "the proxy MUST remain stateless per request." The
residual exposure is concentrated in two honest places:

- **The failover path** (`llm_fallback.py`) — silent model substitution + retry
  storms + rate-limit deadlocks are Governance/Cost failures that live wherever we
  *switch providers*, not in the proxy.
- **Prax's own stream reassembly** — tool-call index alignment is a Prax concern the
  moment we parse streamed tool-call deltas ourselves.

## Honest limits of the source

- It's a **taxonomy, not a validated benchmark** — few catalog entries, reproductions
  are illustrative simulations, the paper isn't out. We adopt the *vocabulary and the
  loud-vs-silent frame*, not any numbers or code.
- Its scope is the **gateway tier**, which is narrower than Prax (it says nothing
  about planning, memory, or tool *selection*). It complements, doesn't overlap,
  [prax-benchmarks](prax-benchmarks.md) (capability/safety) — this is
  *infrastructure reliability*.
- Overlaps our own thesis: the "silent 200" insight is the same "audit the check"
  discipline from the eval-rigor cluster, applied one layer down.

## Adopt punch-list (tracked in [adopt-tracker](adopt-tracker.md))

1. **Silent-failure test suite for `prax-secrets-proxy`** — turn the applicable modes
   into keyless tests/invariants: *stateless-per-request* (no cross-request bleed
   under concurrency), *stream integrity* (a truncated upstream is surfaced, not
   silently 200-ed), *auth isolation* (already tested). The proxy's whole value is
   trust; these guard it.
2. **Record the model that actually answered** (not just the one requested) on the
   failover path — the cheapest defense against silent model substitution; feeds the
   hallucination-guard / provenance story.
3. **Bound failover retries** — explicit cap + backoff + jitter in `llm_fallback.py`
   so a provider blip can't become a retry storm; a 429 must fail *over*, not block.
4. **Loud-vs-silent as a standing lens** — when we add any middle-tier component
   (caching, an inspector, Tier-2 egress proxy), ask "what silent-200 corruption
   could this introduce?" as a design-review question.

None of these are large; #1 is the natural next increment on the proxy and gives it
a reliability story to match its security story.
