# Docker

[← Infrastructure](README.md)

## Docker Compose

Start with the [setup guide](../guides/setup.md), which includes the required
Prax, TeamWork, and prax-sandbox sibling checkouts and private configuration.

```bash
docker compose up --build
```

The stock configuration is for a trusted owner or shared development environment.
It mounts the host Docker socket into Prax and the Prax checkout read-write into
the sandbox. Those privileges are not a hardened boundary against a compromised
agent. Read [Deployment topology](../security/deployment-topology.md) before
using an exposed server or relying on credential isolation.

### Day-to-day commands

```bash
docker compose up                         # start using existing images
docker compose up --build                 # build images and start the stack
docker compose up --build prax            # start Prax and its dependencies
docker compose up --build sandbox         # start only the sandbox service
docker compose build prax                 # build without starting services
docker compose restart prax               # restart after code-only changes
```

Selecting `sandbox` does not start its dependent Prax service. For code-only
changes, the default source mounts avoid an image rebuild, but the process needs
a restart to load the changes unless a development reloader is active.

### Development reloader

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

The development override enables Flask's reloader. Dependency or Dockerfile
changes still require rebuilding the image.

### Services and ports

| Service | What runs | Addresses |
|---------|-----------|-----------|
| `prax` | Flask, TeamWork, Qdrant, Neo4j, and optional ngrok | Host `3000` and `8000` map to TeamWork's container `8000`; host `5001` is Prax; `4040` is ngrok's dashboard. Qdrant/Neo4j ports stay internal. |
| `sandbox` | Coding agents, Chromium/CDP, desktop, and toolchains from the sibling prax-sandbox image | Stock Compose passes OpenAI/Anthropic key values and mounts the selected workspace at `/workspace` plus the checkout at `/source`. |
| `tailscale` (opt-in) | Tailscale sidecar using kernel TUN mode, `NET_ADMIN`, and `/dev/net/tun` | Tailnet HTTPS routes to TeamWork and, in the full observability profile, Grafana. Node state persists in a volume. |

Prax waits for the sandbox healthcheck before starting. The bundled entrypoint
starts its internal services. A healthy container is an availability signal, not
proof that every UI panel, model credential, or tool path works.

Full Compose profiles are `local-llm`, `observability`, `tailscale`, and
`secrets-proxy`. Lite Compose has the bundled memory services and the `tailscale`
profile, but does not define those other optional services. There is no `memory`
or `ollama` profile. See [Setup](../guides/setup.md#full-and-lite-modes).

### Observability

```bash
docker compose --profile observability up --build
```

Full Compose adds Tempo, Loki, Prometheus, Promtail, and Grafana. Grafana is
published at host port `3002`. The example configuration enables anonymous
Grafana admin access, so restrict its reachability or configure authentication
before exposing it. See [Observability](observability.md).

### Access and credential boundaries

Adding Tailscale does not close the existing host-published ports. Bind or firewall
those ports for your deployment. Keep sandbox control interfaces private.

The sandbox is not a credential-free image by virtue of being a separate
container: stock Compose passes provider values into its environment and mounts
the Prax checkout. Configure independent coding clients and review mounts when
using a [secrets proxy](../security/secrets-proxy.md).

For an alternative container arrangement, derive it from the checked-in Compose
and Dockerfiles, including their sibling build contexts, configuration, workspace
mounts, and service dependencies. A bare `docker build .` / `docker run prax`
example omits those requirements and is not an equivalent deployment.
