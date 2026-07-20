# Security

Prax applies defense-in-depth across all trust boundaries: webhook validation, path traversal protection, sandbox auth, and a multi-layer plugin security model.

## Contents

- [Plugin Trust & Isolation](plugin-trust.md) — Trust tiers, subprocess isolation, capabilities proxy, lifecycle audit
- [Tool Risk Classification](tool-risk.md) — Risk levels, governance layer, supply chain hardening
- [Configuration](configuration.md) — Environment variables, .env setup, all configuration options
- [Network Exposure & Binding](network-exposure.md) — Why Prax/TeamWork bind loopback by default, and how to serve on `0.0.0.0` safely behind an authenticating proxy (Tailscale, IAP, Cloudflare Access, oauth2-proxy)
- [The Sandbox Execution Boundary](sandbox-execution-boundary.md) — Where code-exec tools (`run_python`, `data_query`, `sandbox_shell`, `lean_check`, OpenCode) actually run and what they can reach: **the container is the boundary, never the host** (no host-subprocess fallback); only `/workspace` is mounted (secrets/source are not, except the gated self-improve `/source` mount); container network egress is unrestricted (the residual exfiltration risk, tracked). Why isolation, not command-filtering. The rules for adding a code-exec tool.
