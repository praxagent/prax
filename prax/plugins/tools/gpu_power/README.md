# gpu_power — least-privilege cloud GPU on/off

Gives Prax exactly three tools — `gpu_power_on`, `gpu_power_off`, `gpu_power_status`
— and **nothing more**. Prax can flip a cloud GPU on or off; it cannot create,
destroy, resize, SSH into, or read data from anything.

## How it stays least-privilege (two layers)

1. **No cloud credential in Prax.** The plugin only calls a tiny **user-run
   power-broker** (reference: [`examples/gpu-power-broker/`](../../../../examples/gpu-power-broker/))
   over HTTPS with a bearer token. The broker holds the real cloud credential
   server-side and exposes only `POST /power {action:on|off}` + `GET /power`, with
   the instance ID **hard-coded**. So "Prax can only flip the GPU" is true by
   construction, even for providers (RunPod/Lambda/Vast) whose API tokens can't be
   scoped to power-only.
2. **The capability ceiling** in `permissions.md` declares only `http` + the one
   secret `GPU_POWER_BROKER_TOKEN` — the plugin is structurally incapable of
   anything else. All HTTP goes through the SSRF-guarded `caps.http_post/http_get`.

`gpu_power_on`/`gpu_power_off` are classified **HIGH-risk** (they start/stop a
billable instance) so they hit the confirmation gate; `gpu_power_status` is LOW.

## Configure

```bash
GPU_POWER_BROKER_URL=https://your-broker.example.ts.net   # the user-run broker
GPU_POWER_BROKER_TOKEN=<bearer>                            # the only GPU cred Prax holds
```

**Fail-closed:** with `GPU_POWER_BROKER_URL` unset the plugin registers **zero
tools** — no GPU-power capability exists. This keeps Prax sleek when there's no GPU.

## Design & providers

See [`docs/guides/cloud-gpu.md`](../../../../docs/guides/cloud-gpu.md) for the
provider table, the AWS/GCP provider-scoped IAM alternative, the threat model
("blast radius of a leak = the GPU flaps on/off"), and the plug-and-play decision
flow. The recurring philosophy: Prax gets the **ability** (a GPU + the sandbox to
write code on command); no model (Whisper/SAM3/…) is hard-wired.
