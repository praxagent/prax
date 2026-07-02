# Running big models for evals without a rented GPU (CPU · Mac · DGX Spark)

> **Why this exists.** Evals don't need to be fast — they can run overnight or
> over days. That makes a **big model on memory you already own** the cheapest way
> to get a strong eval *subject*: a high-RAM **CPU box**, an Apple-silicon **Mac**
> (unified memory), or an NVIDIA **DGX Spark** — none of which is a rented
> datacenter GPU. This guide is the *how*: pick a box, serve a big (usually
> **MoE**) model over an OpenAI-compatible endpoint, and point Prax at it.
> Two engines: **llama.cpp** for a CPU-only Linux box, **ds4** for a Mac or Spark.
> Companions: [`cloud-gpu.md`](cloud-gpu.md) (when you *do* rent a GPU) and
> [`local-vision.md`](local-vision.md) (local vision models). The eval suites that
> consume this are in [`../../prax/eval/README.md`](../../prax/eval/README.md).

## The split that makes this cheap

Run the **harness** (Prax) and the **model server** on different machines:

```
┌─────────────────┐         OpenAI /v1          ┌──────────────────────────┐
│  Prax harness   │  ───────────────────────▶   │  big model on a CPU box  │
│  (tiny box ok)  │   VLLM_BASE_URL                │  llama.cpp / ds4         │
│  drives evals   │  ◀───────────────────────   │  slow but cheap          │
└─────────────────┘      tokens (overnight)       └──────────────────────────┘
```

Prax only orchestrates HTTP calls and waits — so the harness host can be a tiny
instance. All the RAM goes to the **model box**. The eval suites are resumable
and have **no per-task timeout by default**, so a model emitting a few
tokens/second is fine.

## CPU inference in one paragraph

On CPU, **RAM size decides what *fits*; RAM bandwidth + core count decide how
*fast*.** The cheat code is **Mixture-of-Experts (MoE)** models: only a few
billion parameters activate per token, so a 30B–120B MoE runs at usable speed on
CPU while a dense 70B crawls. Quantize to **Q4** (or MXFP4 for GPT-OSS) to halve
the RAM. Expect single-digit-to-low-teens tokens/sec — irrelevant for overnight
evals.

## Hardware sizing (CPU-only, Q4-ish)

| RAM | Model (engine) | Notes |
|---|---|---|
| **32–48 GB** | **Qwen3-30B-A3B** (llama.cpp) | MoE, ~3B active → genuinely fast on CPU. Best $/quality starting point. |
| 48–64 GB | **GPT-OSS-20B** (llama.cpp) | Apache-2, native MXFP4 (full quality at ~Q4 size) |
| 96–128 GB | **GPT-OSS-120B** (llama.cpp) | MoE, ~5B active, ≈63 GB. Near-frontier on a CPU box. |
| 128 GB+ | **DeepSeek V4 Flash** (ds4 on Mac/CUDA, or llama.cpp GGUF on CPU) | Frontier MoE. ds4's q2 tier targets 96/128 GB; q4 needs 256 GB+. |

Prefer **more RAM bandwidth** (8+ memory channels, e.g. EPYC/Xeon-SP) and
**physical cores** over clock speed. A cheap **Hetzner dedicated** box (e.g.
~128 GB ECC for roughly €100/mo) beats AWS for sustained overnight runs; AWS
`r7i`/`m7i` spot if you want on-demand.

---

## Engine A — llama.cpp (the Linux CPU path)

`llama-server` ships an OpenAI-compatible `/v1/chat/completions` endpoint — this
is the right choice for a **CPU-only Linux box**.

```bash
# 1. Build (CPU; native arch optimizations on)
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build -DGGML_NATIVE=ON
cmake --build build -j --config Release        # binaries in build/bin/

# 2. Get a GGUF (examples — pick one your RAM fits)
#    Qwen3-30B-A3B  (~18 GB Q4_K_M)            unsloth/Qwen3-30B-A3B-GGUF
#    GPT-OSS-120B   (~63 GB MXFP4)             ggml-org/gpt-oss-120b-GGUF
huggingface-cli download unsloth/Qwen3-30B-A3B-GGUF Qwen3-30B-A3B-Q4_K_M.gguf --local-dir ./models

# 3. Serve (CPU-only: no -ngl, threads = PHYSICAL cores)
./build/bin/llama-server \
  -m ./models/Qwen3-30B-A3B-Q4_K_M.gguf \
  -c 32768 \                 # context window
  -t $(nproc) \              # threads (use physical, not logical, cores)
  --host 0.0.0.0 --port 8080 \
  --mlock                    # pin weights in RAM (skip if RAM is tight)
```

Key flags for CPU/MoE throughput:
- `-t N` — threads; set to **physical** cores (hyperthreads rarely help).
- `--mlock` — keep weights resident (avoid swap); drop it if you're RAM-bound and
  relying on `mmap`.
- `--numa distribute` — on a multi-socket box, spread across NUMA nodes.
- `--n-cpu-moe N` — *only* if the box also has a small GPU: keep N MoE expert
  layers on CPU and the rest on GPU. Pure CPU: omit it.
- Quant choice: `Q4_K_M` is the sweet spot; `Q5_K_M` if you have RAM headroom and
  want a bit more fidelity; GPT-OSS is already MXFP4 — use it as-is.

## Engine B — ds4 (DeepSeek V4) — **recommended for Mac or DGX Spark**

[antirez/ds4](https://github.com/antirez/ds4) is a *native DeepSeek V4* engine
with an OpenAI/Anthropic-compatible server and **disk KV-cache to run models
larger than RAM**. It is the **preferred engine when your box is a big Mac
(Apple Metal, unified memory) or an NVIDIA DGX Spark / GB10** — both use memory
you already paid for rather than a rented datacenter GPU, and ds4 is tuned for
exactly these targets. (On a *CPU-only Linux* box, `make cpu` is **diagnostics
only** — there, prefer llama.cpp with a DeepSeek-V4 GGUF.)

```bash
git clone https://github.com/antirez/ds4 && cd ds4
make                 # macOS Metal (default)
# make cuda-spark    # NVIDIA DGX Spark / GB10   ← Spark users build this target
# make cuda-generic  # other NVIDIA CUDA GPUs
./download_model.sh q2-imatrix          # 96/128 GB RAM tier (2-bit imatrix)
# ./download_model.sh q4-imatrix        # 256 GB+ tier;  pro-q2-imatrix → 512 GB PRO
./ds4-server --ctx 100000 \
  --kv-disk-dir /tmp/ds4-kv --kv-disk-space-mb 8192 \
  --host 0.0.0.0                        # default port 8000
# Models larger than RAM: stream routed experts from SSD
# ./ds4 -m ./ds4flash.gguf --ssd-streaming --ssd-streaming-cache-experts 32GB
```

Then wire Prax exactly as below, with `VLLM_BASE_URL=http://<mac-or-spark>:8000/v1`.

---

## Wire Prax at the endpoint

The `vllm` provider is just an OpenAI client against `VLLM_BASE_URL` (no API key
needed). In the Prax host's `.env`:

```bash
LLM_PROVIDER=vllm
VLLM_BASE_URL=http://<model-box>:8080/v1     # llama.cpp port 8080; ds4 port 8000
# Point every tier at the served model id (all the same is fine for a single box):
LOW_MODEL=Qwen3-30B-A3B
MEDIUM_MODEL=Qwen3-30B-A3B
HIGH_MODEL=Qwen3-30B-A3B
```

> **Embeddings caveat.** Memory/knowledge retrieval uses a separate
> `EMBEDDING_PROVIDER` (default OpenAI). For a fully-local stack set
> `EMBEDDING_PROVIDER=ollama` + `OLLAMA_BASE_URL`, or leave embeddings on a cheap
> API — they're not what the eval is measuring.

## Run the eval suites overnight

```bash
make eval-harness-lift EVAL_TIER=medium    # how much does the harness lift THIS model?
make eval-capability   EVAL_TIER=medium    # deterministic capability checks
make eval-gaia LIMIT=20 EVAL_TIER=medium   # quick GAIA smoke; drop LIMIT for the full set
```

- **No timeout by default** (`PRAX_EVAL_TASK_TIMEOUT_S=0`) — a slow box runs as
  long as it needs. Set a positive value only as a safety rail.
- **Resumable** — kill it, reboot, re-run the *same* command; it skips finished
  tasks. Watch with `tail -f $PRAX_EVAL_DIR/suites/<run>/progress.jsonl`.
- **Concurrency 1** (`PRAX_EVAL_CONCURRENCY`) — one CPU server serves one request
  at a time well; leave it at 1.

## Caveats

- **Tool-calling fidelity on open models is tracked, not assumed**
  (IDEAS_BACKLOG #20). A weaker local model may format tool calls worse — which
  is *exactly* what the capability/harness-lift suites surface, so it's a feature
  here, not a blocker.
- **Prefill is slow on CPU** for long prompts; keep `-c` (context) only as large
  as your cases need.
- **First token latency** can be many seconds on a big model — normal, and
  invisible to an overnight batch.

## References

- [llama.cpp — running gpt-oss](https://github.com/ggml-org/llama.cpp/discussions/15396)
- [Optimizing gpt-oss-120b on consumer hardware](https://carteakey.dev/blog/local-inference/optimizing-gpt-oss-120b-local-inference/)
- [antirez/ds4 (DeepSeek V4 engine)](https://github.com/antirez/ds4)
