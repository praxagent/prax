# Tool Risk & Supply Chain

[← Security](README.md)

## Tool risk classification

Every tool name has a risk level (`prax/agent/action_policy.py`):

| Risk | Behavior (when the governance wrapper is in front) | Examples |
|------|----------|---------|
| **HIGH** | Blocked on first call; requires user confirmation | `self_improve_deploy`, `browser_click`, `plugin_write`, `plugin_import`, `schedule_create` |
| **MEDIUM** | Executes immediately; logged to audit trail | `note_create`, `browser_navigate`, `arxiv_search`, `course_publish` — and **every unrecognised tool name** (unclassified → MEDIUM) |
| **LOW** | Executes immediately; logged | `sandbox_shell`, `get_current_datetime`, `gpu_power_status` (the complete `_LOW` set as of 2026-09; `workspace_read`, `note_list`, `todo_add` are unclassified → MEDIUM) |

Risk levels come from the static `_HIGH` / `_MEDIUM` / `_LOW` sets in
`prax/agent/action_policy.py` (a tool-site `@risk_tool(risk=RiskLevel.HIGH)`
attribute is also honoured; IMPORTED-plugin tools are elevated to HIGH
dynamically). The behaviour column is enforced by `wrap_with_governance`
(`prax/agent/governed_tool.py`), which is applied in exactly two places: the
hub's `tool_registry.get_registered_tools()` and the MCP server.

**Known gap (2026-09): the HIGH confirmation gate does not reach the HIGH
tools.** Spokes build their own tool lists and run them through
`bind_tools_user_context` + `build_agent_loop` without `wrap_with_governance`
(`prax/agent/spokes/_runner.py`; `prax/agent/trifecta.py`'s module docstring
states the same layering). Every name in `_HIGH` — `browser_click`,
`browser_fill`, `browser_request_login`, `browser_finish_login`,
`schedule_create`, `schedule_update`, `schedule_reminder`, `plugin_activate`,
`plugin_write`, `plugin_import`, `plugin_import_activate`, `self_improve_deploy`,
`gpu_power_on`, `gpu_power_off` — is reached only through an ungoverned
sub-loop: the browser, scheduler and plugin-management names are bound by
`spokes/` agents; `self_improve_deploy` by the self-improvement sub-agent
(`prax/agent/self_improve_agent.py`, `build_agent_loop(llm, tools)` with no
wrapper, reached from the sysadmin spoke via `delegate_self_improve`); and
`gpu_power_on`/`gpu_power_off` come from the `gpu_power` plugin
(`prax/plugins/tools/gpu_power/plugin.py`), which registers no tools unless
`GPU_POWER_BROKER_URL` is set and is not on the hub's plugin promotion
allowlist (`_ORCHESTRATOR_PLUGIN_ALLOWLIST` in `prax/agent/tool_registry.py`).
None is in the hub registry, so as of 2026-09 that registry (45 tools with
default settings, enumerated keyless) contains **zero** HIGH-classified
tools and the first-call block, the scoped-confirm variant
(`HIGH_RISK_SCOPED_CONFIRM`), the earned-trust downgrade and the semantic-entropy
gate that key on `RiskLevel.HIGH` never fire in the shipped topology. What the
hub does gate is the `delegate_<spoke>` tool, which is unclassified (MEDIUM).
Separate mechanisms still hold: the import security scan + acknowledgement gate
in `import_plugin_repo` (load-time, below) and the MCP server's refusal of any
HIGH tool. The fix direction is to wrap spoke tool lists with
`wrap_with_governance` inside `run_spoke` (tracked).

## Supply chain hardening

In March 2026, the [TeamPCP supply chain campaign](https://ramimac.me/teampcp/) compromised Trivy, Checkmarx KICS, and [LiteLLM](https://github.com/BerriAI/litellm/issues/24512) across GitHub Actions, Docker Hub, PyPI, and npm — all by stealing CI/CD credentials and publishing poisoned versions under legitimate project names. The attack exploited mutable version tags (GitHub Action tags force-pushed to malicious commits, Docker Hub tags pointing to backdoored images) and compromised PyPI publishing tokens.

Prax applies the following mitigations against this class of attack:

| Layer | Mitigation | Status |
|-------|-----------|--------|
| **GitHub Actions** | Actions pinned to full commit SHAs, not mutable version tags. A tag like `@v4` can be force-pushed; a SHA cannot. | **Partial** (2026-09): `ci.yml`, `release.yml` and two steps of `fresh-install.yml` are SHA-pinned; `fresh-install.yml` still uses `actions/setup-node@v4` (mutable tag). |
| **Docker base images** | Intended: `Dockerfile`, `Dockerfile.lite` and prax-sandbox's `sandbox/Dockerfile` pin base images to `@sha256:` digests so tag hijacking on Docker Hub or GHCR has no effect. | **Not done** (2026-09): no `FROM` line carries a digest (`node:22-slim`, `python:3.13-slim`, `debian:trixie-slim` by tag). Only the `uv` binary `COPY --from=ghcr.io/astral-sh/uv@sha256:…` is digest-pinned. |
| **Python dependencies** | `uv.lock` contains SHA-256 hashes for every wheel and sdist. `uv sync --frozen` in Docker builds rejects any package whose hash doesn't match. A poisoned PyPI upload (the LiteLLM vector) fails verification. | Done |
| **PyPI quarantine window** | `exclude-newer = "7 days"` in `pyproject.toml` prevents uv from resolving any package version published less than 7 days ago. Most PyPI compromises (LiteLLM, TeamPCP) are detected within 24–72 hours; a 7-day rolling buffer ensures poisoned releases are flagged and yanked before they can enter the dependency tree. | Done |
| **CI/CD secrets** | CI jobs have no publishing credentials, API keys, or deploy tokens. There is nothing to steal from a compromised workflow. | Done |
| **No `pull_request_target`** | CI uses `pull_request` (safe — runs on the PR's merge commit with read-only access), not `pull_request_target` (the Trivy entry point — runs in the base repo context with write access and secrets). | Done |

**Updating pins:** For GitHub Actions, look up the SHA at `https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{tag}`. (When base-image digests land, refresh them with `docker inspect --format '{{index .RepoDigests 0}}' <image:tag>`.)

**Upgrading dependencies:** Since `exclude-newer = "7 days"` is a rolling window, simply run `uv lock --upgrade` at any time to pull the latest packages that have cleared the 7-day quarantine. Review the `uv.lock` diff for unexpected new packages or version jumps, then commit both files together.

### Remaining risk: plugin code execution

Imported plugins execute in **separate host subprocesses** with a stripped environment (`prax/plugins/bridge.py` passes only `PATH`, `HOME`, `LANG`, `PYTHONPATH`). That removes the keys from the child's *environment*; it is not a filesystem or network boundary. However:

- **Known gap (2026-09): the child can still load every key.** The subprocess inherits Prax's working directory, `PYTHONPATH` and `HOME`, and Pydantic settings read `.env` from the working directory, so `from prax.settings import settings` inside the child returns the same credentials the parent holds. Filesystem and network access in the child are unrestricted (plain `open()`, `socket`), and the child runs as the same OS user. Full detail: [plugin-trust.md](plugin-trust.md).
- **Side-channel attacks** (timing, cache) are theoretically possible but impractical over JSON-RPC pipes.
- **Capability abuse** — a malicious plugin could use `caps.http_get()` to exfiltrate data from its scoped directory to an external server, or to probe internal services (SSRF). The HTTP budget (50 requests — counted per `PluginCapabilities` instance for its lifetime, not per invocation: the counter in `prax/plugins/capabilities.py` is never reset), `caps.*` filesystem scoping to `plugin_data/{plugin}/`, and the **SSRF egress guard** (see below) mitigate but do not eliminate this. The HIGH-risk confirmation gate applies only to plugin tools promoted into the hub registry (see the Known gap above).
- **Subprocess escape** is not required — the subprocess already has the host filesystem (see the first bullet). Docker container isolation (future enhancement) would add a real second boundary.

**Current controls:** env-stripped subprocess + static analysis + capabilities gateway + per-tier policy + call budgets + HIGH risk classification (hub-promoted tools only) + runtime auto-rollback + blocking security scan + SSRF egress guard. The audit hook and import blocker in `prax/plugins/sandbox_guard.py` are **defined but never installed** (`install_all_guards` has no callers outside that module as of 2026-09) and should not be counted.

### SSRF egress guard

Outbound HTTP from the plugin gateway (`caps.http_get`/`http_post`) and the URL reader
(`url_reader.fetch_markdown`) is validated by `prax/utils/ssrf.py`: an http/https scheme allowlist,
plus rejection of any host that **is or resolves to** a non-public address. Redirects are followed
manually so every hop is re-validated. Controlled by `SSRF_PROTECTION_ENABLED` (default on) with an
`SSRF_ALLOWED_HOSTS` allowlist for self-hosted dev services. (The sandbox `/v1/shell` is
deliberately arbitrary execution behind the root-equivalent daemon bearer, and is out of scope.)

**Blocked address classes** — a URL is refused if its host is, or DNS-resolves to, any of these.
The check uses Python's `ipaddress` module (`is_private`/`is_loopback`/`is_link_local`/
`is_reserved`/`is_multicast`/`is_unspecified`), so it is not a hand-maintained list, but concretely
that covers:

| Class | Ranges | Why it's blocked |
|-------|--------|------------------|
| **RFC 1918 private** | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | internal LAN / VPC hosts |
| **Loopback** | `127.0.0.0/8`, `::1` | services on the host itself |
| **Link-local** | `169.254.0.0/16`, `fe80::/10` | includes the **cloud metadata endpoint** `169.254.169.254` (credential theft on AWS/GCP/Azure) |
| **Unique-local IPv6** | `fc00::/7` | the IPv6 equivalent of RFC 1918 |
| **Unspecified / reserved / multicast** | `0.0.0.0`, `::`, reserved + multicast blocks | non-routable / abuse-prone |

IPv4-mapped IPv6 addresses (`::ffff:10.0.0.1`) are unwrapped and re-checked so they can't smuggle a
private IPv4 past the guard. Hostnames that fail to resolve are allowed through (a non-resolving host
is not an internal-resource risk, and the real request simply fails) — see `prax/utils/ssrf.py` for
the rationale and the residual DNS-rebinding caveat.

**Known gaps (2026-09):**
- **CGNAT / Tailscale range not blocked.** `100.64.0.0/10` is neither `is_private` nor `is_reserved`
  in Python's `ipaddress`, so `_is_blocked_ip` returns False for it — and that is the range the
  suite's own tailnet perimeter uses. Hosts on the tailnet are reachable through the guard.
- **Not every fetcher routes through it.** `workspace_download` (`prax/agent/workspace_tools.py`)
  calls `requests.get(url, allow_redirects=True)` directly with no scheme or address check, and
  saves the body into the workspace root, where `workspace_read` can read it back.
- **Destination filtering is not query filtering.** `fetch_url_content` and `web_search` send the
  model-chosen URL / query (including any query string) to r.jina.ai or the search provider;
  `prax/agent/trifecta.py` classifies them as untrusted *sources* only, never as sinks, so the
  trifecta guard cannot gate a `?q=<data>` exfiltration to an allowed host.

### Importing external plugins

`plugin_import` and `plugin_import_activate` are classified **HIGH-risk**, but they are
spoke-internal (`sysadmin`), so the confirmation gate does not currently reach them (see the Known
gap at the top). The load-time gate does hold: when the import security scan
(`scan_plugin_security`, called from `import_plugin_repo` / `update_plugin_repo` in
`prax/services/workspace_service.py`) finds warnings, the plugin is flagged
`requires_acknowledgement` and the loader **refuses to activate it** until the warnings are
acknowledged — not a prompt the model can skip. `plugin_import_activate` records that
acknowledgement. This scan applies to IMPORTED (`shared/…`) plugins; WORKSPACE plugins under
`<workspace>/plugins/custom/` are imported in-process without it — see
[sandbox-execution-boundary.md](sandbox-execution-boundary.md).
