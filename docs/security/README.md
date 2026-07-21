# Security

Prax applies defense-in-depth across all trust boundaries: webhook validation, path traversal protection, sandbox auth, and a multi-layer plugin security model.

## Contents

- [Plugin Trust & Isolation](plugin-trust.md) — Trust tiers, subprocess isolation, capabilities proxy, lifecycle audit
- [Tool Risk Classification](tool-risk.md) — Risk levels, governance layer, supply chain hardening
- [Configuration](configuration.md) — Environment variables, .env setup, all configuration options
- [Network Exposure & Binding](network-exposure.md) — Why Prax/TeamWork bind loopback by default, and how to serve on `0.0.0.0` safely behind an authenticating proxy (Tailscale, IAP, Cloudflare Access, oauth2-proxy)
- [The Secrets Proxy — running a KEYLESS Prax](secrets-proxy.md) — Run Prax with **no real API keys in its process**: a small separate proxy holds the keys, injects them into model calls, and streams responses back — so a compromised/injected Prax has **nothing to steal** (the infra-level "make the secret unreachable" wall). Tier 1 (OpenAI + Anthropic) is built as a **separate, isolated service** ([`praxagent/prax-secrets-proxy`](https://github.com/praxagent/prax-secrets-proxy)) — real isolation is process/filesystem separation, not a second env file Prax can `open()`; it's opt-in and default Prax is unchanged: allowlist by construction, streaming, an audit log that never logs the key/body. Honest limits (stops theft, not abuse; the proxy becomes the trusted component) + how to run it + Tier 2 (general egress) plans.
- [The Sandbox Execution Boundary](sandbox-execution-boundary.md) — Where code-exec tools (`run_python`, `data_query`, `sandbox_shell`, `lean_check`, OpenCode) actually run and what they can reach: **the container is the boundary, never the host** (no host-subprocess fallback); only `/workspace` is mounted (host source/`.env` are not, except the gated self-improve `/source` mount). **⚠️ But the container ENV carries `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` (for OpenCode), readable by any exec tool, and egress is unrestricted → a live exfiltration path a prompt-injection could use.** Ranked mitigations (tracked). Why isolation, not command-filtering; the rules for adding a code-exec tool.
