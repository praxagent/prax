# GPU power-broker (reference)

A ~150-line, zero-dependency HTTPS endpoint that lets Prax turn **one** cloud GPU
on/off — and nothing else. You run it next to your cloud account; it holds the
real provider credential server-side. Prax's [`gpu_power`](../../prax/plugins/tools/gpu_power/)
plugin holds only a bearer token + the broker URL.

It exists because GPU specialists (RunPod/Lambda/Vast) can't scope an API token to
"power only" — so the broker becomes the least-privilege boundary by construction.
(For AWS/GCP you can alternatively scope an IAM cred directly — see
[`docs/guides/cloud-gpu.md`](../../docs/guides/cloud-gpu.md).)

## API (the entire surface)

```
POST /power {"action":"on"}    # start the configured instance
POST /power {"action":"off"}   # stop it
GET  /power                    # {"state":"on"|"off"|"unknown"}
```

Bearer-gated (constant-time). Everything else → 404. The instance ID is fixed in
the process, so a leaked client bearer can only flip that GPU — never create,
destroy, resize, SSH, or read data. **Blast radius of a leak = the GPU flaps.**

## Run it

Dry-run (no cloud — verify the wiring):
```bash
BROKER_TOKEN=$(openssl rand -hex 32) python3 broker.py     # http://127.0.0.1:8799
curl -s -X POST localhost:8799/power -H "Authorization: Bearer $BROKER_TOKEN" \
     -d '{"action":"on"}'                                   # {"state":"on","provider":"dryrun"}
```

AWS (real, TLS, reachable):
```bash
PROVIDER=aws AWS_REGION=us-east-1 BROKER_INSTANCE_ID=i-0abc123gpu \
BROKER_TOKEN=... BROKER_TLS_CERT=cert.pem BROKER_TLS_KEY=key.pem \
BROKER_BIND=0.0.0.0 python3 broker.py
```
The broker's own cloud cred should still be **scoped** (e.g. the ARN-pinned IAM
policy in `cloud-gpu.md`) as defense-in-depth — the broker is the *behavioral*
boundary, the IAM policy is the *hard* one.

## Security defaults (fail-closed)

- Refuses to start without `BROKER_TOKEN`.
- Refuses a non-loopback bind without TLS (`BROKER_TLS_CERT` + `BROKER_TLS_KEY`).
  Bind `127.0.0.1` if you terminate TLS at a proxy (e.g. `tailscale serve`).
- Bearer never logged.

## Point Prax at it

```bash
GPU_POWER_BROKER_URL=https://your-broker.example.ts.net
GPU_POWER_BROKER_TOKEN=<the BROKER_TOKEN>
```
With `GPU_POWER_BROKER_URL` unset, the `gpu_power` plugin registers no tools
(fail-closed) — Prax stays sleek when there's no GPU.

## Providers

`PROVIDER=dryrun` (default, in-memory) · `aws` (boto3 `start/stop/describe`) ·
`runpod` (REST `resume/stop`; needs `RUNPOD_API_KEY`). Add an adapter by writing a
3-line function (start/stop/state on the one instance) and registering it in
`_PROVIDERS`.
