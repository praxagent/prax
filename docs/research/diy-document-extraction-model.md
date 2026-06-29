# Building our own schema-constrained document-extraction model

Design note / build plan. **How we'd build a commercial-friendly equivalent of
[Datalab's Lift](lift-document-extraction.md)** — PDF/image + JSON Schema →
guaranteed-valid, well-typed JSON — reusing Prax's existing fine-tune + vLLM +
eval infrastructure. Synthesized from a code-grounded multi-agent audit; claims
are tagged **[verified-in-Prax]** (checked against the repo) or
**[external]** (best practice, verify against pinned versions).

## The insight that reframes everything

Lift's headline guarantee — *"always-valid, well-typed JSON matching your
schema"* — is a **decode-time property you get for free on any permissive base
model, with zero training.** Schema-constrained decoding is a logit processor:
the JSON Schema is compiled to a state machine and every token that would break
validity is masked to `-inf`, so the output *cannot* be malformed — no parse
errors, no retries. vLLM ships this natively (`guided_json` / `response_format:
json_schema`, xgrammar/guidance backends). [external]

So the build splits cleanly:

- **Valid + well-typed JSON = constrained decoding** → free, no training, days of wiring.
- **Correct *values* (Lift's ~90.2% field / ~20.9% full-doc) = base-model quality + fine-tune** → the expensive, optional part.

A useful, commercial-clean extractor therefore exists at the **end of Phase 0**;
Phases 1–2 only chase accuracy.

## Honest take first: build vs. just constrained-decode a frontier API

- **If you only need valid JSON from clean/born-digital docs:** OpenAI/Anthropic/
  Gemini structured outputs already do this today, zero infra. **Then don't build a model.**
- **The real reasons to build are narrow:** (a) **data residency / no-egress** —
  docs can't leave our infra (contracts, PHI, PII); (b) **unit cost at volume** —
  a self-hosted ~9B on one GPU beats per-token API pricing past a threshold;
  (c) **the hard tail** — scanned/handwritten/figure-heavy multi-page docs where a
  *domain-fine-tuned* VLM wins; (d) **shipping the weights** in a product. Lift
  exists for (a)+(c)+(d).
- **Brutal truth on accuracy:** matching Lift is a real training program dominated
  by *data-labeling* spend, not GPU. And Lift's own ~20.9% full-doc accuracy shows
  the ceiling is low — one wrong field on a 30-field doc fails the doc. Don't
  promise parity cheaply.

**Recommendation:** do Phase 0 regardless (cheap, commercial-clean, no-egress,
validity-guaranteed). Gate Phases 1–2 behind a concrete business case for (a)–(d)
*and* a labeled eval set proving the base model isn't already good enough.

## License posture (throughout)

Base on **Qwen3-VL-Instruct / Qwen3-8B (Apache-2.0)** — same lineage as Lift but
**redistributable**: we can sell and ship fine-tuned weights with no obligation to
Datalab. Avoid Llama (MAU gate + mandatory "Llama" naming on derivatives).
[external — verify exact HF commit license]. The contamination rule for Phase 1+:
**do not distill GPT/Claude/Gemini outputs into product weights** (every major API
ToS bars building competitive models — that voids an Apache release). Keep a
per-shard **data-provenance manifest** so the Apache claim is defensible.

## What Prax already gives us (≈70% scaffolded) [verified-in-Prax]

The fine-tune loop is base-model-agnostic orchestration and is directly reusable:

- **Fine-tune orchestration** — `prax/services/finetune_service.py`: harvest →
  JSONL → subprocess training → status-file polling → verify → promote → rollback
  (`run_self_improvement_cycle`), JSON adapter registry, **vLLM LoRA hot-swap** via
  `/v1/load_lora_adapter` (`finetune_service.py:296-339`). The `finetune` spoke
  (`prax/agent/spokes/finetune/agent.py`) is the flag-gated delegation pattern to clone.
- **vLLM serving rails** — `settings.vllm_base_url` (`settings.py:436`); the `vllm`
  provider in `llm_factory.py:222` builds a `ChatOpenAI` against it.
- **Multimodal inference plumbing** — `prax/agent/vision_tools.py` base64-inlines
  images as `data:` URIs to any OpenAI-compatible server (`VISION_BASE_URL`), the
  closest analog to Lift's interface (no egress for a local server).
- **Data-generation sources** — `pdf_service.py` (opendataloader-pdf) and
  `url_reader.py` (Jina). opendataloader's `convert()` also supports
  `format='markdown-with-images'` + page-image rendering, so the *same* dependency
  can emit page rasters (model input) **and** structured text (teacher/weak label).
- **GPU sandbox** — `make sandbox-gpu` + `docker-compose.gpu.yml` (reserves
  `count: all` GPUs).
- **Eval seams** — `prax/eval/goldens.py` has injectable `judge=`/`replay_fn=`
  hooks (`goldens.py:130,185`) — the exact seam to plug a deterministic extraction
  comparator into, with `eval_gate.py`'s `PRAX_EVAL_MIN_PASS_RATE` as the gate.

## The hard ≈30% that's greenfield [verified-in-Prax that it's absent]

1. A **deployed vLLM server with guided decoding** — there is no `vllm` service in
   any compose/k8s file and **zero** `guided_json`/`response_format` usage in code.
2. A **schema param threaded** through the vLLM provider + `vision_tools`
   (`vision_tools._analyze_openai` has no schema param and `max_tokens=2000` — too
   small for multi-page JSON).
3. A **deterministic extraction comparator** (schema-validity + per-field +
   full-doc accuracy) — no field-accuracy/JSON-validity scorer exists; `jsonschema`
   isn't even installed.
4. A **VLM training script** (current `scripts/finetune_train.py` is text-only
   `FastLanguageModel`, `max_seq_length=2048`) + a **PDF→image renderer**
   (`pypdfium2`/`pdf2image` absent).
5. **ML/constraint deps** — `vllm`, `unsloth`, `peft`, `trl`, `torch`,
   `bitsandbytes`, `xgrammar`, `jsonschema` all missing (training runs in a
   separate CUDA env by design).
6. **Operator scaffolding** — `FINETUNE_*`/`VLLM_*`/`DOC_EXTRACT_*` defaults exist
   in `settings.py` but are absent from `.env-example`.

## Phase 0 — Constrained-decoding MVP (days)

**Goal:** Lift's validity guarantee, commercial-friendly, no training.

1. Stand up a **vLLM server** for Qwen3-VL-8B-Instruct (or text-only Qwen3-8B for
   born-digital) with `--guided-decoding-backend guidance` (best schema coverage —
   bare xgrammar rejects `pattern`/numeric bounds/`minItems` and historically
   errors rather than degrading). Add a `vllm` service to `docker-compose.gpu.yml`.
2. Thread `extra_body={"guided_json": schema}` through the vLLM provider
   (`llm_factory.py:222`) and add a schema param + larger `max_tokens` to
   `vision_tools._analyze_openai`.
3. **Flag-gated `document_extract(path, schema)` spoke** — clone `spokes/finetune/`
   (the disabled-early-return + `build_spoke_tools` pattern). Add
   `settings.doc_extract_enabled` + `DOC_EXTRACT_BASE_URL`/`DOC_EXTRACT_MODEL`.
4. **Input routing:** born-digital PDF/URL → `pdf_service`/`url_reader` text path
   (cheaper, no image tokens); scanned/image → vision path (base64, no egress).
5. **One `kind: doc_extract` golden** scored by a **deterministic comparator**
   injected via the existing `run_golden_suite(replay_fn=..., judge=...)` seam —
   emitting schema-validity rate (≈100% proves the constraint is on), per-field
   accuracy, full-doc accuracy, latency p50/p95. *(A tracking golden for this is
   already in `prax/eval/goldens/document_extract.yaml`.)*

**Expected:** schema-validity ≈100%; field accuracy = whatever base Qwen3-VL gives
(well below Lift, but real and measured). **Compute:** one GPU to serve (~24GB for
8B). **License:** Apache-2.0 base, no training → fully clean.

**Risks:** constrained-decoding backend footguns (use `guidance`, pre-flight every
schema, keep to the safe subset Lift itself uses); *valid ≠ correct* (track field
accuracy separately or the "guaranteed valid" headline lies); field-order can cost
10–30% reasoning on ~9B (order rationale fields before committed values, or
two-pass reason→extract). [external]

## Phase 1 — SFT for accuracy on clean data (weeks)

Fork `scripts/finetune_train.py` (text-only → keep JSONL/subprocess/status-file
contract so `finetune_service` orchestration is untouched), add `FINETUNE_MAX_SEQ`
(2048 → 16k–32k for multi-page), train on
`{user: instruction+schema+doc, assistant: gold JSON}`.

**Data (the real cost driver — more human-hours than GPU-dollars):** synthetic
backbone where the JSON *is* the source of truth (own the layout + gold, sidesteps
copyright/ToS), ~50–100k pairs; permissive real corpora (CORD/SROIE/PubLayNet,
SEC/EDGAR, public-domain gov forms) with a human-verified golden slice; pilot 5k →
measure → scale. **Compute:** Qwen3-8B QLoRA fits ~24GB; long-context wants an
A100-80GB; ~30–80 GPU-hours (~$40–105 cloud) [external]. **Expected:** field
accuracy plausibly into the 70s–80s on covered doc types; full-doc stays low — make
**field accuracy** the headline metric.

**Data-constrained regime (we live here).** Prax fine-tunes have *limited unique
data* (5–100k pairs, not infinite tokens), so the pretraining intuition "more params
= better" inverts. From data-constrained scaling (Muennighoff 2023; Lovelace 2026,
via Lilian Weng's *"Scaling Laws, Carefully"*): **repeated data decays in value**,
so use **strong weight decay** to blunt the overfitting penalty, and prefer **more
epochs over more parameters** when you can't add unique examples. Concretely: don't
reach for a bigger base to fix accuracy on a fixed dataset — squeeze the data
(curate/dedup/reweight — *the* cost driver above) and tune epochs + weight decay
first. (The scaling-laws *pretraining* core is otherwise out of scope — Prax fine-
tunes, it doesn't pretrain.)

## Phase 2 — Vision + RL on field-accuracy (optional, the true Lift-equivalent)

**Why Phase 1 (SFT) must come before Phase 2 (RL) — the cold-start problem.**
(Principle worth holding for *any* RL fine-tuning Prax does, not just extraction.
Source: Patrick Toulme on GLM-5.2's training, 2026-06.) RL needs **positive
trajectories** — rollouts where the model actually completed the task and earned
reward. **No success on a task → zero reward → zero gradient → you cannot RL it.**
That's the cold start. The standard fix is to *seed* the model with successes from a
stronger model (distillation) until it produces positive trajectories, then GRPO to
hill-climb, then drop the seeding. **Prax's commercial-clean version of that seed is
Phase 1 itself:** SFT on synthetic/permissive gold data is what lifts the base model
over the zero-success hump — so by Phase 2 it already emits gradient-bearing
trajectories (exactly why the passport example starts at 87.6%, not 0%). Crucially,
Prax gets the cold-start seed from **SFT-on-permissive-data, NOT by distilling
Claude/GPT/Gemini** — the contamination rule above (frontier-API ToS bars building
competitive models and voids an Apache release) forbids the post's literal recipe.
So: **the SFT→RL ordering isn't a nicety, it's the cold-start requirement** — and
solving cold start without frontier distillation is precisely Prax's commercial-clean
differentiator. If a future RL run on some task shows ~no reward variance, that's the
cold-start signal: seed with more SFT (permissive/synthetic) before resuming RL.

**Why Prax needs no learned environment simulator.** Some agentic-RL work trains a
*world model* to simulate the environment for cheap rollouts (e.g. Qwen-AgentWorld,
arXiv 2606.24597) — but that only pays off when the environment is **expensive or
unverifiable**. Prax's RL tasks are the opposite: **verifiable** (extraction's reward
is deterministic field-accuracy; the gold JSON *is* the environment). So Prax stays
on the *"if you can verify it, you can train it"* side — the deterministic reward is
the simulator, no learned world model required. (Same principle that motivated the
`verify` deterministic-scoring path in `prax/eval/goldens.py` — see
[`awesome-evals.md`](awesome-evals.md).)

> **Bookmark — a permissive cold-start seed *for agentic* fine-tuning.** This plan is
> document-extraction (its seed = synthetic/permissive *extraction* corpora). But the
> *no-frontier-distillation* contamination rule applies to **any** future fine-tune of
> Prax's own behaviour, and the hard part there is finding a permissive agentic seed.
> **OpenThoughts-Agent** ([OpenThinkerAgent-32B](https://huggingface.co/open-thoughts/OpenThinkerAgent-32B),
> [arXiv 2606.24855](https://arxiv.org/abs/2606.24855)) is exactly that: a fully-open,
> **Apache-2.0** dataset of 100K agentic (terminal/code/SWE) trajectories + a published
> curation pipeline + ablations on what data diversity matters — i.e. a
> frontier-distillation-*free* cold-start seed + recipe for agentic SFT. **Not for
> this doc-extraction model**, and **not** a recommended open *backend* (its 32B is
> research-grade, well below GLM-5.2). Filed here only so the future "fine-tune Prax's
> agentic policy" effort knows where the permissive data lives.

VLM training rewrite (`FastVisionModel` + `UnslothVisionDataCollator`, vision tower
frozen, LoRA on the LLM; multi-image messages = native Qwen3-VL multi-page); add a
`pypdfium2` PDF→image renderer (Prax has none); **GRPO/GSPO** alignment (TRL) with
reward = soft format-validity + normalized per-field exact-match (a Qwen2.5-1.5B
passport extractor went 87.6%→94.2% field acc, +6.6pp, ~22 min on 1×A100 [external]).
**Do not train against the constrained decoder** — train on plain JSON, enforce
validity only at serve time. **Total to a credible 8B product ≈ 50–200 GPU-hours,
~$100–600 cloud** — dominated by data labor.

## Day-1 checklist

1. `pip install jsonschema` (main venv, for the eval comparator); provision a CUDA host with `vllm` — see [`../guides/cloud-gpu.md`](../guides/cloud-gpu.md) for how to rent that GPU (and power it off).
2. Add a `vllm` service to `docker-compose.gpu.yml`; serve Qwen3-VL-8B-Instruct (Apache-2.0) with `--guided-decoding-backend guidance`.
3. Add `settings.doc_extract_enabled` + `DOC_EXTRACT_BASE_URL/MODEL`; thread `extra_body={"guided_json": schema}` into `llm_factory.py:222`.
4. Clone `spokes/finetune/` → `spokes/doc_extract/` with `document_extract(path, schema)`.
5. Write the deterministic comparator; wire the `document_extract` golden via `run_golden_suite(replay_fn=..., judge=...)`.
6. License-pin the exact Qwen3-VL HF commit; start the data-provenance manifest before collecting any training data.

## Bottom line

A commercial-friendly Lift-equivalent is **mostly an integration job, not a
research project**, because Prax already owns the fine-tune/serve/eval scaffolding
and the validity guarantee is free via constrained decoding. Phase 0 is worth doing
on its own merits (no-egress, valid-by-construction extraction); Phases 1–2 are a
real but optional training program justified only by a concrete data-residency /
cost / hard-tail / ship-the-weights case. Tracked in evals via
`prax/eval/goldens/document_extract.yaml` so it doesn't get lost. See also
[Lift](lift-document-extraction.md) and [PixelRAG](pixelrag-visual-rag.md) (shared
Qwen3-VL vision backbone).
