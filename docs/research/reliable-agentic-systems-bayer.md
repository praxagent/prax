# Reliable Agentic AI Systems — the Bayer/PRINCE case study, mapped to Prax

[← Research](README.md)

> **Reference note.** Source: Sarang Sanjay Kulkarni (Principal Consultant,
> Thoughtworks), *"Building Reliable Agentic AI Systems: A Case Study in building
> production-ready agentic AI systems."* martinfowler.com —
> <https://martinfowler.com/articles/reliable-llm-bayer.html>
>
> The article documents **PRINCE**, a regulated multi-agent RAG assistant Bayer built
> over decades of pharmaceutical-safety data. This note (a) summarises its argument,
> (b) maps each principle against Prax's **shipped code**, and (c) records where it
> genuinely inspires change. The matrix was built by a multi-agent audit and then
> adversarially re-verified against the repo — every "strong"/"partial" row is backed
> by real code at the cited `file:line`; aspirational `docs/plans`, `*_TODO`, and
> backlog files were explicitly excluded as evidence.

## Why this is here

PRINCE's thesis is that production reliability comes **not from better models or prompts**
but from two engineering disciplines built *around* the model:

1. **Context engineering** — *"how information was shaped and routed between specialized
   agents."* Each agent gets only the slice it needs; avoid monolithic prompts and
   context pollution. *"Larger context windows did not eliminate the need for selectivity."*
2. **Harness engineering** — *"how orchestration, recovery, and observability were built
   around the models to maintain control and reliability."* LangGraph control layer,
   state persistence, retries/fallbacks, reflection loops, evals, tool boundaries, human
   review.

This is the same thesis as Prax's own [Harness Engineering](harness-engineering.md) note
("the environment is the agent"), arrived at from a regulated-RAG angle. So the article is
mostly a **validation** of Prax's architecture — with a few sharp, specific places it
exposes a real gap.

## TL;DR verdict

**Prax already embodies the two-pillar thesis.** Of ~41 mapped principles:

- **~19 strong** — hub-and-spoke per-spoke tool/prompt scoping, selective per-turn context
  injection, LangGraph control layer, node-level rollback-retries, unified multi-provider
  factory, per-provider circuit breaker, OTel/Tempo/Prometheus tracing+metrics, hybrid
  (dense+sparse+graph RRF) retrieval, deterministic claim-audit grounding gates,
  HIGH-risk confirmation, confidence-gated suppression of unattended output.
- **~12 partial** — present but narrower than the article (monolithic orchestrator prompt;
  per-stage discipline only inside the synthesis pipeline; risk-tier-not-deny-by-default
  tool boundaries; in-memory-only checkpoints; single-axis eval judge; prompt-only citations).
- **7 genuine gaps (absent)** — see "Where it inspires change" below. **All 7 were subsequently
  implemented (2026-06)**; the matrix rows and status table reflect the shipped state.
- **3 not-applicable** — Text-to-SQL schema injection, SELECT-only SQL guard, and pharma
  NER / regulatory per-page citations are PRINCE-domain-specific (Prax exposes no SQL surface
  and has no regulated document corpus).

**Top five inspired changes** (all verified low-cost, high-leverage): cross-provider LLM
fallback · automated/continuous evaluation · durable checkpoint + user-initiated resume ·
within-turn recovery-context injection · orchestrator-prompt selectivity.

It **did** inspire change — every candidate below was implemented (2026-06); see the status
table at the head of "Where it inspires change".

## Alignment matrix

Legend: ✅ strong · ◐ partial · ❌ absent (genuine gap) · — not applicable (domain-specific).

### 1. Context engineering

| Article principle | Prax | Evidence (verified) |
|---|---|---|
| Scope each sub-agent to its own toolset + prompt | ✅ | Hub-and-spoke: `spokes/_runner.py:144` builds a fresh ReAct agent with **only** that spoke's tools + its own `SYSTEM_PROMPT`; orchestrator sees only `delegate_*`. 12 always-on spokes + desktop/sandbox gated (`spokes/__init__.py:23-75`). |
| Selective context routing (only what each stage needs) | ✅ | `workspace_service.py:626` injects only token-overlap-relevant user-note lines (not the whole file); memory recall is top-5 + 500-token cap (`memory_service.py:306`); plan is compact-rendered (`workspace_service.py:1131`). |
| Avoid monolithic oversized prompts | ◐ | Honored for **history/tool-results** (`context_manager.py:323` compaction, per-model windows) but **not** the static prompt: `system_prompt.md` is ~77 KB / ~19K tokens, ~30 sections, shipped every turn (`orchestrator.py:662-672`). |
| Per-stage context discipline (plan/retrieve/evidence/synthesise) | ◐ | Real per-phase pipeline exists but only in `pipelines/synthesis.py` (used by content + knowledge spokes). The **primary** interactive path is one broad ReAct loop. |
| Schema-injection optimization (inject only relevant schema) | — | PRINCE Text-to-SQL specific; no SQL/schema-in-prompt surface anywhere in Prax (grep empty). |

### 2. Orchestration, topology & tool boundaries

| Article principle | Prax | Evidence (verified) |
|---|---|---|
| LangGraph as the control layer | ✅ | `orchestrator.py:137` builds the ReAct graph w/ checkpointer; per-turn recursion limit + OTel callbacks; rollback-retry loop `_invoke_with_retry` (`orchestrator.py:1003-1178`). |
| Evolve monolith → domain sub-agents | ✅ | 15 spoke dirs, 14 routable `delegate_*`; memory deliberately de-routed after a documented over-routing incident (`spokes/__init__.py:26-37`). Content is a true sub-hub (writer/reviewer/publisher). |
| Controlled tool boundaries (only permitted ops) | ◐ | One choke point (`tool_registry.py` → `wrap_with_governance`) with a risk-tier map + confirmation gate. **But** unknown tools default to MEDIUM-and-run (`action_policy.py:213`), not deny-by-default; one HIGH confirm unlocks **all** HIGH for the turn (`governed_tool.py:254`). |
| SELECT-only SQL guard | — | No agent-facing SQL surface (grep empty). |
| Intent clarification as a cheap first step | ❌ | Exists only as prompt guidance (`system_prompt.md`), and the prompt actively biases *against* asking ("clarification questions cost momentum"). No code-level pre-flight gate before delegation. |

### 3. Reflection & planning loops

| Article principle | Prax | Evidence (verified) |
|---|---|---|
| Process reflection / Think-&-Plan node | ◐ | `think()` scratchpad (`workspace_tools.py:1252`) + `agent_plan` plan-before-work, nudged by `_classify_complexity` + enforced by a continuation loop. But it's a skippable ReAct tool, not a structured pre-act node whose output constrains tool choice. |
| Data reflection (evidence sufficient? loop back) | ◐ | Research prompt says "search broadly / cross-reference / flag gaps" (`research_agent.py:46`) but there is **no programmatic sufficiency gate** that re-enters retrieval; the only structural re-entry is URL-fetch-**failure** (`orchestrator.py:766-781`). |
| Draft reflection (review draft before returning) | ✅ | `SynthesisPipeline` write→review→revise up to 3 passes with a cross-provider reviewer + 10-criterion note reviewer (`synthesis.py:233`, `deep_dive.py:97-175`). |
| Error-context propagation | ◐ | Machinery is strong (see §4) but the structured diagnosis is **logged, not re-injected** on the current retry. |

### 4. Recovery & reliability

| Article principle | Prax | Evidence (verified) |
|---|---|---|
| State persistence via checkpointer | ✅ | Pluggable checkpointer — `CHECKPOINT_BACKEND=sqlite` (`SqliteSaver`) persists data across restarts; `InMemorySaver` default. Failed turns kept (not purged) when `CHECKPOINT_RESUME_ENABLED`. (`checkpoint.py:_build_saver`) |
| Separation of agent-state vs app-state | ◐ | De-facto split (message-state in checkpointer; turn metadata in `TurnCheckpoint`) but not a formal typed boundary. |
| Built-in retries at multiple steps | ✅ | `_invoke_with_retry`: context-overflow retries (progressive compaction), invalid-checkpoint restart, budgeted rollback-retry (`orchestrator.py:1003-1178`). |
| Node-level retries (retry a whole step) | ✅ | `get_rollback_config` rolls back 2 checkpoints to the last clean decision point, resumes graph w/ `messages=None` (`checkpoint.py:127`, `orchestrator.py:1176`). |
| User-initiated resume from failure point | ✅ | `ConversationAgent.resume_last_turn()` continues the saved thread (skips completed steps); the resumable pointer is persisted to `.prax/resumable.json` so it survives a restart (`CHECKPOINT_RESUME_ENABLED`). Documented reset path. |
| Cross-provider LLM fallback after retries | ❌ | Retries re-run the **same** graph bound to the **same** provider; circuit breaker only fails fast. **The article's single biggest reliability lever, and Prax already has the unified factory to do it cheaply.** |
| Unified multi-provider interface | ✅ | `llm_factory.build_llm()` returns a uniform model across openai/anthropic/google/ollama/vllm; reused by `multi_model` for consensus. |
| Multi-provider circuit breaker | ✅ | Full CLOSED→OPEN→HALF_OPEN per provider, wired into `build_llm` + OTel callbacks + health monitor. *Bug:* `on_llm_error` hardcodes `provider="openai"` (`callbacks.py:164`) so non-OpenAI failures mis-attribute. |

### 5. Observability & evaluation

| Article principle | Prax | Evidence (verified) |
|---|---|---|
| Rich tracing of all traffic (Langfuse-equiv) | ✅ | Custom execution-graph tracer → OTLP/Tempo; OTel callbacks on **every** LLM/tool (`trace.py`, `callbacks.py`, attached at `llm_factory.py:152`); `.prax/graphs/*.jsonl`, 7-day rotation. |
| System metrics (CloudWatch-equiv) | ✅ | 6 Prometheus families (`metrics.py`), `/metrics` endpoint, full LGTM stack + Grafana dashboards; agent self-queries via `obs_query_{logs,metrics,traces}`. |
| Dataset evals on prompt/model/workflow change | ◐ | `eval/runner.py` (replay failure-journal + LLM judge) and `eval/gaia_single.py` exist — but only fire via a manual HTTP route; **CI runs only ruff + pytest**, no eval gate. |
| RAGAS metric suite (decomposed axes) | ❌ | Single holistic 0–1 judge score; no faithfulness/answer-relevancy/context-relevancy decomposition (grep empty). Can't localise *which* dimension regressed. |
| Live-traffic evals as daily batch | ❌ | Traffic is fully traced but nothing scores it on a schedule; scheduler has CronTrigger but no eval job. **The biggest eval-side gap — substrate already exists.** |
| Testing pyramid (per-step AND end-to-end) | ◐ | e2e (`ScriptedLLM`), integration (real-LLM + judge), per-spoke routing harness — but they live in separate manually-run tools, not one reported pyramid; coverage harness not in CI. |
| Continuous hallucination/bias monitoring | ✅ | Inline `claim_audit.py` (ungrounded numbers/news/weather) + `semantic_entropy.py` (k=3 agreement gate). *Gap:* per-turn, not a trended `prax_hallucination_*` metric; no bias monitoring. |

### 6. Retrieval, grounding, transparency & human-in-the-loop

| Article principle | Prax | Evidence (verified) |
|---|---|---|
| Hybrid retrieval (weighted semantic+keyword) | ✅ | `retrieval.hybrid_search()` fuses dense+sparse+graph via weighted RRF (`retrieval.py:38`). The **knowledge-graph** path now also does true hybrid (dense+sparse vectors in `prax_knowledge_concepts` fused with the keyword arm — `knowledge_vectors.py` + `knowledge_graph.search_knowledge`), degrading to substring when Qdrant is down. |
| Reranking (cross-encoder / second stage) | ✅ | LLM-judge rerank over the top fused candidates before truncation (`retrieval._rerank`, `RETRIEVAL_RERANK`). |
| Query expansion / rewriting (HyDE, multi-query) | ✅ | Paraphrase/HyDE variants embedded and unioned before RRF (`retrieval._expand_queries`, `RETRIEVAL_QUERY_EXPANSION`). |
| Metadata filtering | ✅ | Qdrant payload indexes (user_id/source/tags/created_at) + per-user hard scoping + namespace filters (`vector_store.py:76`). |
| Grounding to cut hallucination | ✅ | `claim_audit.py` verbatim-matches money/percent/ranking + narrative claims against actual tool results; epistemic source-reliability tags (VERIFIED/INDICATIVE/INFORMATIONAL). *Gap:* regex/substring, so paraphrase hallucinations pass. |
| Transparency of intermediate steps | ✅ | TraceEvents (TOOL_CALL/AUDIT/DECISION/THINK…), live Auditor channel, `trace_search`/`trace_detail`. *Gap:* doesn't surface the retrieved **chunks/scores** behind an answer. |
| Granular hoverable per-claim citations | ◐ | Prompt-instructed ("cite everything") + document-level `**Source:**` headers; no structural claim→span binding. |
| Human-in-the-loop / confirmation | ✅ | `governed_tool.py` HIGH-risk confirm-then-execute; risk declared at the tool site; imported plugins auto-HIGH. *Gap:* same-turn re-call, not a durable approval queue. |
| Confidence-gated automation (auto vs quarantine) | ✅ | Unattended scheduled briefings are suppressed/disclaimed below an evidence floor (`claim_audit.py:377`, `orchestrator.py:1429`). *Gap:* attended turns only post the flag internally, don't quarantine the reply. |
| Accuracy-before-cost / staged rollout | ◐ | Tiered models + difficulty routing + selective consensus give accuracy-when-it-matters; no canary/eval-gated promotion (largely an org concern). |
| Pharma NER + regulatory per-page citation | — | Domain-specific; Prax has generic personal-knowledge extraction, no regulated corpus. |

## Where it inspires change (verified, prioritized)

> **Status update (2026-06): all of the below shipped**, each behind an env flag that
> defaults to today's behaviour (so the eval gate — not a silent regression — governs when
> behaviour actually changes). Flags are documented in `.env-example`. Summary:
>
> | Item | Flag(s) | Default | [Eval-gate verdict 2026-07-08](flag-eval-campaign-2026-07-08.md) |
> |---|---|---|---|
> | P1 cross-provider fallback | `LLM_FALLBACK_ENABLED` (+`LLM_FALLBACK_CHAIN`) | off | not benchmark-measurable (fault injection); needs 2nd provider key |
> | P2 continuous/decomposed evals | `EVAL_NIGHTLY_ENABLED`, `make eval` | off / manual | (is the gate itself) |
> | P3 durable checkpoint + resume | `CHECKPOINT_BACKEND=sqlite`, `CHECKPOINT_RESUME_ENABLED` (persisted pointer survives restart; reset via the state file / flag) | off | judgment item — unit-tested, low-risk |
> | P4 within-turn recovery injection | `RECOVERY_CONTEXT_INJECTION` | **on** | shipped on pre-gate |
> | P5 orchestrator-prompt selectivity | `PROMPT_SELECTIVITY_ENABLED` | off | **FLIPPED** — no regression, −2% tokens |
> | P6 intent-clarification gate | `INTENT_CLARIFICATION_ENABLED` | off | **REJECTED** — +11% tokens, no pass-rate gain |
> | P7 reranking + query expansion | `RETRIEVAL_RERANK`, `RETRIEVAL_QUERY_EXPANSION` | off | **DEFERRED** — no suite coverage; adds LLM calls |
> | P7 hybrid knowledge search | `KNOWLEDGE_HYBRID_ENABLED` (degrades to substring when Qdrant down) | **on** | shipped on pre-gate |
> | circuit-breaker attribution fix | — (always on) | fixed | — |
> | deny-by-default tools / scoped HIGH unlock | `UNKNOWN_TOOL_HIGH_RISK`, `HIGH_RISK_SCOPED_CONFIRM` | off | **REJECTED** — correctness regression (blocked a needed tool) |
> | hallucination-guard counter / attended quarantine / retrieval TraceEvent | `prax_hallucination_guard_total`, `CLAIM_AUDIT_ATTENDED_QUARANTINE` | on / off / on | quarantine **DEFERRED** — A/B inconclusive (dead search backend) |
> | in-loop middleware (langstack) | `AGENT_MIDDLEWARE_ENABLED` | off | **FLIPPED** — no regression, −7% tokens; security lift within noise at n=6 |

Each item is backed by the code anchors above. Ordered by leverage-to-cost.

> **Gate outcome (2026-07-08):** the first eval-gate run A/B'd these flags —
> verdicts, method, and honest caveats in
> [flag-eval-campaign-2026-07-08.md](flag-eval-campaign-2026-07-08.md).
> Flipped: `AGENT_MIDDLEWARE_ENABLED`, `PROMPT_SELECTIVITY_ENABLED`.
> Rejected on evidence: `INTENT_CLARIFICATION_ENABLED`, deny-by-default tool
> boundaries. Deferred: retrieval rerank/expansion, attended quarantine.

**P1 — Cross-provider LLM fallback.** *Gap §4.* When the primary provider keeps failing or
its breaker is OPEN, surface to the user instead of retrying on a second provider. Fix:
on retry / breaker-OPEN, rebuild the orchestrator LLM via `build_llm()` against an ordered
list of `(provider, model)` pairs (providers already enumerated in
`multi_model._available_providers`). *Article calls this "the single biggest reliability
gap." Cheap because the unified factory already exists.*

**P2 — Automated / continuous evaluation.** *Gaps §5.* (a) Wire `run_eval_suite` (or a frozen
golden set) into `make ci` / a scheduler job so a system-prompt or model-config diff can't
ship without a measured before/after. (b) Add a nightly cron (scheduler already supports
`CronTrigger`) that samples N traces from `.prax/graphs`, runs a reference-free judge, and
writes daily aggregate quality to Prometheus — turning the existing trace store into the
article's live-traffic eval. (c) Decompose the single judge score into orthogonal axes
(grounding/faithfulness, task-relevancy, correctness) so regressions localise.

**P3 — Durable checkpointing + user-initiated resume.** *Gaps §4.* Make the checkpointer
pluggable (`InMemorySaver` for lite, `SqliteSaver`/`PostgresSaver` for durable), defer
checkpoint deletion on failure/timeout (TTL), and add a "resume last turn" path that
re-invokes the existing `thread_id` with `messages=None` so "continue" actually skips
completed steps.

**P4 — Within-turn recovery-context injection.** *Gap §3/§4.* `build_recovery_context` is
computed then only `logger.info`'d (`orchestrator.py:1091-1096`); the retry rolls back with
`messages=None`, so the model never sees the diagnosis. Inject it as a `HumanMessage` on
retry — the exact pattern already used for plan/URL continuations (`orchestrator.py:758`).
Near one-line leverage.

**P5 — Orchestrator-prompt selectivity.** *Gap §1.* The ~77 KB prompt ships whole every turn.
Apply the relevance-scoring already used for user notes (`workspace_service.py:626`) to gate
large optional sections of `system_prompt.md` behind cheap intent signals, or split it into a
small always-on core + lazily-loaded capability modules (mirroring `progress_detail`'s
fetch-on-demand model).

**P6 — Intent-clarification pre-flight node.** *Gap §2.* Add a cheap low-tier gate before
delegation that, on requests flagged ambiguous-**and**-irreversible, returns a single
clarifying question instead of guessing — complementing the existing `estimate_difficulty` /
`_classify_complexity` turn-start estimators.

**P7 — Retrieval precision: reranking + query expansion + hybrid knowledge search.** *Gaps §6.*
**Shipped.** LLM-judge rerank over the top fused candidates (`RETRIEVAL_RERANK`); paraphrase/HyDE
variants unioned before RRF (`RETRIEVAL_QUERY_EXPANSION`); and `knowledge_search` now runs **true
hybrid** retrieval — concept embeddings in a `prax_knowledge_concepts` Qdrant collection
(`knowledge_vectors.py`) fused with the keyword arm, auto-indexed on write, with
`reindex_user_concepts()` for the backlog and graceful fall-back to substring when Qdrant is down.

**Smaller, mechanical wins.** Fix the circuit-breaker provider mis-attribution
(`callbacks.py:164` — read the real provider from the span / `_infer_provider_from_model`);
emit a `prax_hallucination_guard_total{type}` counter when `claim_audit`/semantic-entropy
fire so the guards become trended/alertable; make tool boundaries deny-by-default with a
per-component allowlist and scope the HIGH-confirm unlock to the specific tool; extend
attended-turn handling to quarantine/append claim-audit warnings (today only scheduled
turns do); surface retrieved chunks + RRF scores as a TraceEvent.

## Where Prax already goes *beyond* the article

The mapping isn't one-directional — several Prax mechanisms have no PRINCE analog:

- **Cross-run Reflexion memory** — metacognitive failure profiles with Ebbinghaus
  confidence decay (`metacognitive.py`), injected as warnings into future runs. PRINCE's
  reflection is within-run only.
- **Semantic-entropy gate** — samples HIGH-risk calls k=3 and blocks on low agreement
  (`semantic_entropy.py`).
- **Trace self-introspection** — `trace_search` over the agent's own past execution graphs
  ("have I solved this before?"), see [Harness Engineering §32](harness-engineering.md).
- **Multi-perspective error analysis** — deterministic logical/completeness/assumptions/
  alternative diagnosis (`error_recovery.py`).
- **Mechanical architecture enforcement** — `scripts/check_layers.py` layer linter in CI.
- **Epistemic source-reliability tagging** — a generalisation of the article's
  confidence-gating applied to *every* tool result, not just metadata extraction.

## See also

- [Harness Engineering](harness-engineering.md) — the sibling thesis (§28–32), from the
  SWE-agent / Anthropic / OpenAI-Codex angle.
- [Orchestration](orchestration.md) — bounded sub-agents, context management.
- [Grounding](grounding.md) — error recovery via checkpointing, anti-hallucination.
- [Model Routing](model-routing.md) — tiering / difficulty-driven routing.
- [Planning & Reflexion](planning-reflexion.md) · [Error & Metacognition](error-metacognition.md).
- [Checkpointing](../agents/checkpointing.md) · [Observability](../infrastructure/observability.md).
