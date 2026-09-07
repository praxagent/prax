# Setup

[← Guides](README.md)

This guide follows the checked-in Docker Compose configuration. Use one deployment
for one owner or a trusted team sharing an execution environment. Authentication
does not provide isolation between mutually untrusted tenants; see
[Authentication](authentication.md) and [Deployment topology](../security/deployment-topology.md).

## Prerequisites

For Compose, install Git and Docker with the Compose and Buildx plugins. Docker
builds Python, Java, browser, and toolchain dependencies inside the images; host
Python and Java are not required for this path. Allow disk space for the sandbox
image and persistent workspace data.

You need a model provider account, a local model endpoint, or an isolated
[secrets proxy](../security/secrets-proxy.md). Configure [cost controls](cheap-evals.md)
before a live provider run.

**Optional:** to attach a host NVIDIA GPU to the sandbox container for ad-hoc CUDA
work (Whisper acceleration, local inference, ML experiments via `sandbox_shell`),
install [`nvidia-container-toolkit`](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
and use `make sandbox-gpu` (or layer the override in via
`COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml` in `.env`). See
[GPU access — local, cloud, least-privilege power control](cloud-gpu.md).

## Installation Details

Clone all three build inputs as siblings:

```bash
git clone https://github.com/praxagent/prax.git
git clone https://github.com/praxagent/teamwork.git
git clone https://github.com/praxagent/prax-sandbox.git
cd prax
cp .env-example .env
```

Edit `.env` with the values below. Keep it private and out of version control.

## Required Configuration

| Variable | Purpose |
|----------|---------|
| `PRAX_USER_ID` | Workspace directory name, such as `usr_alice`. Required when running in Docker — `app.py` fails fast without it (compose sets `RUNNING_IN_DOCKER=true`). Selects the shared sandbox workspace; it is not per-request tenant isolation. |
| `FLASK_SECRET_KEY` | A long, random secret for Prax session signing. |
| `LLM_PROVIDER` and its credential/model settings | Select the intended provider and models. For direct OpenAI use, set `OPENAI_KEY`; for Anthropic, select `LLM_PROVIDER=anthropic` and set `ANTHROPIC_KEY`. For proxied models, follow the proxy guide instead of supplying provider keys. |
| `TS_AUTHKEY` | Current Compose files require a nonempty value during interpolation, even with Tailscale disabled. For local use with `COMPOSE_PROFILES` unset, use `TS_AUTHKEY=unused`. Replace it with a real key before enabling `tailscale`. |

Prax creates `workspaces/<PRAX_USER_ID>` on first run and, on later starts, points
your identity at it.

> **Known gap (2026-09):** on a *fresh* identity DB the association is not made when the user is created. Startup creates `workspaces/<PRAX_USER_ID>` for service state (`prax/services/state_paths.py`), the first message creates the user under an opaque `usr_<id8>` directory (`identity_service._canonical_workspace`), and the *next* startup's `reconcile_workspace_dir()` repoints the user at `<PRAX_USER_ID>` without migrating — its symlink is only created when the target directory does not already exist. Anything written in that first session (notes, library, schedules) can therefore become invisible after the first restart. Reproduced in a scratch tree during the 2026-09 review; not yet fixed. If a first session's work disappears after a restart, look in `workspaces/` for the `usr_*` directory it was written to — the files are still there.

The default stack includes TeamWork. Discord and Twilio are optional channels;
Twilio webhooks additionally need a publicly reachable HTTPS endpoint.
See [Configuration](../security/configuration.md).

```bash
docker compose up --build
```

Open **http://localhost:3000**. The full Compose file starts `prax` and `sandbox`;
Prax bundles TeamWork, Qdrant, Neo4j, and optional ngrok inside its container.

The stock files publish host ports and mount the host Docker socket into Prax,
which is administrative access to the host daemon. Treat this as a trusted local
setup. (**Closed 2026-09:** until then they also mounted the checkout read-write
at `/source` in the sandbox and passed `OPENAI_KEY`/`ANTHROPIC_KEY` into it —
leftovers from the removed coding agents. The sandbox now gets only
`${WORKSPACE_DIR}/${PRAX_USER_ID}` at `/workspace` and no model keys;
`tests/test_compose_sandbox_service.py` pins that.) Adding Tailscale or a proxy
does not close the ports; review the
[deployment boundary](../security/deployment-topology.md) before using an
exposed server.

### Full and lite modes

| Mode | Start command | Optional profiles |
|------|---------------|-------------------|
| Full | `docker compose up --build` | `local-llm`, `observability`, `tailscale`, `secrets-proxy` |
| Lite | `docker compose -f docker-compose.lite.yml up --build` | `tailscale` |

Both modes bundle memory services. There is no `memory` profile and no `ollama`
profile. The `local-llm` profile belongs to the full file.

## Memory System Setup

Short-term conversation context is separate from long-term memory. Compose starts
the bundled Qdrant and Neo4j services; `MEMORY_ENABLED` controls whether Prax uses
memory. Service data persists under the selected workspace's `.services` directory.

### Enabling Long-Term Memory

`MEMORY_ENABLED` defaults to `true` (`prax/settings.py`), so nothing needs to be
set; `MEMORY_ENABLED=false` turns long-term memory off. No extra profile is
needed: Qdrant and Neo4j are compiled into the `prax` image (`Dockerfile`) and
listen inside the container — `localhost:6333` and `localhost:7474`/`7687` —
while the default compose publishes only `3000`, `8000`, `5001`, `4040` to the
host. Data persists under `WORKSPACE_DIR/<PRAX_USER_ID>/.services/{qdrant,neo4j}/`.
For a host-process Prax (`make run-local-all`), the Makefile starts Qdrant and
Neo4j as Docker containers itself.

### Choosing an Embedding Provider

**OpenAI:** memory text is sent to the configured embedding API. Compare retrieval
quality on your material rather than assuming one provider is always best.

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
```

**Ollama:** runs embeddings on the machine hosting Ollama. For full Compose:

```env
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
OLLAMA_BASE_URL=http://ollama:11434   # the prax service's compose default; native Prax: http://localhost:11434
```

```bash
docker compose --profile local-llm up --build
```

`ollama-init` pulls the configured embedding model after Ollama becomes healthy.
`ollama:11434` is the container-network address. Native Prax on the host uses
`http://localhost:11434` instead. Local embeddings do not make other model calls
or external tools local.

**In-process embeddings:** set `EMBEDDING_PROVIDER=local` to use fastembed without
a separate embedding service. Model downloads may require internet access.
See [Memory](../infrastructure/memory.md#embedding-providers) for details.

### Verifying Memory

Check services without exposing management ports:

```bash
docker compose ps
docker compose exec prax curl -fsS http://localhost:6333/healthz
docker compose exec prax curl -I http://localhost:7474
```

Expect healthy core services, a successful Qdrant response, and an HTTP response
from Neo4j. These check availability, not retrieval. In chat, ask Prax "what's
your memory status?" (it uses the `memory_stats` tool), store a harmless test
fact, and retrieve it in a new conversation. Check the returned fact against the
one entered. Host management URLs such as `localhost:6333/dashboard` require an
explicit port mapping or tunnel under Compose; the host-process path
(`make run-local-all`) publishes the usual ports, so `http://localhost:6333/dashboard`
and `http://localhost:7474` work there.

## Remote access and native development

For remote HTTPS access, follow [Tailscale configuration](../security/configuration.md#remote-access-tailscale-sidecar).
Persist node state for this long-running service. Configure authentication and
host port restrictions separately.

For native development, install Python 3.13 through `uv` and the required host
toolchain — Java 11+ for `opendataloader-pdf` PDF extraction, Docker for the
sandbox, and optionally the `gh` CLI for self-modification PR creation — then
follow the [README](../../README.md). Native and container service addresses
differ; use the configuration for the process you are running.
