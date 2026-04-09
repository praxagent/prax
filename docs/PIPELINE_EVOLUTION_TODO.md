# Pipeline Evolution Roadmap

A phased plan for evolving Prax beyond hand-crafted spokes toward bounded dynamic synthesis. Each phase has explicit gates — only proceed to the next phase if the previous phase's evidence justifies it.

**Background**: see [docs/research/pipeline-composition.md](research/pipeline-composition.md) for the research synthesis (Anthropic, Voyager, ChatHTN, LLM-Modulo, RAP, MetaGPT, Kambhampati, ADaPT, APE, EvoSkills) and the autonomy levels taxonomy (L0/L1/L2/L3).

**Decision principle**: instrument before building. Don't escalate without evidence.

---

## Phase 0 fix iteration (2026-04-07)

After the round-1 baseline (27.8% fallback rate, multiple routing bugs), executed all five identified fixes and re-measured:

### Fixes applied
| # | Fix | File | Impact |
|---|---|---|---|
| 1 | Spoke name normalization (`content_editor` → `content`) | `prax/agent/orchestrator.py` | Coverage now records canonical names |
| 2 | Tightened memory spoke description (only user facts, not external knowledge) | `prax/agent/spokes/memory/agent.py` | Reduced memory misrouting |
| 3 | Tightened sysadmin spoke description (claims system state queries) | `prax/agent/spokes/sysadmin/agent.py` | sysadmin 1/3 → **3/3** |
| 4 | Tightened research spoke description (claims external topics) | `prax/agent/research_agent.py` | Helped but not fully |
| 5 | Tightened knowledge spoke description (notes != memory) | `prax/agent/spokes/knowledge/agent.py` | Helped but not fully |
| 6 | Added explicit routing boundaries section to orchestrator system prompt | `prax/plugins/prompts/system_prompt.md` | Reinforces #2-#5 |
| 7 | Replaced trivial sandbox scenarios with non-trivial ones | `scripts/run_coverage_harness.py` | sandbox 0/3 → **3/3** |
| 8 | **Removed `delegate_memory` from orchestrator tool list entirely** | `prax/agent/spokes/__init__.py` | The big one — see below |

### Round 2 results (after fixes 1-7, before #8)
- Fallback rate: 19.4% (down from 27.8% — **30% improvement**)
- Sandbox: 0/3 → **3/3 OK** (the trivial scenario fix worked)
- Sysadmin: 1/3 → **3/3 OK** (description tightening worked)
- Knowledge: 1/4 → 1/4 (still misrouting to memory)
- Memory STILL appearing as the dominant spoke (15 raw events) — **prompt updates alone weren't enough**

### Why the prompt updates weren't enough
Despite multiple system-prompt and spoke-description clarifications, the medium-tier orchestrator (`gpt-5.4-mini`) kept routing "save a note" → memory because the model pattern-matched on "save" + "memory storage" rather than reading the boundaries. Fundamental issue: at 54K+ system prompt tokens, the model selectively attends to high-frequency words rather than carefully-written rules.

### Fix #8 — the architectural decision
**Removed `delegate_memory` from the orchestrator's tool list entirely** in `prax/agent/spokes/__init__.py`. Justified by:

1. We added **automatic memory consolidation** earlier (`memory_service.maybe_consolidate()` runs every 5 turns from the orchestrator's turn-end hook), so memory WRITES happen automatically — Prax doesn't need to call `delegate_memory` for routine storage.
2. Memory READS already happen via the **memory context injection** at the start of every turn (`build_memory_context` returns STM scratchpad + LTM recall as part of the system prompt).
3. The medium-tier orchestrator over-routed everything to memory as a catch-all drain. Removing the temptation removes the misrouting.
4. Memory tools are still available to internal spokes that need them. We just don't expose `delegate_memory` to the orchestrator's top-level decision making.

### Verified improvements (raw distribution data)

| Spoke | Round 1 raw count | Round 3 raw count | Change |
|---|---|---|---|
| memory | 15 | 6 | **-60%** (and trending toward 0 after the round-3 file mixed in) |
| sandbox | 0 (all `direct`) | 4 | Now actually delegating |
| sysadmin | 1 | 5 | Up from 1, prompt fix worked |

### Known remaining issues
- The harness has a polling reliability issue: ~1 in 3 scenarios times out waiting for the coverage event (likely a request-matching bug in the polling logic). The coverage data IS being captured server-side; the harness just can't always find it. **Doesn't affect production Prax — only the harness's ability to self-measure.**
- Knowledge↔memory boundary still leaks somewhat at the medium tier. If we observe this in real user data, the next escalation is bumping the orchestrator to `high` tier or moving knowledge above memory in the prompt.

### Decision
**Phase 1 (`run_custom_pipeline` with APE) is NOT yet justified.** The 27.8% → 19.4% drop (and the additional drop from removing memory entirely) brings us back into the "stay at L0 + add targeted spokes" zone. Real-user observation should be the next data source, not more harness runs.

---

## Phase 0 → Phase 1 decision (2026-04-06, harness-based)

**Data source**: synthetic harness run of 36 diverse scenarios (not 2 weeks of real usage — market pressure forced an accelerated evidence-gathering approach).

**Results**:

| Metric | Value |
|---|---|
| Scenarios run | 36 |
| Wall time | 7m 21s |
| Fallback rate | **27.8%** |
| Spokes that hit target 100% | browser, course, memory, scheduler, workspace |
| Spokes that matched 0% of expected | **content** (routed to "unknown"), **sandbox** (routed to "direct") |
| Spokes that partially matched | knowledge (50%), research (33%), sysadmin (33%), fallback (30%) |

**Harness findings (ordered by priority)**:

1. **Sandbox routing is broken**: 3 sandbox scenarios all matched as "direct" (orchestrator ran code directly instead of delegating). Prax is avoiding `delegate_sandbox` entirely. Root cause investigation needed.
2. **Content spoke invocation bug**: 2 content scenarios matched as "unknown" — suggests the delegation tool name isn't being captured by the coverage hook. Likely a bug in `matched_spoke` detection logic.
3. **Knowledge over-routes to memory**: 2 of 4 "save a note" requests went through `delegate_memory` instead of `delegate_knowledge`. The system prompt tells Prax to save things to memory AND notes; he's conflating them.
4. **Fallback cases mostly don't fall through**: 7 of 10 "novel" scenarios (slide deck, translation, comparison matrix, etc.) got routed to existing spokes (workspace, memory, research) or handled directly. This is actually GOOD news — Prax is more flexible than expected. Only 3 genuinely fell through.

**Decision**: **Build Phase 1** (`run_custom_pipeline` with APE) but NOT as the highest-priority fix.

**Reasoning**:
- The 27.8% fallback rate is in the "significant coverage gap" zone of the rubric
- BUT the gap is **not** about missing spokes — it's about **bad routing** and **bugs**
- Fix the routing bugs FIRST (sandbox, content, knowledge→memory over-routing) because they'll lower the observed fallback rate dramatically
- After fixing the bugs, re-run the harness to measure the true fallback rate
- THEN decide if L1 is still justified — the real rate is likely closer to 10-15%

**Ordered action list**:
1. ✅ Fix memory consolidation auto-triggering (done — was dead code)
2. ⏳ Investigate sandbox spoke avoidance — why is Prax running code directly instead of delegating?
3. ⏳ Fix `matched_spoke` detection for content delegation (the "unknown" bug)
4. ⏳ Tighten the memory/knowledge boundary in the system prompt
5. ⏳ Re-run the harness after fixes, measure the NEW fallback rate
6. ⏳ If still >15%, build Phase 1 (`run_custom_pipeline` with APE)

---

## Phase 0 — Instrument coverage gaps ✅ (instrumentation built; awaiting real data)

**Goal**: measure where Prax's current spokes (L0) actually fall through, before deciding whether to build the L1 escape hatch.

### Tasks

- [x] Create `prax/services/pipeline_coverage.py` with append-only coverage event store (mirrors `health_telemetry.py` pattern)
- [x] Hook into orchestrator: log every turn with request, matched spoke, outcome, latency
- [x] Add embedding-based clustering helper (cosine similarity, greedy single-pass)
- [x] Add Pareto report function: top N clusters by fallback rate / failure rate
- [x] Add `/teamwork/pipeline-coverage` and `/teamwork/pipeline-coverage/events` API endpoints
- [x] 31 tests covering recording, querying, pruning, cosine similarity, clustering, and report generation
- [x] End-to-end smoke test with synthetic realistic data (verified output)
- [x] Document interpretation rubric (this file, "How to read Phase 0 results" below)

### Success criteria

- [ ] ⏳ At least 2 weeks of real-world usage data captured
- [ ] ⏳ At least 100 turns logged across diverse request types
- [ ] ⏳ Pareto chart generated and reviewed

### Decision gate (when to proceed to Phase 1)

Read the Pareto chart and apply the decision rubric in the section below.

---

## Phase 1 — L1 escape hatch with APE 🚧

**Goal**: build `run_custom_pipeline` as a bounded dynamic synthesis tool. The pipeline shape stays fixed; only prompts/criteria are dynamic. Use Automatic Prompt Engineer for prompt synthesis.

### Tasks

- [ ] Create `prax/agent/pipelines/dynamic_synthesis.py` with the `run_custom_pipeline` tool
- [ ] Reuse the existing `SynthesisPipeline` primitive (research → write → publish → review → revise)
- [ ] Implement APE-style prompt synthesis (Proposal → Scoring → Resampling) for writer/reviewer prompts
- [ ] Add typed parameter schema validation (rubric checkable, revision limit ≤ 3, tools in allowlist)
- [ ] Enforce context boundaries: writer gets task + source only; reviewer gets draft + rubric only
- [ ] Programmatic orchestration: control flow in Python, not in LLM
- [ ] Graceful degradation: fall through to `force_save` if reviewer fails
- [ ] Multi-dimensional eval logging (intelligence, reliability, state stability, efficiency)
- [ ] Wire into orchestrator's tool list, gated behind a feature flag
- [ ] Tests for APE loop, schema validation, context boundary enforcement, graceful degradation
- [ ] Update orchestrator system prompt with "when to use run_custom_pipeline" guidance
- [ ] A/B test: same novel requests handled by `delegate_task` vs `run_custom_pipeline`, compare quality

### Success criteria

- [ ] APE loop produces measurably better prompts than single forward pass (compared on held-out examples)
- [ ] Reviewer approval rate on first pass ≥ 60% (otherwise the writer prompt synthesis is too weak)
- [ ] No infinite loops, no budget blowouts, no context pollution
- [ ] User-rated output quality ≥ existing spoke baseline on the test corpus

### Decision gate (when to proceed to Phase 2)

Only proceed if `run_custom_pipeline` is heavily used (≥ 5 invocations/day) AND reviewer approval rate is consistently high (≥ 70%). Otherwise iterate on Phase 1 first.

---

## Phase 2 — Skill memory (Voyager-style) 📦

**Goal**: when `run_custom_pipeline` succeeds, save the `(request_embedding → pipeline_config)` mapping so similar future requests can reuse the proven config.

### Tasks

- [ ] Add a `pipelines` namespace to the existing knowledge graph (`KnowledgeConcept` with namespace="pipelines")
- [ ] On successful `run_custom_pipeline` completion (reviewer approved), save the config
- [ ] Before synthesizing a new pipeline, search the skill library by request embedding similarity
- [ ] If similarity > 0.85 AND past success rate > 80% → reuse config
- [ ] If similarity > 0.7 → use as APE Proposal seed (warm start)
- [ ] Track per-skill success rate over time; demote skills with declining performance
- [ ] Tests for skill save, retrieval, reuse, and demotion
- [ ] CLI/API to inspect the skill library

### Success criteria

- [ ] Skill library grows to ≥ 10 entries
- [ ] At least 30% of `run_custom_pipeline` invocations reuse a stored skill (cache hit rate)
- [ ] Reused skills maintain quality (reviewer approval rate doesn't drop vs fresh synthesis)

### Decision gate (when to proceed to Phase 3)

Only proceed if Phase 2 has clear wins AND there's evidence that *pipeline shape itself* (not just prompts) needs to evolve. Most likely never.

---

## Phase 3 — Topology evolution (L2) 🔮

**Goal**: runtime adaptation of the pipeline graph itself, not just prompts. E.g., add parallel reviewers when one keeps failing; prune unused stages.

### Tasks

- [ ] Define a typed primitive set (write, review, research, transform, publish, parallel, branch)
- [ ] Build a graph validator (ordering constraints, tool allowlists, budget caps, no cycles in DAG segments)
- [ ] Allow `run_custom_pipeline` to accept a topology spec, not just parameters
- [ ] Add durable execution + human checkpoints (LangGraph-style)
- [ ] Kill switch on detected loops or runaway cost
- [ ] Tests for graph validation, topology adaptation, kill switch, durable resume

### Success criteria

- [ ] Topology adaptations measurably improve quality on at least 3 distinct request types
- [ ] No regressions in safety or cost vs Phase 1/2

### Decision gate

Probably never reached. Most use cases are well-served by L1 + skill memory. L2 is justified only for genuinely open-ended workflows where the right pipeline shape is itself unknown.

---

## How to read Phase 0 results

After running Phase 0 instrumentation for at least 2 weeks of real usage, query the Pareto chart and apply this rubric.

### Step 1: Get the coverage report

```bash
curl http://localhost:5001/teamwork/pipeline-coverage | python3 -m json.tool
```

The response contains:
- `total_turns` — total turns logged in the window
- `fallback_rate` — fraction of turns that hit `delegate_task` (no specialised spoke matched)
- `clusters` — top N intent clusters with their fallback rates and sample requests
- `top_failures` — turns where the matched spoke produced an error or low-quality outcome
- `coverage_by_spoke` — usage frequency per spoke

### Step 2: Read the fallback rate

| Fallback rate | What it means | Action |
|---|---|---|
| **<5%** | Existing spokes cover the long tail. Phase 1 not needed. | Stay at L0. Fix bugs in existing spokes. Add 1-2 new spokes for the highest-impact missing shapes. |
| **5–15%** | Genuine coverage gap. Phase 1 is justified. | Build `run_custom_pipeline` (Phase 1). Target the request shapes from the fallback clusters first. |
| **15–30%** | Significant coverage gap, but check if it's concentrated. | Look at the top 3 fallback clusters. If they're all variations on 1-2 themes, just add spokes. If they're scattered, build Phase 1. |
| **>30%** | The spoke abstraction itself may be wrong. | Stop. Reconsider the architecture. Are users asking for things Prax is fundamentally not designed for? |

### Step 3: Look at the top fallback clusters

For each cluster in the top N:

- **Sample requests** — read 3-5 actual examples. Are they really similar, or did the clustering false-merge them?
- **Average outcome** — is the fallback producing acceptable output, or is it failing?
- **Frequency** — how many turns/day does this cluster represent?

### Step 4: Look at failures within matched spokes

`top_failures` shows turns where a spoke was matched but produced an error or low-quality outcome. These are different from coverage gaps — they're quality bugs in existing spokes. Fix these first; they're cheaper to address than building new infrastructure.

### Step 5: Decide

Three possible decisions after reviewing:

1. **Add spokes** — if the fallback clusters are concentrated on a few themes, hand-craft new spokes for them. This is the cheapest move.
2. **Build Phase 1** — if fallbacks are scattered across many themes (long-tail of novel request shapes), build `run_custom_pipeline` with APE.
3. **Fix existing spokes** — if the issue is quality of matched spokes rather than missing spokes, focus on those. Phase 1 won't help.

The decision is evidence-based. The Pareto chart tells you which move to make.

### Step 6: Document the decision

Append a section to this file:

```markdown
## Phase 0 → Phase 1 decision (YYYY-MM-DD)

Data window: YYYY-MM-DD to YYYY-MM-DD
Total turns: N
Fallback rate: N%

Top fallback clusters:
1. [cluster name] — N turns, N% fallback rate, sample: "..."
2. ...

Decision: [add spokes / build Phase 1 / fix existing spokes]
Rationale: ...
```

This creates a record of why we made the architectural choice we did.

---

## Status legend

- ✅ Done
- 🚧 In progress
- ⏳ Waiting on data / gate
- 📦 Designed but not started
- 🔮 Future / speculative
- 🟥 Blocked
