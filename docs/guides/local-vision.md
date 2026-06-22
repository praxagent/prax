# Local models — vision and inference

[← Guides](README.md)

You can run Prax fully off-OpenAI by pointing both the vision tool
(`analyze_image`) and the chat-completion path at any OpenAI-compatible
local server: `llama.cpp`'s `llama-server`, vLLM, Ollama (`/v1`), or LM
Studio.  One endpoint can serve both — it's the same HTTP API.

## How it works

| Component | What flips when you point it at a local server |
|---|---|
| `analyze_image` (`prax/agent/vision_tools.py`) | When `VISION_BASE_URL` is set, the OpenAI client uses that base URL **and** images get inlined as `data:image/...;base64,...` URIs.  Most local servers run without outbound network access and can't reach the Discord/Twilio CDN themselves. |
| Chat / orchestrator / spokes (`prax/agent/llm_factory.py`) | The existing `vllm` provider points `ChatOpenAI` at `VLLM_BASE_URL`.  Despite the name, it works against any OpenAI-compatible server — llama.cpp, Ollama, vLLM all match. |

`VISION_API_KEY` is optional.  Most local servers ignore the key; Prax
passes a placeholder when none is configured.

`models_used` in every trace node now records the provider and model that
served each LLM and vision call.  When a call goes to a local endpoint, the
provider field includes the URL — e.g. `openai@http://127.0.0.1:8083/v1` —
so reading a trace tells you immediately which calls ran on-prem and which
went to a hosted API.

## llama.cpp `llama-server`

`llama.cpp` supports vision via the multimodal projector (`mmproj`).  You
need two GGUFs: the language model and the matching `mmproj-*.gguf`.

### Build a CUDA `llama-server` (skip if your binary already supports CUDA)

The default Unsloth `llama-server` binary is CPU-only.  To use the GPU you
need a CUDA build.  On Ada / Ampere with `nvcc` already installed:

```bash
cd ~/.unsloth/llama.cpp        # or wherever your llama.cpp source lives
cmake -B build-cuda \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89 \   # 89 = Ada (RTX 4060/4080/2000-Ada).  Use 86 for Ampere, 90 for Hopper.
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_CURL=OFF
cmake --build build-cuda --config Release -j16 --target llama-server
```

Resulting binary: `build-cuda/bin/llama-server`.  Build takes 3–5 min on a
modern desktop CPU.  Verify CUDA is in:

```bash
build-cuda/bin/llama-server --version
# => ggml_cuda_init: found 1 CUDA devices ...
```

A CPU-only binary will print `warning: no usable GPU found` on startup and
ignore `-ngl`.

### Pick a model that fits your VRAM

Sized for a 16 GB GPU (e.g. RTX 2000 / 4060 / 4080 Ada).  Numbers are rough
on-disk sizes — the active VRAM footprint is usually smaller for MoE models.

| Model | VRAM target | On disk | Vision-capable? | Notes |
|---|---|---|---|---|
| Gemma 3 4B | ~6 GB | ~3 GB | yes | Fastest path; pairs with `mmproj-model-f16.gguf` from `ggml-org/gemma-3-4b-it-GGUF`. |
| Qwen2.5-VL 7B-Instruct | ~10 GB | ~5 GB | yes | Strongest small VL model.  Reliable Q4_K_M GGUFs from `unsloth` / `bartowski`. |
| Qwen3.6-35B-A3B (MoE) | ~9 GB w/ CPU offload | ~22 GB | yes | Sparse MoE: only ~3 B params active per token.  `-ncmoe` offloads routed-expert layers to CPU.  Strong at chat + tool calls; OCR at Q4 is rough. |

Get GGUFs from Hugging Face.  Always pull the matching `mmproj-*.gguf`
from the same repo — vision projectors are not interchangeable.

```bash
mkdir -p /data/models/qwen35-vision && cd /data/models/qwen35-vision
hf download unsloth/Qwen3.6-35B-A3B-GGUF \
  Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
  mmproj-F16.gguf \
  --local-dir .
```

### Start one server that serves both vision and chat

The same `llama-server` instance handles both modes — `--mmproj` enables
multimodal, and `/v1/chat/completions` already supports tool calls when
`--jinja` is on.  Two clients pointing at the same port is fine.

**Qwen3.6-35B-A3B on 16 GB VRAM** (verified on RTX 2000 Ada):

```bash
./build-cuda/bin/llama-server \
  -m /data/models/qwen35-vision/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
  --mmproj /data/models/qwen35-vision/mmproj-F16.gguf \
  --alias qwen35-local \
  --host 127.0.0.1 --port 8083 \
  -ngl 70 -ncmoe 30 -fa on \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  -c 32768 -t 12 --jinja --metrics
```

What each flag does:

- `-ngl 70` — offload 70 layers to GPU (the model has ~40 — anything ≥ all
  layers means "everything that fits").
- `-ncmoe 30` — keep the routed-expert tensors of 30 layers on CPU.  Tune
  this: raise on CUDA OOM, lower for more throughput if you have headroom.
  At `-ncmoe 30` on a 16 GB Ada we measured ~9 GB VRAM used, with ~6 GB
  free.
- `-fa on` — flash attention.
- `--cache-type-k q8_0 --cache-type-v q8_0` — quantize the KV cache, halves
  the cache cost.  Worth it for any context ≥ 16k; quality hit is small.
- `-c 32768` — context length.  Upstream cards say 131072 (128 k) is
  supported, but most real workloads fit in 16–32 k and shorter contexts
  start fast.  Bump only when you genuinely send long transcripts.
- `-t 12` — CPU threads.  Set to physical core count.
- `--jinja` — enables the model's chat template (required for tool calls).
- `--metrics` — Prometheus stats at `/metrics` (optional).
- `--host 127.0.0.1` — bind localhost only.  llama-server has no auth; do
  not expose it on the LAN unless you've put auth in front.

**Smaller alternative (Qwen2.5-VL-7B)** — fully GPU-resident, faster:

```bash
./build-cuda/bin/llama-server \
  -m /data/models/qwen2.5-vl-7b/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf \
  --mmproj /data/models/qwen2.5-vl-7b/mmproj-Qwen2.5-VL-7B-Instruct-f16.gguf \
  --alias qwen-vl \
  --host 127.0.0.1 --port 8083 \
  -ngl 99 -fa on -c 32768 --jinja
```

### Point Prax at it

In `.env`:

```bash
# Vision (analyze_image — Discord/SMS/TeamWork attachments)
VISION_PROVIDER=openai
VISION_MODEL=qwen35-local                    # match the --alias
VISION_BASE_URL=http://127.0.0.1:8083/v1
# VISION_API_KEY=                             # leave empty — llama-server ignores it

# Chat / orchestrator / spokes (replaces OpenAI for the agent loop)
LLM_PROVIDER=vllm                            # any OpenAI-compatible endpoint, despite the name
VLLM_BASE_URL=http://127.0.0.1:8083/v1
BASE_MODEL=qwen35-local                      # used by the default tier
LOW_MODEL=qwen35-local
MEDIUM_MODEL=qwen35-local
HIGH_MODEL=qwen35-local
```

Restart Prax.  Every trace node will now show `provider=openai@http://127.0.0.1:8083/v1`
and `model=qwen35-local` in `models_used`.  Vision attachments and chat
turns hit the same local server.

### What we measured

On RTX 2000 Ada (16 GB) running Qwen3.6-35B-A3B-UD-Q4_K_M:

| Workload | Latency | VRAM used |
|---|---|---|
| Cold model load | ~3 s after first token, ~30 s total | 9 GB |
| Vision (one image, ~100 vision tokens, ~60 reply tokens) | ~3.4 s | — |
| Chat with one tool call (~200 prompt tokens, ~200 reply tokens) | ~8.4 s | — |
| Steady-state generation | ~30 tok/s | — |

Qwen3.6-35B-A3B is excellent at chat + tool calling at this quantization
but OCR fidelity at Q4 is mediocre (we measured `HILO` for `HELLO`).  For
OCR-heavy use cases, run a dedicated VL model (Qwen2.5-VL-7B at Q5 or
higher) on a second alias and point only `VISION_MODEL` at it.

### OCR fidelity comparison (Q4_K_M, same hardware)

Same OCR probe (single line of bold black text on a white background)
against two locally-served models on the RTX 2000 Ada:

| Model | Size | Probe text | Output | Verdict |
|---|---|---|---|---|
| Gemma 3 4B | ~3 GB | `ANALYZE_IMAGE WORKS` | `ANALYZE_IMAGE WORK` | 18/19 chars — dropped trailing S |
| Qwen3.6-35B-A3B | ~22 GB | `QWEN VISION OK` | `EVERYBODY` | unrelated — failed |
| Qwen3.6-35B-A3B | ~22 GB | `HELLO` | `HILO` | 3/5 chars |

Gemma 3 4B beat Qwen3.6-35B-A3B at OCR despite being ~7× smaller.  Why:
Gemma 3 was designed multimodal from scratch, while Qwen3.6-35B-A3B is a
chat-focused MoE (3 B activated params per token) with vision bolted on.
For text inside images, prefer a model whose card lists vision as a
primary capability (Gemma 3, Qwen2.5-VL, Pixtral, MiniCPM-V) over a
larger general-purpose model with `image-text-to-text` tagged on.

## Hybrid routing — local for cheap, OpenAI for hard

You don't have to commit to fully local.  `prax/plugins/configs/llm_routing.yaml`
lets every component pick its own `provider` / `model` / `tier` /
`temperature` independently.  A common cost-saving pattern:

- The **orchestrator** runs on every turn and on every scheduled briefing —
  put it on the local model.
- Sub-agents that need synthesis quality (note writing, content review,
  research) keep their hosted-API tier.
- Cheap classifiers (`note_quality_reviewer`) go local.

Keep the global env vars on OpenAI as the default, then carve out the
specific components you want local in `llm_routing.yaml`:

```yaml
# .env stays on hosted defaults
# LLM_PROVIDER=openai
# BASE_MODEL=gpt-5.4-nano
# (no VLLM_BASE_URL — it's only consulted by components that ask for vllm)

# llm_routing.yaml — per-component overrides
components:
  orchestrator:                # cheap, hot path → local
    provider: vllm
    model: qwen35-local
    temperature: 0.7

  note_quality_reviewer:       # cheap binary classifier → local
    provider: vllm
    model: qwen35-local
    temperature: 0.2

  subagent_knowledge:          # note synthesis → keep hosted
    tier: high
    temperature: 0.5

  subagent_research:           # research → keep hosted
    tier: high
    temperature: 0.3

  subagent_professor:          # consensus across providers → keep hosted
    tier: pro
    temperature: 0.3
```

For this to work, `VLLM_BASE_URL` (the global pointer at your local server)
still needs to be set in `.env` even though `LLM_PROVIDER` stays as
`openai` — the per-component `provider: vllm` overrides reach for that URL
when they fire:

```bash
LLM_PROVIDER=openai
VLLM_BASE_URL=http://127.0.0.1:8083/v1
```

For vision, the same logic applies — keep `VISION_PROVIDER=openai` (real
OpenAI) and unset `VISION_BASE_URL`, OR point vision at a local OCR-strong
model on a different alias / port:

```bash
VISION_PROVIDER=openai
VISION_MODEL=gemma3-vision
VISION_BASE_URL=http://127.0.0.1:8084/v1   # second llama-server, OCR-tuned
```

`models_used` in the trace makes the routing visible per call — you can
audit a real session and check that the expensive components went where
you intended.

## vLLM

vLLM is OpenAI-compatible out of the box.  One server, one config:

```bash
vllm serve Qwen/Qwen2.5-VL-7B-Instruct \
  --host 127.0.0.1 --port 8000 \
  --max-model-len 32768
```

```bash
VISION_BASE_URL=http://127.0.0.1:8000/v1
VISION_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
LLM_PROVIDER=vllm
VLLM_BASE_URL=http://127.0.0.1:8000/v1
BASE_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
```

### Co-hosting two models on one GPU

To serve a **heavy** model and a **fast** model from one box (the local analog of
Prax's low/medium/high tiers), run two vLLM backends behind one OpenAI-compatible
proxy (e.g. LiteLLM) and point `VLLM_BASE_URL` at the proxy, routing components via
`llm_routing.yaml`. Two gotchas from
[`../research/two-qwen3-on-one-spark.md`](../research/two-qwen3-on-one-spark.md):

- **`gpu_memory_utilization` is a fraction of TOTAL VRAM, not free VRAM.** Size the
  models so their fractions sum to **< ~0.95** (leave ~5 GiB CUDA overhead).
- **Load the big model first**, then `nvidia-smi --query-gpu=memory.used
  --format=csv`, then size the small one against (free − ~5 GiB) — measured
  residency ≠ targets. Use **FP8** to fit large models.

Add `--enable-auto-tool-choice --tool-call-parser hermes` so the local model can
call tools (Prax needs this).

## Ollama

```bash
ollama serve
ollama pull qwen2.5vl:7b
```

```bash
VISION_BASE_URL=http://127.0.0.1:11434/v1
VISION_MODEL=qwen2.5vl:7b
LLM_PROVIDER=vllm
VLLM_BASE_URL=http://127.0.0.1:11434/v1
BASE_MODEL=qwen2.5vl:7b
```

## Cloud GPU (no local GPU? launch one on demand)

A cloud GPU running vLLM is **just a remote `VLLM_BASE_URL`** — once it's up,
nothing on the inference path changes. The full guide — top providers, how to
**launch / serve / power off**, and a **least-privilege "on/off only"** capability
(so Prax can never do more than flip the GPU) — is in
[`cloud-gpu.md`](cloud-gpu.md). The plug-and-play flow: GPU present locally → use
it; absent → power a scoped cloud box on, point `VLLM_BASE_URL` at it, power it off
when done. No model is hard-wired; Prax serves what it needs on demand and saves
recurring recipes as workspace plugins.

## Fine-tuning path

The same GPU unlocks fine-tuning: Prax has a harvest → LoRA-train → verify →
promote loop (`finetune_service.run_self_improvement_cycle`, vLLM adapter hot-swap
via `/v1/load_lora_adapter`, `scripts/finetune_train.py` — Unsloth QLoRA in a
separate CUDA venv), gated on `FINETUNE_ENABLED`. A QLoRA of an 8B fits ~24 GB; a
real run on an A100-80GB is ~30–80 GPU-hours (~$40–105). See
[`cloud-gpu.md`](cloud-gpu.md) for *how to rent that A100 and turn it off* and
[`../research/diy-document-extraction-model.md`](../research/diy-document-extraction-model.md)
for an end-to-end build that exercises this path.

## Verifying it works

The fastest end-to-end check, after restart:

1. Send a small image to the Discord bot or SMS line.  In the resulting
   trace, look for one `analyze_image` tool call with `models_used`
   pointing at your local URL.  `run_python` / `delegate_desktop` should
   not appear.
2. Send a plain text question.  Every span's `models_used` should show the
   local provider/model — nothing pointing at `api.openai.com`.

If `analyze_image` doesn't register, double-check `build_vision_tools()`'s
gating: it requires either `VISION_BASE_URL`, `VISION_API_KEY`, or
`OPENAI_KEY` to be non-empty.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Connection refused` on every call | `llama-server` isn't running, or the port in `*_BASE_URL` is wrong. |
| `warning: no usable GPU found` at server startup | CPU-only `llama-server` binary.  Build the CUDA target as shown above. |
| 500 with "tokenizer doesn't support multimodal" | Started without `--mmproj`, or the projector doesn't match the base model. |
| Garbled / unrelated OCR output but shapes/colors are correct | Q4 OCR quality is rough on a 35B-A3B.  Try Q5/Q6 or a 7 B VL model. |
| `model … not found` from the OpenAI client | `VISION_MODEL` / `BASE_MODEL` doesn't match the `--alias` (or Ollama tag, or vLLM model id). |
| OOM at startup | Raise `-ncmoe`.  If still OOM, drop quant or model size. |
| Image fetch errors in the Prax log | The remote URL has expired (Discord CDN URLs expire after ~24 h).  Ask the user to re-upload. |
