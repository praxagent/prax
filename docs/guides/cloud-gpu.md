# GPU access for Prax — local, cloud, and least-privilege power control

> **Philosophy (firm — see the sleek/plug-and-play principle).** Prax *core* stays
> sleek. A GPU is **plug-and-play and on-demand**, never bundled. Prax is given
> the **ability** — (1) a GPU endpoint via the existing `VLLM_BASE_URL` /
> `VISION_BASE_URL` rails, and (2) a sandbox to **write code on command** that
> serves *any* model (Whisper, image/video gen, SAM3, …). **No model is
> hard-wired into core.** A recipe Prax repeats graduates into a **workspace
> plugin** (`plugins/custom/`) whose `permissions.md` ceiling is its own
> least-privilege boundary. This guide is the *how*; nothing here makes Prax
> heavier when no GPU is present.

This is the cloud counterpart to [`local-vision.md`](local-vision.md) (run a local
LLM / vision model) and the multi-model technique in
[`../research/two-qwen3-on-one-spark.md`](../research/two-qwen3-on-one-spark.md).

## Decision flow — where does the GPU come from?

1. **`VLLM_BASE_URL` / `VISION_BASE_URL` already set + reachable** → use it. No
   provisioning. (A cloud GPU running vLLM is *just a remote `VLLM_BASE_URL`* —
   zero inference-path change.)
2. **Local GPU present** (`make sandbox-gpu-check` passes) → serve in the sandbox
   via `docker-compose.gpu.yml`; point `*_BASE_URL` at localhost.
3. **Cloud-GPU power capability configured** → **power ON** a *pre-provisioned*
   instance, wait for vLLM health, set `VLLM_BASE_URL` at runtime, work, **power
   OFF**.
4. **Otherwise** → degrade gracefully onto hosted APIs exactly as today
   (finetune/vLLM tools early-return their "disabled" message).

The key invariant: **once a box is up and serving vLLM, it's only a URL.** All the
hard part is *getting one cheaply and turning it off*.

## Sovereign / data-resident deployment — which open model to serve

The sections above answer *where the GPU comes from*; this one answers *which model
to run on it* when the requirement is **sovereignty** — on-prem / no-egress / data
residency / EU AI Act compliance. That's the one scenario Prax's default backend
can't cover: Claude (and the other hosted APIs) are remote and US-hosted, so a user
under those constraints needs an **open-weights** model served on infrastructure
they control. No core change is needed — this is purely the existing `vllm`/`local`
path (`prax/agent/llm_factory.py`), pointed at an open model.

**Recommended open backends — pick by what's driving the choice:**

- **Frontier capability + permissive license → GLM-5.2** (Z.ai). A **744B**-param
  MoE (~40B active), **1M-context**, **MIT-licensed** model that's strong on
  **agentic / coding** tasks at ~70–80% lower token cost than GPT-5.5 / Opus 4.8.
  This is the best "capable open backend" when you want frontier-class behaviour on
  weights you can self-host — and its strong tool-calling (e.g. BFCL) makes it the
  **lead candidate for the #20 fidelity-validation pass** below. Self-host on your
  own vLLM (drop-in via the `vllm` config below); also offered by hosted
  OpenAI-compatible APIs (e.g. [Baseten](https://baseten.co/), NVFP4/Dynamo-tuned
  for speed) when sovereignty *isn't* the driver — see the hosted note.
- **EU-compliance / multilingual → [Apertus](https://apertvs.ai/)** (Swiss AI
  Initiative — EPFL / ETH Zurich / CSCS). **EU AI Act-built** (opt-outs, PII
  removal, memorization mitigation), **fully reproducible**, **1000+ languages**;
  **8B**/**70B** + distilled "Apertus Mini". The pick *when compliance or broad
  multilinguality is the driver*, not raw capability.

For any other sovereign deployment, any OpenAI-compatible open model works on the
same path.

**Wiring it (self-hosted — no new code, config only):**

```bash
LLM_PROVIDER=vllm
VLLM_BASE_URL=http://your-model-host:8000/v1   # the vLLM server you control
# Tiers map to the model id your server exposes; for a single backend set them all
# the same (the agent still upgrades/downgrades within one model). Examples:
LOW_MODEL=zai-org/GLM-5.2        # or swiss-ai/Apertus-8B
MEDIUM_MODEL=zai-org/GLM-5.2     # or swiss-ai/Apertus-70B
HIGH_MODEL=zai-org/GLM-5.2
PRO_MODEL=zai-org/GLM-5.2
```

Verified: `build_llm(provider='vllm', model='…')` constructs a `ChatOpenAI` bound
to `VLLM_BASE_URL` with no core change — so the **self-hosted** inference path is
genuinely a drop-in.

> **Hosted open models (not sovereign) need a small wiring change.** GLM-5.2 is
> also served by OpenAI-compatible *hosted* APIs (Baseten, Z.ai). Pointing Prax at
> one isn't quite drop-in today: the `vllm` path hardcodes `api_key="not-needed"`,
> and the `openai` path takes a key but no `base_url` override
> (`prax/agent/llm_factory.py`). An OpenAI-compatible **"base_url + key"** provider
> option is a minor follow-up — not wired yet. Self-hosting (above) needs nothing.

> **Honest caveat (the part that *isn't* free).** Wiring the endpoint is solved;
> what is **not** yet proven is that Prax's tool-heavy agent loop (97+ tools, long
> system prompt, multi-step delegation) runs at acceptable *fidelity* on an open
> backend — open models vary widely on OpenAI-style tool-calling, instruction
> adherence, and long-context behaviour. That validation is the real work to make
> sovereign deployment first-class, and it's tracked in
> [`../IDEAS_BACKLOG.md`](../IDEAS_BACKLOG.md) #20 (GLM-5.2 is the lead candidate to
> validate first) — not assumed here.

> **Inference-engineering note (out of scope for Prax).** The serving-side craft
> that makes a hosted GLM-5.2 fast — NVFP4 quantization, prefill/decode
> disaggregation, KV-aware routing, multi-token-prediction speculation (NVIDIA
> Dynamo / Blackwell) — is a model *provider's* concern, not Prax's: Prax consumes
> an OpenAI-compatible endpoint and doesn't run an inference engine. The one
> consumer-side lever, **stable-prefix / system-prompt KV-cache reuse**, is already
> tracked as `IDEAS_BACKLOG.md` #5 (prompt caching).

### Serving config — KV-cache memory for long-reasoning models

Once you *self-host* (above), the **KV cache** — the per-token key/value tensors the
server holds during generation — becomes the memory bottleneck, and it bites exactly
the models worth self-hosting: long-reasoning ones (GLM-5.2 et al.) emit 32K+ tokens,
and the cache alone OOMs a 24 GB GPU around ~24K tokens. Worth knowing before you
size a box or reach for "KV cache compression"
([NVIDIA EAI, "KV cache compression and its infra problems"](https://research.nvidia.com/labs/eai/blogs/kv-cache-compression-and-its-infra-problems/)):

- **Naive token eviction does *not* free memory under vLLM/SGLang.** Paged attention
  allocates fixed ~16-token blocks and reclaims one only when it's *entirely* empty;
  evicting scattered tokens leaves a survivor in nearly every block → ~zero memory
  recovered. Eviction must be paired with **compaction** (repack survivors to empty
  whole blocks) to actually help.
- **Don't let a compression method force *eager* attention.** Score-based methods
  (H2O/SnapKV) need the attention matrix FlashAttention never materializes; falling
  back to eager attention to get it erases the speed win.
- **Where it's worth it:** geometry-aware eviction at a **2–3K-token KV budget** keeps
  reasoning accuracy while buying ~**2.5× throughput / ~10.7× memory** vs. full cache
  — i.e. it's a real lever for fitting a long-reasoning backend on a smaller/cheaper
  box, *if* your serving stack does the compaction.

**For the default (API-consumer) Prax this is transparent** — the provider runs the
inference engine and KV cache; you only get the consumer-side lever (stable-prefix
reuse, #5). This note matters only on the self-hosted/sovereign path (#20).

## Top cloud GPU providers (June 2026)

Two integration shapes. **Shape A — rent-a-box:** you get an instance with a
start/stop API, run vLLM/Ollama yourself, and (optionally) hold a power
credential. **Shape B — serverless:** a scale-to-zero OpenAI-compatible URL; "off"
is structural (no credential to hold, $0 when idle). Prices are on-demand,
mid-range GPU, indicative.

| # | Provider | Shape | Start/stop | Provider-scopable to *power-only*? | $/hr | Local-LLM fit / gotcha |
|---|---|---|---|---|---|---|
| 1 | **RunPod** | A+B | `runpodctl pod start\|stop`; SDK `resume_pod/stop_pod` | **Partial** — `rpa_` keys are r/w or read-only, no start-only tier → use a broker | H100 ~$1.99, L40S ~$0.79 | Best DX/price; **a stopped pod still bills its volume** |
| 2 | **Vast.ai** | A | `vastai start\|stop\|destroy instance <id>` | **Partial** — write bundles create/destroy → broker | 4090 <$0.40 | Cheapest; low-trust hosts, weak data residency |
| 3 | **Lambda** | A | launch / restart / **terminate** (no *stop*) | **No** key scoping | H100 ~$2.49–3.29 | "off" = **terminate** (destroys the box; use a persistent FS or re-pull weights) |
| 4 | **AWS EC2** | A | `aws ec2 start-instances` / `stop-instances` | **Yes — gold standard** (IAM on one ARN) | H100 (P5) ~$3.90 | Full VM; STS short-lived creds; best residency/egress control |
| 5 | **GCP Compute** | A | `gcloud compute instances start` / `stop` | **Yes** (custom role, 2 perms) | H100 (A3) ~$3.00 | Full VM; co-best least-privilege |
| 6 | **Azure** | A | `az vm start` / **`az vm deallocate`** | **Yes** (custom RBAC role) | H100 (ND) ~$3–4 | Must **deallocate** — a plain `stop` keeps billing |
| 7 | **CoreWeave** | A | k8s scale replicas → 0 | RBAC namespace role (heavier) | H100 ~$4.25 | Enterprise/multi-GPU; overkill for one box |
| 8 | **Hyperstack** | A | `/{id}/start`, `/stop`, `/hibernate` | No per-VM scope | H100 ~$1.9–2.4 | EU residency; `hibernate` = fast warm restart |
| 9 | **Paperspace/DO** | A | Core API `/machines/{id}/start`/`/stop` | No per-machine scope | H100 ~$2.24–3.18 | Gradient API deprecated; use Core "machines" |
| 10 | **Modal** | B | none — scale-to-zero, per-second | **N/A** (off is automatic, $0) | ~$3.95-eq H100 | **Best serverless escape hatch** — deploy a vLLM function → OpenAI URL |

Honorable mentions: Nebius (IAM-style, EU), Crusoe (ships an MCP server),
TensorDock, Fly.io (`auto_stop_machines`), and managed Shape-B inference
(Replicate / Together / Fireworks / Baseten).

**Shortlist for "launch a GPU, run a model, turn it off":**
- **RunPod** — disposable on-demand default; volume persists weights for fast relaunch.
- **Lambda** — cheapest clean VM API; *design around "off = terminate"*.
- **AWS / GCP** — when **provider-enforced** power-only is a hard requirement.
- **Modal** — no-credential serverless fallback (no power control needed at all).

## Secure least-privilege ON/OFF

The rule the user asked for: **all Prax can do is turn the GPU on and off** — nothing
else. Two ways to guarantee it.

### Option 1 — provider-scoped credential (AWS / GCP)

**AWS — ARN-pinned IAM policy (tightest).** `Start/StopInstances` support
resource-level scoping; `DescribeInstances` does not (separate `*` statement):

```json
{ "Version": "2012-10-17", "Statement": [
  { "Sid": "PowerOneGpuInstance", "Effect": "Allow",
    "Action": ["ec2:StartInstances", "ec2:StopInstances"],
    "Resource": "arn:aws:ec2:us-east-1:123456789012:instance/i-0abc123gpu" },
  { "Sid": "ReadStateOnly", "Effect": "Allow",
    "Action": "ec2:DescribeInstances", "Resource": "*" } ] }
```

Not granted ⇒ not permitted: Terminate, RunInstances, Modify, snapshot, SSM, SSH.
(A tag-conditioned variant works too, but you **must also deny `ec2:CreateTags`/
`DeleteTags`** or the holder could re-tag a different box into scope — prefer the
ARN-pinned form.)

**GCP — custom role + per-instance condition:**

```bash
gcloud iam roles create praxGpuPower --project=PROJECT_ID \
  --title="Prax GPU Power (on/off only)" \
  --permissions=compute.instances.start,compute.instances.stop,compute.instances.get
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:prax-gpu-power@PROJECT_ID.iam.gserviceaccount.com" \
  --role="projects/PROJECT_ID/roles/praxGpuPower" \
  --condition='expression=resource.type=="compute.googleapis.com/Instance" && resource.name=="projects/PROJECT_ID/zones/ZONE/instances/INSTANCE_NAME",title=only-one-gpu-vm'
```

(Add `compute.zoneOperations.get` if the caller polls the long-running op.) Not
granted: delete/insert, setMetadata (no SSH-key injection), disks, snapshots.

### Option 2 — a provider-agnostic power broker (recommended default)

GPU specialists (RunPod/Lambda/Vast) **can't** scope a token to power-only. The
robust, provider-independent answer: the **user** runs a tiny HTTPS endpoint that
holds the real (broad) provider credential *server-side*; Prax holds only a
**bearer token + URL**. Exactly two verbs; the instance ID is **hard-coded in the
broker**:

```
POST /power {"action":"on"}   -> provider start
POST /power {"action":"off"}  -> provider stop
GET  /power                   -> {"state": "..."}   (optional)
```

Reject everything else. This makes "Prax can only power on/off" **true by
construction, regardless of provider**, and is the *only* safe option for
Lambda/RunPod/Vast. It's also a fine default for AWS/GCP (the cloud credential
never leaves the broker host). Mirror the proven
[`prax-sandbox/docs/remote.md`](../../../prax-sandbox/docs/remote.md) daemon model:
**mandatory bearer (constant-time check) + mandatory TLS + a single exposed port**,
nothing else reachable.

### Threat model

A leaked power credential can **start/stop one pre-existing instance and read its
state** — and nothing more. It cannot terminate, create/launch (no surprise bills
or crypto-mining pivot), resize, touch disks/snapshots, read data, inject SSH
keys/metadata, or edit tags/IAM. **Blast radius of a leak = "the GPU flaps on and
off"** — cheap, recoverable, not a data breach, and not unbounded spend (instance
type is fixed; only on-hours vary).

### Reuse Prax's shipped security machinery

- **Ephemeral creds** — make the broker bearer (or an MCP token) short-lived via
  the shipped `MCP_TOKEN_EXPIRY_ENABLED` / `MCP_TOKEN_EXPIRES_AT` and
  `SHARE_LINK_TTL_*`. A lapsed token ⇒ Prax loses the power capability until
  re-issued.
- **SSRF guard** — every broker/provider call goes through `prax/utils/ssrf.py`
  `safe_request()`, which re-validates each redirect hop and blocks
  `169.254.169.254` / RFC1918 / link-local, so a poisoned `GPU_POWER_BROKER_URL`
  can't pivot to the cloud metadata endpoint. (Known caveat in `ssrf.py`: it's
  check-time, not connect-time — not a full DNS-rebinding defense.)
- **HIGH-risk confirmation** — classify `gpu_power_on`/`gpu_power_off` HIGH so they
  hit the `governed_tool.py` confirmation gate; `gpu_power_status` is LOW.
- **Cost guardrail** — schedule an idle-auto-stop via the
  [scheduler](scheduler.md). Call out the billing gotchas above (Lambda
  off=terminate; Azure deallocate-not-stop; RunPod/Vast bill the stopped volume).

## Design sketch (not implemented — design only)

To wire this when built (all default-off, fail-closed):

- Settings (in the `VLLM_*`/`MCP_*` family): `GPU_PROVIDER=""` (`none|broker|aws|gcp`),
  `GPU_POWER_BROKER_URL=""`, `GPU_POWER_BROKER_TOKEN=""` (`repr=False`),
  `GPU_INSTANCE_ID=""`. Unset ⇒ no GPU-power capability.
- Tools `gpu_power_on` / `gpu_power_off` / `gpu_power_status`, registered like
  `finetune_tools.py`, governance-wrapped (HIGH/HIGH/LOW), calling out via
  `ssrf.safe_request()`. After the box is up, set `VLLM_BASE_URL` at runtime
  (`llm_config_update`) — no inference-path change.
- Better still where latency allows: a **serverless (Modal) backend** removes the
  credential entirely — "off" is automatic.

Tracked in [`../IDEAS_BACKLOG.md`](../IDEAS_BACKLOG.md) (#16). Build the
`gpu_power` capability as a **workspace plugin** declaring only `capabilities: http`
+ one scoped secret — structurally incapable of anything but the broker call (see
[`extending.md`](extending.md)).
