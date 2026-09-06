# Network Exposure & Binding

[← Security](README.md)

How Prax and TeamWork decide *which network interface* they listen on, why the
default is **loopback only**, and how to expose them **safely** when you don't use
Tailscale — e.g. behind Google IAP, Cloudflare Access, or oauth2-proxy.

## The model: secure by default, exposure is opt-in

The **application processes** bind **`127.0.0.1` (loopback) by default**, so a
host install of Prax/TeamWork is **not reachable from the network** — only from
the same machine. A reverse proxy on that machine (Tailscale serve, nginx, Caddy)
reaches the app by dialing `localhost`, so you get remote access **without** the
app ever listening on a routable interface.

| Process | Port | Bind knob | Default |
|---------|------|-----------|---------|
| Prax (Flask) | 5001 | `PRAX_HOST` (`settings.bind_host`) | `127.0.0.1` |
| TeamWork backend (uvicorn) | 8000 | `TEAMWORK_HOST` | `127.0.0.1` (set by the Makefile launch) |
| TeamWork Vite dev server | 5173 | `VITE_BIND_ALL=1` → all ifaces | `127.0.0.1` |

**Known gap (2026-09): the backing services do not follow this rule.** The native
`make run-local-all` path starts Qdrant with `docker run -p 6333:6333` (no
authentication) and Neo4j with `docker run -p 7474:7474 -p 7687:7687
-e NEO4J_AUTH=neo4j/prax-memory` (`Makefile`, `_local-qdrant` / `_local-neo4j`);
`-p HOST:CONTAINER` without an address publishes on **all interfaces**, and
Docker's own iptables rules sit in front of a host firewall such as ufw. The
compose stack publishes Grafana as `3002:3000` with anonymous access enabled at
the **Admin** role (`docker-compose.yml`, `GF_AUTH_ANONYMOUS_*`). Until those
bind `127.0.0.1:` explicitly, the cloud security group / host firewall is the
only thing keeping the vector store, graph DB and Grafana off the network.

> **Containers are the exception, on purpose.** The Prax image sets
> `ENV PRAX_HOST=0.0.0.0` (and TeamWork's image binds `0.0.0.0`) because inside a
> container the network namespace is isolated — the real boundary is the
> *published port* / Kubernetes Service / firewall, not the in-container bind.
> Binding `0.0.0.0` *on the host* is what's dangerous; binding it *inside a
> container whose ports you control* is normal.

**Rule of thumb:** the app should bind `0.0.0.0` only when **something else owns
the security boundary** — a container's port mapping, or an authenticating proxy
plus a firewall. If nothing else owns it, loopback is correct.

## Scenario A — Tailscale (the default here, recommended)

Keep the loopback binds. Expose with `tailscale serve` (tailnet-only) or
`tailscale funnel` (public internet, but still gated by Tailscale identity for
funnel + your ACLs). Either way `tailscaled` runs on the host and dials
`localhost:5173`/`:8000`, so **no app ever binds a routable port**. This is the
safest posture and needs no `*_HOST` changes. (`make run-local-all-tail-dev` sets
this up.)

## Scenario B — serve `0.0.0.0` behind an authenticating proxy

When you can't use Tailscale and want the UI reachable behind, e.g., **Google IAP**
("app proxy"), **Cloudflare Access**, **AWS ALB + Cognito/OIDC**, or
**oauth2-proxy / nginx with auth**. The non-negotiable rule:

> **Binding `0.0.0.0` is safe *only* if an authenticating reverse proxy is the
> sole ingress AND a firewall admits only that proxy.** Never put the raw app
> ports on `0.0.0.0/0`.

Two shapes, in order of preference:

### B1 — Proxy on the **same host** → you DON'T need `0.0.0.0`

Run the auth proxy (oauth2-proxy, nginx+`auth_request`, Caddy `forward_auth`) on
the same machine. It binds the public port (443) and dials the app at
`localhost`. **Keep the app binds at loopback** — they never touch a routable
interface. This is strictly safer than B2; prefer it whenever the proxy can be
co-located.

```
internet ──443──> oauth2-proxy (host) ──localhost:8000──> TeamWork ──localhost:5001──> Prax
```

### B2 — Proxy on a **different host / managed** → app must bind `0.0.0.0`

Google IAP (GCP HTTPS Load Balancer), Cloudflare (proxied), AWS ALB — the proxy
reaches the app over the network, so the app must listen on a routable interface.
Then **all** of the following are required:

1. **Expose only the user-facing surface.** Put the proxy in front of the
   **TeamWork backend (`:8000`, run in *prod* mode** so it serves the built SPA +
   proxies to Prax). Set `TEAMWORK_HOST=0.0.0.0`. **Leave `PRAX_HOST=127.0.0.1`**
   — Prax `:5001` is internal (only TeamWork talks to it); never expose it or the
   Vite dev server. This is a firewall statement, not something the app enforces:
   Prax cannot tell TeamWork from any other caller. `POST /teamwork/webhook`
   (which starts an agent turn) and the other `/teamwork/*`,
   `/plugins/*` and `/api/users/*` routes carry **no inbound credential check** —
   `TEAMWORK_API_KEY` is sent *outbound* by `prax/services/teamwork_service.py`
   and never verified inbound (**Known gap, 2026-09**). Whoever can reach `:5001`
   can drive Prax.
2. **Firewall the app port to the proxy only** — never `0.0.0.0/0`:
   - **Google IAP / GCP LB**: allow the backend port from the Google LB ranges
     **`35.191.0.0/16`** and **`130.211.0.0/22`** (health checks + IAP) only;
     enable IAP on the backend service; grant `IAP-secured Web App User` to the
     allowed identities.
   - **Cloudflare**: allow only Cloudflare's published IP ranges — or, better, use
     a **`cloudflared` tunnel** so there is **no inbound port at all** (this
     collapses to B1-like safety).
   - **AWS ALB**: the instance security group admits only the ALB's security
     group; auth via ALB OIDC/Cognito.
3. **Authenticate at the proxy** (IAP = Google identity; Access = your IdP;
   oauth2-proxy = OIDC). The app is never the auth boundary.
4. **Verify the proxy's signed assertion at the app** (defense-in-depth, so a
   request that bypasses the proxy straight to the bound port is rejected by the
   app itself — not only by the firewall). **TeamWork ships this** as a
   fail-closed, default-off middleware (`teamwork/proxy_auth.py`). Enable it on the
   exposed TeamWork backend:

   ```bash
   PROXY_AUTH_ENABLED=true
   PROXY_AUTH_PROVIDER=iap                 # or: cloudflare_access
   PROXY_AUTH_AUDIENCE=/projects/<PROJECT_NUMBER>/global/backendServices/<ID>
   # Cloudflare Access instead:
   #   PROXY_AUTH_PROVIDER=cloudflare_access
   #   PROXY_AUTH_AUDIENCE=<your Access application AUD tag>
   #   PROXY_AUTH_ISSUER=https://<team>.cloudflareaccess.com   # JWKS derived from this
   ```

   The preset fills the header (`x-goog-iap-jwt-assertion` / `cf-access-jwt-assertion`),
   algorithms (ES256 / RS256), JWKS URL, and issuer; `*_HEADER`, `*_JWKS_URL`,
   `*_ALGORITHMS`, `*_ISSUER` are overridable for a custom provider. Every **HTTP**
   request except `PROXY_AUTH_EXEMPT_PATHS` (default `/health,/healthz`) and CORS
   preflight must then carry a signature the proxy minted, verified against the
   provider's JWKS (audience + issuer + signature). **Fail-closed** for HTTP:
   enabled-but-misconfigured refuses to start; a missing/invalid assertion is
   `401`. Off by default → a complete no-op for Tailscale / same-host-proxy / dev.

   **Known gap (2026-09): WebSockets bypass this check.** `ProxyAuthMiddleware`
   (`teamwork/src/teamwork/proxy_auth.py`) subclasses Starlette's
   `BaseHTTPMiddleware`, whose `__call__` passes any non-`http` scope straight
   through, so TeamWork's WebSocket routes — `/ws`, `/api/terminal/ws/{project_id}`
   (the shared PTY), `/api/browser/ws/{project_id}`, `/api/browser/cast/{role}`,
   `/api/desktop/websockify`, `/api/desktop/clipboard` — are never asked for the
   assertion. For those surfaces the firewall rule in step 2 (plus the proxy's own
   authentication of the upgrade request) is the **only** control; there is no
   app-level second layer until the middleware is rewritten as a raw ASGI
   middleware that handles `websocket` scopes.
5. **TLS terminates at the proxy**; keep the proxy→app hop on a private network.

### Minimal checklist for B2

- [ ] `TEAMWORK_HOST=0.0.0.0`, TeamWork in **prod** mode (`:8000` serves the SPA)
- [ ] `PRAX_HOST=127.0.0.1` (Prax stays internal) — and Vite not running in prod
- [ ] Firewall/SG admits **only** the proxy's source ranges to `:8000`
- [ ] Auth enforced at the proxy (IAP / Access / OIDC)
- [ ] App verifies the proxy JWT — set `PROXY_AUTH_ENABLED=true` (+ provider/audience) on TeamWork (HTTP routes only — WebSockets rely on the firewall, see above)
- [ ] No raw port (`5001`/`5173`/`8000`) reachable from `0.0.0.0/0`

## Anti-patterns

- ❌ `PRAX_HOST=0.0.0.0` on a cloud VM with the security group open to the world
  and no auth proxy — this is exactly the exposure loopback-by-default prevents.
- ❌ Exposing Prax `:5001` or the Vite dev server `:5173` to the internet. Only the
  user-facing TeamWork surface should ever be fronted.
- ❌ `ngrok http 5001` as the Twilio webhook tunnel — which is what
  `scripts/ngrok-launch.sh` runs when `NGROK_AUTHTOKEN` is set, and what the
  Twilio setup in [configuration.md](configuration.md) tells a host install to
  run. An ngrok port tunnel is **not path-scoped**: it publishes every Flask route
  above (the unauthenticated `/teamwork/*`, `/plugins/*`, `/api/users/*` included)
  to the public internet, not just `/sms`, `/transcribe` and `/shared/<token>`.
  Restrict the tunnel to the Twilio paths (a path-filtering rule at the tunnel, or
  a reverse proxy that forwards only those routes) before using it.
- ❌ Relying on "nobody knows the IP/port." Obscurity is not the firewall.
