# Security

Prax aims at defense-in-depth across its trust boundaries. Read the linked pages
for what each control actually covers today; the headline, as of 2026-09:

- **Inbound webhooks.** Only the Twilio routes (`/sms`, `/transcribe`, `/respond`,
  `/reader`, `/read`, `/conference`, `/say`, `/play`) carry signature validation
  (`prax/blueprints/twilio_auth.py`), and it is skipped when `TWILIO_AUTH_TOKEN`
  is unset. **Known gap (2026-09):** the TeamWork webhook (`POST /teamwork/webhook`)
  and the rest of `/teamwork/*`, `/plugins/*` and `/api/users/*` have **no**
  inbound credential check — `TEAMWORK_API_KEY` is only sent *outbound* by
  `prax/services/teamwork_service.py` and is never verified on an inbound request.
  The network perimeter is the only control there ([network-exposure.md](network-exposure.md)).
- **Path containment** on the workspace file operations (`safe_join`).
  **Known gap (2026-09):** the Library's `space`/`notebook`/`slug` path segments
  are joined raw in `prax/services/library_service.py` (`_space_path`,
  `_notebook_path`, `_note_path` — no `fullmatch`/`relative_to` check), and
  `workspace_root()`'s legacy fallback in `prax/services/workspace_service.py`
  joins an unresolved `user_id` with plain `os.path.join`.
- **Sandbox access.** There is no per-process sandbox auth key: local mode is
  `docker exec` via the Docker socket; remote mode is the operator-set
  `SANDBOX_DAEMON_TOKEN` (bearer, optional mTLS). The sandbox's own ports are
  unauthenticated and published on loopback only.
- A **multi-layer plugin security model** — with the limits stated in
  [plugin-trust.md](plugin-trust.md).

## Contents

- [Plugin Trust & Isolation](plugin-trust.md) — Trust tiers, subprocess isolation, capabilities proxy, lifecycle audit
- [Tool Risk Classification](tool-risk.md) — Risk levels, governance layer, supply chain hardening
- [Configuration](configuration.md) — Environment variables, .env setup, all configuration options
- [Network Exposure & Binding](network-exposure.md) — Why Prax/TeamWork bind loopback by default, and how to serve on `0.0.0.0` safely behind an authenticating proxy (Tailscale, IAP, Cloudflare Access, oauth2-proxy)
- [Deployment Topology — isolate, sandbox, guardrail](deployment-topology.md) — the **endorsed production shape**: Prax, the secrets-proxy, the sandbox (and optionally TeamWork) as **separate containers on one shared network** — they can talk but can't read each other's filesystem/env/keys. Covers the two proxy modes (reverse = model keys; forward MITM = ~all egress), how to wire forward mode, the **🔒 lock-the-proxy-down-hard** checklist, and the keys-in-Prax opt-out (at your own risk).
- [Credential Registry — the single source of truth (Prax ⇄ proxy, no drift)](credentials.md) — **every** credential Prax supports, classified by whether/how the proxy can hold it (model / forward / local). Backed by `prax/services/credential_registry.py` + a **CI drift-guard test** so a new key can't be added without classifying it. Start here to see what's proxyable and what stays in Prax.
- [The Secrets Proxy — running a KEYLESS Prax](secrets-proxy.md) — Run Prax with **no real API keys in its process**: a small separate proxy holds the keys, injects them into model calls, and streams responses back — so a compromised/injected Prax has **nothing to steal** (the infra-level "make the secret unreachable" wall). Tier 1 (OpenAI + Anthropic, base-URL reverse proxy) is built as a **separate, isolated service** ([`praxagent/prax-secrets-proxy`](https://github.com/praxagent/prax-secrets-proxy)) — real isolation is process/filesystem separation, not a second env file Prax can `open()`; it's opt-in and default Prax is unchanged: allowlist by construction, streaming, an audit log that never logs the key/body. Honest limits (stops theft, not abuse; the proxy becomes the trusted component; only `build_llm()` reads `OPENAI_BASE_URL`/`ANTHROPIC_BASE_URL` — the direct OpenAI clients elsewhere do not) + how to run it. The Tier-2 **forward (MITM) proxy** for REST egress is shipped as an opt-in profile — see [deployment-topology.md](deployment-topology.md).
- [Git repos attached to a space](space-git-repos.md) — Attaching repositories to a Library space: **one ed25519 deploy key per repo** (an account SSH key cannot be scoped — it reaches everything the account can), `IdentitiesOnly=yes` so ssh cannot silently widen that, **write off until a human toggles it, per repository**, and checkouts isolated under their own space with a name check *plus* a resolved-path check. Keys live outside the workspace and `library/spaces/*/repos/` is gitignored, because the workspace commits everything not ignored.
- [Provenance Laundering](provenance-laundering.md) — **Found 2026-08-07, FIXED the same day** (kept as the worked example of "provenance follows the content, not the transport"). Provenance used to be decided by which TOOL returned content: `fetch_url_content` was `untrusted_source`, but the same page auto-captured into `library/raw/` and read back via `workspace_read` was `private_data`, which could ARM the lethal-trifecta guard's *private* leg with attacker text. The fix stamps `provenance: untrusted-external` in `raw_capture`'s front-matter and taints on the **marker**, whatever tool returned it. Remaining limits (in the doc): the marker check lives inside the `UntrustedContentTaint` middleware and so is only active when `AGENT_MIDDLEWARE_ENABLED` is on (default on since 2026-08-07); `library_raw_promote` copies a capture into a note **without** the marker, so the laundering re-opens one hop later; user-PASTED content is never tainted; the → sink leg was never demonstrated.
- [The Sandbox Execution Boundary](sandbox-execution-boundary.md) — Where code-exec tools (`run_python`, `data_query`, `sandbox_shell`, `lean_check`) actually run and what they can reach: those tools dispatch through `prax_sandbox_client` into the container and never fall back to the host. **Known gaps stated there (2026-09):** `desktop_*`, plugin `caps.run_command` and the mermaid validator go through `prax/utils/shell.py`, which runs on the **Prax host** unless `RUNNING_IN_DOCKER=true` (i.e. on every native install); prax's own `docker-compose.yml`/`.lite.yml` still inject `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` into the sandbox and bind-mount the whole repo rw at `/source` — the keyless, `/workspace`-only posture holds only for the sandbox started from prax-sandbox's compose (`make run-local-all`, `deploy/update.sh`); `/workspace/plugins/` is auto-imported in-process by the loader; `delegate_sandbox` is invisible to the trifecta guard. The coding-agent CLIs were removed from the image (prax-sandbox #4) and the coding-session tools from Prax (#142). Residual: egress is unrestricted; ranked mitigations (tracked). Why isolation, not command-filtering; the rules for adding a code-exec tool.
