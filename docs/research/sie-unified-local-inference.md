# SIE — Superlinked Inference Engine (unified local model serving)

**Source:** [superlinked/sie](https://github.com/superlinked/sie) (Apache-2.0;
Python/TypeScript/Rust; 2.5k stars, 244 forks at assessment)
**Assessed:** 2026-08-06. TJ's question: *"any value in documenting this for
people who run Prax on local models?"*

**Verdict: yes — document as a deployment option (guide section, shipped), and
adopt ONE small general fix the documentation exercise exposed
(`EMBEDDING_BASE_URL`). Nothing to integrate beyond that: SIE needs zero
Prax-side code by design, which is exactly what makes it worth documenting.**

---

## What it is

One inference cluster instead of a model-server-per-task patchwork: "it
replaces the patchwork of a separate model server per task with one system that
serves 100+ models." Embeddings (Stella, SPLADE, BGE-M3, ColBERT), generation
(Qwen3, LLaMA variants), entity extraction (GLiNER), OCR, vision (SigLIP) —
all behind **OpenAI-compatible endpoints** (`/v1/embeddings`,
`/v1/chat/completions`), with on-demand model loading + LRU eviction, and a
production stack around it (gateway, KEDA autoscaling, Grafana, Terraform).

## Why it maps onto Prax's local story

Prax's local-model support is deliberately *protocol-shaped*: `llm_factory`
talks to anything OpenAI-compatible (`VLLM_BASE_URL`, third-party
`OPENAI_BASE_URL`), and the docs already carry three local guides
(`local-cpu-inference.md`, `local-vision.md`, `embeddings-migration.md`). A
fully-local Prax today assembles a patchwork: llama.cpp/ds4 for chat, Ollama or
fastembed for embeddings, a separate path for vision. SIE is the consolidation
of that patchwork behind one URL — and because it speaks the same protocol,
**Prax needs no adapter, no client library, no code.** That is the best kind of
integration: a documentation section. Shipped in
[`../guides/local-cpu-inference.md`](../guides/local-cpu-inference.md).

## The adopt: `EMBEDDING_BASE_URL` (found by writing the doc)

Writing the wiring instructions exposed a real gap: the `openai` embedding
provider (`memory/embedder.py:_embed_openai`) constructed its client with SDK
defaults — no explicit base URL. Whether it could target a local server
depended on `OPENAI_BASE_URL` happening to be in the process environment, which
pydantic settings do **not** guarantee (only the proxy-networking allowlist is
exported to `os.environ`; see `settings.py:_export_proxy_env_from_dotenv`). So
the chat path was locally routable and the embeddings path silently was not —
meaning "fully local Prax" quietly kept a dependency on api.openai.com or on
launch-environment luck.

Fix shipped in the same change, general by construction: an explicit
`EMBEDDING_BASE_URL` setting that points the `openai` provider at **any**
OpenAI-compatible `/v1/embeddings` server — llama.cpp server, vLLM, LM Studio,
SIE. Keyless-local works (placeholder key when a base URL is set and no
`OPENAI_KEY` exists); unset means bit-for-bit prior behaviour. Tests:
`tests/test_embedder_base_url.py`.

Per the prime directive this is not an SIE feature — SIE was merely the prompt.
Any local-embeddings user gets it.

## Honest limits

- **We have not run SIE.** No GPU on either Prax box, and SIE's generation path
  wants one (Apple-silicon requires their MLX runtime; some model families need
  separate Docker images for dependency conflicts). The guide section says so
  explicitly, and `EMBEDDING_BASE_URL` is unit-tested-only against a mocked
  client — VERIFICATION_LEDGER row added.
- **No performance numbers published** in their README ("depends on the model,
  task, hardware, and batch size"), so nothing to transcribe and nothing to
  compare. Embedding models are "benchmarked on MTEB" per the README; we did
  not verify.
- **Not a replacement for the CPU eval path.** The overnight big-MoE-on-CPU
  lane in `local-cpu-inference.md` stays the cheap way to get a strong eval
  subject; SIE is for the GPU-box user who wants the whole zoo behind one URL.
- **Adjacent, deliberately not pursued**: SIE's extraction/OCR/vision endpoints
  could in principle back other Prax paths (GLiNER for consolidation's entity
  extraction, OCR for document ingestion). Each would be a real feature with a
  real eval question — parked, not adopted, until someone actually runs Prax
  against an SIE cluster.
