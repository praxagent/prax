# Tool Risk & Supply Chain

[← Security](README.md)

## Tool risk classification

Every tool is classified by risk level at the governance layer:

| Risk | Behavior | Examples |
|------|----------|---------|
| **HIGH** | Blocked on first call; requires user confirmation | `sandbox_execute`, `workspace_send_file`, `browser_click`, `plugin_write`, `schedule_create` |
| **MEDIUM** | Executes immediately; logged to audit trail | `note_create`, `browser_navigate`, `arxiv_search`, `course_publish` |
| **LOW** | Executes immediately; logged | `note_list`, `todo_add`, `workspace_read`, `get_current_datetime` |

Risk levels are declared at the tool definition site via `@risk_tool(risk=RiskLevel.HIGH)` and enforced centrally in `tool_registry.get_registered_tools()`.

## Supply chain hardening

In March 2026, the [TeamPCP supply chain campaign](https://ramimac.me/teampcp/) compromised Trivy, Checkmarx KICS, and [LiteLLM](https://github.com/BerriAI/litellm/issues/24512) across GitHub Actions, Docker Hub, PyPI, and npm — all by stealing CI/CD credentials and publishing poisoned versions under legitimate project names. The attack exploited mutable version tags (GitHub Action tags force-pushed to malicious commits, Docker Hub tags pointing to backdoored images) and compromised PyPI publishing tokens.

Prax applies the following mitigations against this class of attack:

| Layer | Mitigation | Status |
|-------|-----------|--------|
| **GitHub Actions** | All actions pinned to full commit SHAs, not mutable version tags. A tag like `@v4` can be force-pushed; a SHA cannot. | Done |
| **Docker base images** | `Dockerfile` and `sandbox/Dockerfile` pin base images to `@sha256:` digests. Tag hijacking on Docker Hub or GHCR has no effect. | Done |
| **Python dependencies** | `uv.lock` contains SHA-256 hashes for every wheel and sdist. `uv sync --frozen` in Docker builds rejects any package whose hash doesn't match. A poisoned PyPI upload (the LiteLLM vector) fails verification. | Done |
| **PyPI quarantine window** | `exclude-newer = "7 days"` in `pyproject.toml` prevents uv from resolving any package version published less than 7 days ago. Most PyPI compromises (LiteLLM, TeamPCP) are detected within 24–72 hours; a 7-day rolling buffer ensures poisoned releases are flagged and yanked before they can enter the dependency tree. | Done |
| **CI/CD secrets** | CI jobs have no publishing credentials, API keys, or deploy tokens. There is nothing to steal from a compromised workflow. | Done |
| **No `pull_request_target`** | CI uses `pull_request` (safe — runs on the PR's merge commit with read-only access), not `pull_request_target` (the Trivy entry point — runs in the base repo context with write access and secrets). | Done |

**Updating pinned digests:** Each Dockerfile contains a comment with the `docker inspect` command to refresh the digest. For GitHub Actions, look up the SHA at `https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{tag}`.

**Upgrading dependencies:** Since `exclude-newer = "7 days"` is a rolling window, simply run `uv lock --upgrade` at any time to pull the latest packages that have cleared the 7-day quarantine. Review the `uv.lock` diff for unexpected new packages or version jumps, then commit both files together.

### Remaining risk: plugin code execution

Imported plugins execute in **isolated subprocesses** with a stripped environment (no API keys, no secrets). The OS process boundary prevents credential theft and memory-space attacks. However:

- **Side-channel attacks** (timing, cache) are theoretically possible but impractical over JSON-RPC pipes.
- **Capability abuse** — a malicious plugin could use `caps.http_get()` to exfiltrate data from its scoped directory to an external server. The HTTP rate limit (50 requests/invocation), filesystem scoping (plugins can only read/write their own `plugin_data/{plugin}/` directory), and HIGH-risk confirmation gate mitigate but do not eliminate this.
- **Subprocess escape** — if a kernel vulnerability allows escaping process isolation, the subprocess has access to the host filesystem (though not to env vars). Docker container isolation (future enhancement) would add a second boundary.

**Current controls:** subprocess isolation + static analysis + capabilities gateway + per-tier policy + call budgets + HIGH risk classification + user confirmation gate + audit hooks + import blockers + runtime auto-rollback + blocking security scan.
