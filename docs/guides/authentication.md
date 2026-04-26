# Authentication

[← Guides](README.md)

Prax and TeamWork ship without built-in user authentication — they're designed to sit behind an authentication layer that you choose. This guide covers three approaches, from zero-config to production multi-tenant.

## Quick comparison

| Approach | Users | Code changes | Difficulty | Best for |
|----------|-------|-------------|-----------|----------|
| [Tailscale](#tailscale) | 1-10 | None | Easy | Solo / small team, headless servers |
| [OAuth2 Proxy + Google](#oauth2-proxy--google-oauth) | 1-100 | None | Medium | Teams with Google Workspace |
| [OAuth2 Proxy + GitHub](#oauth2-proxy--github-oauth) | 1-100 | None | Medium | Dev teams, open source |
| [Authentik (self-hosted)](#authentik-self-hosted-oidc) | 100+ | Minimal | Advanced | Full control, enterprise, multi-tenant |

All approaches work with both docker-compose and Kubernetes deployments.

---

## Tailscale

Tailscale creates a private network (tailnet) using WireGuard. Only devices logged into your tailnet can reach your services. No ports exposed to the public internet, no passwords, no OAuth configuration.

This is the recommended starting point — it takes 5 minutes and requires zero code changes.

### How it works

```
Public Internet ──X──> Your workstation (ports closed)

Your tailnet:
  Your laptop (100.64.0.1) ──> Workstation (100.64.0.2:3000) ──> TeamWork
  Colleague's laptop (100.64.0.3) ──> Same workstation ──> TeamWork
```

Tailscale assigns each device a stable IP on your tailnet (100.x.y.z). Traffic is encrypted end-to-end with WireGuard. No central server sees your data.

### Step 1: Create a Tailscale account

Go to [https://login.tailscale.com](https://login.tailscale.com) and sign up. Free for personal use (up to 100 devices).

### Step 2: Bring Prax onto the tailnet

You have two options here.  **Option 2a (recommended)** runs `tailscaled`
as a Docker sidecar in the Prax compose stack — your server's host
network never has to expose Prax, no `tailscaled` install on the host,
and the Tailscale node identity is pinned to a Docker volume so
container restarts don't burn through device slots.  **Option 2b** runs
`tailscaled` on the host directly, which is fine if you already manage
Tailscale system-wide.

**Option 2a: Dockerized Tailscale sidecar (recommended)**

1. In the Tailscale admin console (Settings → Keys), generate a key
   with **Reusable ✓**, **Ephemeral ✗**, **Pre-approved ✓**.  Ephemeral
   nodes count against the free tier's 1,000-min/month minute budget;
   non-ephemeral nodes don't, so this matters for a long-running
   server.
2. Add the key to your Prax `.env`:
   ```
   TS_AUTHKEY=tskey-auth-...
   TS_HOSTNAME=prax
   COMPOSE_PROFILES=tailscale
   TEAMWORK_BASE_URL=https://prax.<your-tailnet>.ts.net
   ```
   Without `COMPOSE_PROFILES=tailscale` the sidecar is silently skipped
   — there's no opt-out flag to set if you don't want it.
3. `docker compose up -d`.  The sidecar joins the tailnet automatically
   and serves `https://prax.<tailnet>.ts.net/` (TeamWork) and
   `https://prax.<tailnet>.ts.net:3001/` (Grafana, when the
   observability profile is also active).

Skip ahead to **Step 3** below — you don't need `sudo tailscale up`
on the host.

**Option 2b: Install on the host**

```bash
# Install
curl -fsSL https://tailscale.com/install.sh | sh

# Start and authenticate
sudo tailscale up

# This prints a URL — open it in any browser to authenticate.
# Example: https://login.tailscale.com/a/abc123def456
# After authenticating, the node joins your tailnet.

# Verify
tailscale ip -4
# 100.64.0.2 (your tailnet IP)

tailscale status
# 100.64.0.2  workstation  you@github  linux  -
```

**macOS:**
```bash
# Install via App Store or Homebrew
brew install --cask tailscale

# Or download from https://tailscale.com/download/mac

# Start from menu bar icon, or:
sudo tailscale up
```

### Step 3: Install on your client device (laptop, phone)

Install the Tailscale app on whatever device you'll access TeamWork from:

- **macOS/Windows/Linux**: [https://tailscale.com/download](https://tailscale.com/download)
- **iOS/Android**: App Store / Play Store

Sign in with the same account. Both devices are now on your tailnet.

### Step 4: Access TeamWork via tailnet IP

```bash
# Find your workstation's tailnet IP
tailscale ip -4
# 100.64.0.2

# Access TeamWork (replace with your IP)
open http://100.64.0.2:3000
```

That's it. No port forwarding, no firewall rules, no certificates. If you're not on the tailnet, you can't reach port 3000.

### Optional: Use a Tailscale hostname

Instead of remembering IPs, use MagicDNS:

```bash
# In Tailscale admin console (https://login.tailscale.com/admin/dns):
# Enable MagicDNS

# Now access via hostname:
open http://workstation.tail12345.ts.net:3000
```

### Optional: HTTPS with Tailscale

Tailscale can provision TLS certificates for your tailnet hostnames:

```bash
# On your workstation
sudo tailscale cert workstation.tail12345.ts.net
# Creates workstation.tail12345.ts.net.crt and .key

# Configure your reverse proxy (nginx/Traefik/Caddy) to use these certs
```

### Adding team members

```bash
# In Tailscale admin (https://login.tailscale.com/admin/users):
# 1. Click "Invite users"
# 2. They install Tailscale and join your tailnet
# 3. They can now reach your workstation's TeamWork
```

### Access control (ACLs)

As your team grows, restrict who can access what:

```jsonc
// In Tailscale admin → Access Controls:
{
  "acls": [
    // Everyone can access TeamWork
    {"action": "accept", "src": ["group:team"], "dst": ["tag:prax:3000"]},
    // Only admins can access Prax API directly
    {"action": "accept", "src": ["group:admins"], "dst": ["tag:prax:5001"]},
    // Only admins can SSH
    {"action": "accept", "src": ["group:admins"], "dst": ["tag:prax:22"]},
  ]
}
```

### Tailscale + Kubernetes

For K8s deployments, use the [Tailscale Kubernetes operator](https://tailscale.com/kb/1236/kubernetes-operator):

```bash
helm repo add tailscale https://pkgs.tailscale.com/helmcharts
helm install tailscale tailscale/tailscale-operator -n tailscale --create-namespace \
  --set oauth.clientId=... --set oauth.clientSecret=...
```

Then annotate your TeamWork service:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: prax-teamwork
  annotations:
    tailscale.com/expose: "true"
    tailscale.com/hostname: "prax"
spec:
  # ...
```

TeamWork is now accessible at `https://prax.tail12345.ts.net` — only from your tailnet.

---

## OAuth2 Proxy + Google OAuth

OAuth2 Proxy is a reverse proxy that requires authentication before any request reaches your app. It supports 20+ providers. This section covers Google — the most common for teams already using Google Workspace.

### How it works

```
User ──> OAuth2 Proxy (:4180) ──> TeamWork (:8000)
              │
              └── Redirects to Google login
              └── Verifies token
              └── Sets cookie
              └── Forwards X-Forwarded-User, X-Forwarded-Email headers
```

TeamWork receives every request pre-authenticated. The user's email is in the `X-Forwarded-Email` header.

### Step 1: Create a Google OAuth app

1. Go to [Google Cloud Console](https://console.cloud.google.com/)

2. Create a project (or select an existing one):
   - Click the project dropdown at the top → "New Project"
   - Name: `prax-auth` (or whatever you like)
   - Click "Create"

3. Enable the OAuth consent screen:
   - Navigate to **APIs & Services → OAuth consent screen**
   - Choose **External** (or Internal if you have Google Workspace and want to restrict to your org)
   - Fill in:
     - App name: `Prax`
     - User support email: your email
     - Authorized domains: your domain (e.g., `example.com`) or leave empty for testing
     - Developer contact: your email
   - Click "Save and Continue"
   - Scopes: click "Add or Remove Scopes" → select `email` and `profile` → "Update" → "Save and Continue"
   - Test users: add your email (required for External apps in testing mode)
   - Click "Save and Continue" → "Back to Dashboard"

4. Create OAuth credentials:
   - Navigate to **APIs & Services → Credentials**
   - Click **"+ Create Credentials" → "OAuth client ID"**
   - Application type: **Web application**
   - Name: `Prax OAuth`
   - Authorized redirect URIs: add your callback URL:
     - For local: `http://localhost:4180/oauth2/callback`
     - For production: `https://prax.yourdomain.com/oauth2/callback`
   - Click "Create"
   - **Copy the Client ID and Client Secret** — you'll need these next

### Step 2: Generate a cookie secret

```bash
# Random 32-byte secret for session cookies
python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
# Example output: aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789_-AB
```

### Step 3: Add OAuth2 Proxy to docker-compose

Create a `docker-compose.override.yml` (this layers on top of your existing `docker-compose.yml` without modifying it):

```yaml
# docker-compose.override.yml
services:
  oauth2-proxy:
    image: quay.io/oauth2-proxy/oauth2-proxy:v7.7.1
    ports:
      - "4180:4180"
    environment:
      # Google OAuth
      OAUTH2_PROXY_PROVIDER: google
      OAUTH2_PROXY_CLIENT_ID: "YOUR_CLIENT_ID.apps.googleusercontent.com"
      OAUTH2_PROXY_CLIENT_SECRET: "YOUR_CLIENT_SECRET"
      OAUTH2_PROXY_COOKIE_SECRET: "YOUR_COOKIE_SECRET_FROM_STEP_2"

      # Where to send authenticated traffic
      OAUTH2_PROXY_UPSTREAMS: "http://teamwork:8000"
      OAUTH2_PROXY_HTTP_ADDRESS: "0.0.0.0:4180"

      # Who can log in (restrict to your domain, or * for any Google account)
      OAUTH2_PROXY_EMAIL_DOMAINS: "*"
      # To restrict to your org: OAUTH2_PROXY_EMAIL_DOMAINS: "yourcompany.com"

      # Pass user identity to TeamWork
      OAUTH2_PROXY_SET_XAUTHREQUEST: "true"
      OAUTH2_PROXY_PASS_USER_HEADERS: "true"

      # Cookie settings
      OAUTH2_PROXY_COOKIE_SECURE: "false"  # Set to true with HTTPS
      OAUTH2_PROXY_COOKIE_HTTPONLY: "true"
      OAUTH2_PROXY_COOKIE_SAMESITE: "lax"

      # Session settings
      OAUTH2_PROXY_SESSION_STORE_TYPE: "cookie"
      OAUTH2_PROXY_SKIP_PROVIDER_BUTTON: "true"  # Go straight to Google login

      # Redirect after login
      OAUTH2_PROXY_REDIRECT_URL: "http://localhost:4180/oauth2/callback"
    depends_on:
      teamwork:
        condition: service_healthy
```

### Step 4: Start with auth

```bash
# Normal start — docker-compose.override.yml is auto-loaded
docker compose up -d

# Access via OAuth2 Proxy port (not TeamWork directly)
open http://localhost:4180
# You'll be redirected to Google login → then back to TeamWork
```

### Step 5: Restrict direct access to TeamWork

Remove the TeamWork port mapping from docker-compose so users can only reach it through the proxy:

```yaml
# docker-compose.override.yml — add this to hide TeamWork's direct port
services:
  teamwork:
    ports: !override []  # Remove the 3000:8000 and 8000:8000 mappings
```

Or if you prefer, just don't expose port 3000 in your firewall.

### Kubernetes setup

For K8s, add OAuth2 Proxy as a sidecar or separate deployment:

```yaml
# In your values override:
# k8s/my-values.yaml
auth:
  enabled: true
  provider: google
  clientId: "YOUR_CLIENT_ID"
  clientSecret: "YOUR_CLIENT_SECRET"
  cookieSecret: "YOUR_COOKIE_SECRET"
  emailDomains: "yourcompany.com"
```

Or deploy OAuth2 Proxy via its own Helm chart:

```bash
helm repo add oauth2-proxy https://oauth2-proxy.github.io/manifests
helm install oauth2-proxy oauth2-proxy/oauth2-proxy \
  --set config.clientID="YOUR_CLIENT_ID" \
  --set config.clientSecret="YOUR_CLIENT_SECRET" \
  --set config.cookieSecret="YOUR_COOKIE_SECRET" \
  --set extraArgs.provider=google \
  --set extraArgs.upstream="http://prax-teamwork:8000" \
  --set extraArgs.email-domain="*" \
  --set ingress.enabled=true \
  --set ingress.hosts[0]=prax.yourdomain.com \
  -n prax
```

### Verifying user identity in TeamWork

Once OAuth2 Proxy is in front of TeamWork, every request includes:

```
X-Forwarded-User: alice
X-Forwarded-Email: alice@yourcompany.com
X-Forwarded-Preferred-Username: alice
X-Forwarded-Groups: engineering,admins
```

TeamWork can read these headers to identify the user and route to the correct workspace. No code changes needed until you implement multi-user workspace routing.

---

## OAuth2 Proxy + GitHub OAuth

Same architecture as Google, but using GitHub as the identity provider. Better for dev teams and open source projects.

### Step 1: Create a GitHub OAuth App

1. Go to [GitHub Developer Settings](https://github.com/settings/developers)
2. Click **"OAuth Apps" → "New OAuth App"**
3. Fill in:
   - Application name: `Prax`
   - Homepage URL: `http://localhost:4180` (or your domain)
   - Authorization callback URL: `http://localhost:4180/oauth2/callback`
4. Click "Register application"
5. **Copy the Client ID**
6. Click "Generate a new client secret" → **Copy the Client Secret**

### Step 2: Configure OAuth2 Proxy

```yaml
# docker-compose.override.yml
services:
  oauth2-proxy:
    image: quay.io/oauth2-proxy/oauth2-proxy:v7.7.1
    ports:
      - "4180:4180"
    environment:
      OAUTH2_PROXY_PROVIDER: github
      OAUTH2_PROXY_CLIENT_ID: "YOUR_GITHUB_CLIENT_ID"
      OAUTH2_PROXY_CLIENT_SECRET: "YOUR_GITHUB_CLIENT_SECRET"
      OAUTH2_PROXY_COOKIE_SECRET: "YOUR_COOKIE_SECRET"
      OAUTH2_PROXY_UPSTREAMS: "http://teamwork:8000"
      OAUTH2_PROXY_HTTP_ADDRESS: "0.0.0.0:4180"
      OAUTH2_PROXY_EMAIL_DOMAINS: "*"
      OAUTH2_PROXY_SET_XAUTHREQUEST: "true"
      OAUTH2_PROXY_PASS_USER_HEADERS: "true"
      OAUTH2_PROXY_COOKIE_SECURE: "false"
      OAUTH2_PROXY_SKIP_PROVIDER_BUTTON: "true"
      OAUTH2_PROXY_REDIRECT_URL: "http://localhost:4180/oauth2/callback"
      # Optional: restrict to a GitHub org
      # OAUTH2_PROXY_GITHUB_ORG: "your-org"
      # Optional: restrict to a specific team
      # OAUTH2_PROXY_GITHUB_TEAM: "engineering"
    depends_on:
      teamwork:
        condition: service_healthy
```

Everything else is the same as the Google setup.

---

## Authentik (self-hosted OIDC)

For full control over authentication — custom branding, LDAP/Active Directory integration, SCIM user provisioning, fine-grained policies, and audit logging. Runs as 3 containers alongside Prax.

### When to use Authentik

- You need to support multiple identity sources (Google + GitHub + LDAP)
- You want branded login pages
- You need audit trails for compliance
- You're running multi-tenant and need per-tenant auth policies
- You don't want to depend on external OAuth providers

### Architecture

```
User ──> Authentik Proxy ──> TeamWork
              │
              └── Authentik Server (OIDC provider)
              └── PostgreSQL (auth database)
              └── Redis (sessions)
```

### Setup overview

1. Deploy Authentik (docker-compose or Helm chart)
2. Create an OAuth2/OIDC application in Authentik's admin UI
3. Point OAuth2 Proxy at Authentik's OIDC endpoints (or use Authentik's built-in proxy provider)
4. Configure allowed users/groups

Authentik's own docs are excellent: [https://docs.goauthentik.io](https://docs.goauthentik.io)

```yaml
# docker-compose.override.yml — Authentik services
services:
  authentik-server:
    image: ghcr.io/goauthentik/server:latest
    command: server
    environment:
      AUTHENTIK_SECRET_KEY: "generate-a-long-random-string"
      AUTHENTIK_REDIS__HOST: authentik-redis
      AUTHENTIK_POSTGRESQL__HOST: authentik-db
      AUTHENTIK_POSTGRESQL__USER: authentik
      AUTHENTIK_POSTGRESQL__PASSWORD: authentik-password
      AUTHENTIK_POSTGRESQL__NAME: authentik
    ports:
      - "9000:9000"  # Authentik UI + OIDC endpoints
    depends_on:
      - authentik-db
      - authentik-redis

  authentik-worker:
    image: ghcr.io/goauthentik/server:latest
    command: worker
    environment:
      AUTHENTIK_SECRET_KEY: "same-secret-as-server"
      AUTHENTIK_REDIS__HOST: authentik-redis
      AUTHENTIK_POSTGRESQL__HOST: authentik-db
      AUTHENTIK_POSTGRESQL__USER: authentik
      AUTHENTIK_POSTGRESQL__PASSWORD: authentik-password
      AUTHENTIK_POSTGRESQL__NAME: authentik

  authentik-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: authentik
      POSTGRES_PASSWORD: authentik-password
      POSTGRES_DB: authentik
    volumes:
      - authentik-db-data:/var/lib/postgresql/data

  authentik-redis:
    image: redis:7-alpine
    volumes:
      - authentik-redis-data:/data

volumes:
  authentik-db-data:
  authentik-redis-data:
```

After starting, access `http://localhost:9000/if/flow/initial-setup/` to create the admin account, then configure an OAuth2 provider for Prax.

---

## Multi-user workspace routing

Once authentication is in place and you're ready for multi-user, the app needs to map authenticated users to workspaces. The flow:

```
OAuth2 Proxy ──> X-Forwarded-Email: alice@company.com ──> TeamWork
                                                              │
                                                              ├── Workspace: /workspaces/alice/
                                                              ├── Qdrant collection: prax-alice
                                                              ├── Neo4j namespace: prax_alice
                                                              └── Sandbox: prax-sandbox-2
```

With the Kubernetes operator, this mapping is automatic — creating a `PraxWorkspace` CR provisions everything. Without the operator, TeamWork would need a small middleware to read the header and route accordingly.

---

## Security checklist

| Item | Single user | Multi-user |
|------|-------------|-----------|
| Don't expose TeamWork port publicly | Use Tailscale or firewall | Use OAuth2 Proxy |
| API keys in environment, not code | `.env` file, `chmod 600` | K8s Secrets or cloud secrets manager |
| HTTPS | Tailscale auto-certs or Let's Encrypt | cert-manager + ingress |
| Session cookies | N/A | `COOKIE_SECURE=true`, `COOKIE_HTTPONLY=true` |
| Restrict login | Tailscale ACLs | `EMAIL_DOMAINS` or `GITHUB_ORG` |
| Audit logging | Prax health telemetry | Authentik audit log |
| API key rotation | Manual | Automated via secrets manager |

## References

- [Tailscale](https://tailscale.com/) — WireGuard-based mesh VPN
- [Tailscale Kubernetes operator](https://tailscale.com/kb/1236/kubernetes-operator)
- [OAuth2 Proxy](https://oauth2-proxy.github.io/oauth2-proxy/) — Authentication proxy
- [OAuth2 Proxy Helm chart](https://github.com/oauth2-proxy/manifests)
- [Google OAuth2 setup](https://console.cloud.google.com/apis/credentials)
- [GitHub OAuth Apps](https://github.com/settings/developers)
- [Authentik](https://goauthentik.io/) — Self-hosted identity provider
- [Prax K8s deployment](../../k8s/README.md) — Helm chart and operator docs
