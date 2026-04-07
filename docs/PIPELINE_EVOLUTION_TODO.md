# Pipeline Evolution Roadmap

A phased plan for evolving Prax beyond hand-crafted spokes toward bounded dynamic synthesis. Each phase has explicit gates — only proceed to the next phase if the previous phase's evidence justifies it.

**Background**: see [docs/research/pipeline-composition.md](research/pipeline-composition.md) for the research synthesis (Anthropic, Voyager, ChatHTN, LLM-Modulo, RAP, MetaGPT, Kambhampati, ADaPT, APE, EvoSkills) and the autonomy levels taxonomy (L0/L1/L2/L3).

**Decision principle**: instrument before building. Don't escalate without evidence.

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
