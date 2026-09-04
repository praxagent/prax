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
| `PRAX_USER_ID` | Workspace directory name, such as `usr_alice`. Required by the Docker entrypoint. Selects the shared sandbox workspace; it is not per-request tenant isolation. |
| `FLASK_SECRET_KEY` | A long, random secret for Prax session signing. |
| `LLM_PROVIDER` and its credential/model settings | Select the intended provider and models. For direct OpenAI use, set `OPENAI_KEY`; for Anthropic, select `LLM_PROVIDER=anthropic` and set `ANTHROPIC_KEY`. For proxied models, follow the proxy guide instead of supplying provider keys. |
| `TS_AUTHKEY` | Current Compose files require a nonempty value during interpolation, even with Tailscale disabled. For local use with `COMPOSE_PROFILES` unset, use `TS_AUTHKEY=unused`. Replace it with a real key before enabling `tailscale`. |

The default stack includes TeamWork. Discord and Twilio are optional channels;
Twilio webhooks additionally need a publicly reachable HTTPS endpoint.
See [Configuration](../security/configuration.md).

```bash
docker compose up --build
```

Open **http://localhost:3000**. The full Compose file starts `prax` and `sandbox`;
Prax bundles TeamWork, Qdrant, Neo4j, and optional ngrok inside its container.

The stock files publish host ports and mount the host Docker socket into Prax.
They also mount the checkout read-write at `/source` in the sandbox and pass
`OPENAI_KEY`/`ANTHROPIC_KEY` to its coding agents. Treat this as a trusted local
setup. Adding Tailscale or a proxy does not remove those mounts or close the
ports; review the [deployment boundary](../security/deployment-topology.md)
before using an exposed server.

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

Set `MEMORY_ENABLED=true` and start the normal stack. No extra profile is needed.
Qdrant and Neo4j listen inside `prax`; their ports are not published to the host.

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
OLLAMA_BASE_URL=http://ollama:11434
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
from Neo4j. These check availability, not retrieval. In chat, request memory
status, store a harmless test fact, and retrieve it in a new conversation. Check
the returned fact against the one entered. Host management URLs such as
`localhost:6333/dashboard` require an explicit port mapping or tunnel.

## Remote access and native development

For remote HTTPS access, follow [Tailscale configuration](../security/configuration.md#remote-access-tailscale-sidecar).
Persist node state for this long-running service. Configure authentication and
host port restrictions separately.

For native development, install Python 3.13 through `uv` and the required host
toolchain, then follow the [README](../../README.md). Native and container service
addresses differ; use the configuration for the process you are running.
