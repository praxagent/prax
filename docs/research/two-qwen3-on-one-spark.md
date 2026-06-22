# Two Qwen3 models on one DGX Spark — co-hosting local models

Reference note. Source: Devashish Meena, *"Two Qwen3 Models on One DGX Spark"* —
<https://www.devashish.me/p/two-qwen3-models-on-one-dgx-spark>. Captured for the
local-LLM/GPU serving story; the hands-on guide it informs is
[`../guides/local-vision.md`](../guides/local-vision.md), and the cloud path is
[`../guides/cloud-gpu.md`](../guides/cloud-gpu.md).

## What it demonstrates

Two models co-resident on **one** box — `Qwen3-Next-80B-Instruct-FP8` (heavy
reasoning) + `Qwen3-4B-Instruct-2507` (fast turns) — on an NVIDIA **DGX Spark**
(GB10 Grace-Blackwell, ~119.67 GiB unified memory, ~$4k class). Served as **two
vLLM containers behind a LiteLLM proxy on `:4000`**, routed by model alias, with
`--enable-auto-tool-choice --tool-call-parser hermes` for tool calling.

## The load-bearing lessons

1. **vLLM, not Ollama** — Ollama can't partition GPU memory, lacks
   `gpu_memory_utilization`, and lacks PagedAttention. vLLM gives all three.
2. **`gpu_memory_utilization` is a fraction of TOTAL VRAM, not free VRAM** — the
   headline gotcha that caused repeated deploy failures. Working budgets:
   80B → `gpu_memory_utilization: 0.80, max_model_len: 65536, max_num_seqs: 2`;
   4B → `0.10, 16384, 8`. Allocations must sum **< ~0.95** (leave ~5 GiB CUDA
   overhead).
3. **Procedure:** load the big model first → `nvidia-smi --query-gpu=memory.used
   --format=csv` → size the small model against (free − ~5 GiB). Measured
   residency ≠ targets (80B ~101.5 GiB; 4B ~13.8 GiB), so measure, don't assume.
   (Qwen3-Next's Mamba state alignment makes KV demand less predictable.)
4. **FP8** lets an 80B fit in ~120 GiB.

## Why it matters for Prax

A LiteLLM (or any OpenAI-compatible) proxy in front of N vLLM backends presents
**one** endpoint → maps 1:1 onto Prax's existing `VLLM_BASE_URL` rail
(`prax/settings.py`) and the `vllm` provider in `llm_factory.py`. That's how a
**single local GPU serves a heavy + a fast model behind one URL** — the local
analog of Prax's low/medium/high tiers and per-component `llm_routing.yaml`
routing (run cheap classifiers on the 4B, synthesis on the 80B or a hosted tier).

The DGX Spark is the canonical "one prosumer box, big unified memory" local
target; the same co-hosting recipe applies to a rented cloud GPU (see
[`cloud-gpu.md`](../guides/cloud-gpu.md)) — launch it, serve both models behind a
proxy, point `VLLM_BASE_URL` at it, power it off when done.

**Verdict:** reference + technique to reuse (co-host-behind-one-proxy + the
`gpu_memory_utilization` sizing rule), not a roadmap item.
