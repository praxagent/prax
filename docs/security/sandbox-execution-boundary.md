# The sandbox execution boundary

[← Security](README.md)

This doc states, precisely, **where Prax's code-execution tools run, what they can
and cannot reach, and why the container — not per-command filtering — is the trust
boundary.** It's the companion to [network-exposure.md](network-exposure.md)
(which covers *inbound* binding): this covers the *execution / egress* boundary.

## The model: the container is the boundary

Every tool that runs arbitrary or model-authored code runs **inside the sandbox
Docker container, never on the Prax host**:

| Tool | Runs where |
|---|---|
| `sandbox_shell`, `run_python` | container (`run_shell` → `exec_in_sandbox` → `container.exec_run`) |
| `data_query` (DuckDB) | container (same path; host never even loads duckdb) |
| `lean_check` | container |
| `delegate_sandbox` (OpenCode coding sessions) | container (OpenCode HTTP API, `:4096`, loopback-only) |
| `desktop_*`, browser tools | container (Xvfb + Chromium) |

**There is no host-`subprocess` fallback.** The dispatch (`prax_sandbox_client` →
`control_plane` → `exec_in_sandbox`) resolves the running container and
`exec_run`s into it; if the container is absent it **raises**, and the tool
returns an error. It never degrades to running on the host. `data_query`
additionally refuses when `SANDBOX_ENABLED=false`, and the regression test
`test_data_query_never_executes_on_the_host` pins that duckdb is never loaded
in-process and the module has no `subprocess`/`os.system`/`exec`.

## Why the container, not command filtering

The sandbox's *job* is to run untrusted, model-authored code — so the security
model is **isolation, not validation**. Prax deliberately does **not** try to
allow/deny individual shell commands (the approach the
[OpenCode critique](../research/opencode-critique-eval.md) shows is trivially
evadable — `base64 | sh`, `env cmd`, redirection, `python -c`…). Instead the
*container* (plus host network policy + the cloud security group) is the
perimeter, and Prax's own `governed_tool` risk tiers gate *which tools the model
may call*, not *which strings a shell may run*.

## What is — and is NOT — reachable from inside the container

| Target | Reachable? | Notes |
|---|---|---|
| **Prax host filesystem** | ❌ No | Not mounted. `FROM '/etc/passwd'` reads the *container's* `/etc`, not the host's. |
| **`/workspace`** | ✅ Yes (rw) | The **user's own** workspace, bind-mounted (`WORKSPACE_DIR/<user_id>`). Intended: this is the data the tools operate on. |
| **Container's own image fs** (`/opt`, `/etc`, `/tmp`, installed pkgs) | ✅ Yes | It's the image — no host secrets live here. `/tmp` is internal (never delivered to the user). |
| **Prax source / host `.env` / DB / secrets on disk** | ❌ No (default) | **Not mounted** into the persistent sandbox. |
| **Container ENVIRONMENT variables** | ⚠️ **Yes — and they can hold secrets** | See below — this is a real exposure. |
| **Network egress** (pip, curl, DuckDB `httpfs`, …) | ✅ Yes (unrestricted, default) | The container has outbound network — see the residual-risk below. |

### ⚠️ Environment variables ARE readable — and today carry API keys

The container's env is fully readable by any code-exec tool (`printenv`,
`os.environ`, even DuckDB's `SELECT getenv('X')`). The default `docker-compose.yml`
passes **`ANTHROPIC_API_KEY` and `OPENAI_API_KEY`** into the container so OpenCode
(the coding agent) can call models. So **those keys are readable by `run_python`,
`data_query`, and `sandbox_shell`**, and — combined with the unrestricted egress
above — **exfiltratable** by a code-exec tool driven by untrusted content (an
indirect prompt injection). This is the lethal-trifecta "sensitive-data +
exfiltration" pair at the sandbox layer, and it is **live in the current config**.
More broadly: *anything the operator puts in the sandbox's environment (or a
per-user daemon env) is reachable by every exec tool* — treat the sandbox env as
readable-by-the-model.

**Mitigations (tracked — none fully implemented):**
- **Restrict container egress** (allowlist/proxy) so a read key can't leave — the
  single highest-leverage fix (breaks the exfiltration leg for env *and* files).
- **Use a dedicated, low-blast-radius key for the sandbox** — a separate model
  key with a hard spend cap / easy rotation, not the operator's primary
  `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, so a leak is bounded and rotatable.
- **Keep model keys out of the shared container env** — inject them only into
  OpenCode's own process/config rather than the container-wide environment every
  shell inherits (harder; needs a sandbox-side change).
- Don't rely on the model "not looking" — assume untrusted content can drive it.

### The one elevated case: self-improvement mounts `/source`

The **self-improvement** flow (`self_improve_agent`, opt-in, `@risk_tool` HIGH,
human-gated) mounts the **Prax source tree at `/source`** so OpenCode can read and
modify it. During that flow — and only then — code in the container can reach the
repo, **including the on-disk gitignored `.env` (secrets)**. This is *by design*
(the whole point is to let the agent edit Prax), and it is why the self-improve
tools are HIGH-risk and gated. The everyday persistent sandbox (the one behind
`run_python`/`data_query`/`sandbox_shell`) mounts **only `/workspace`** — verify
with `docker inspect prax-sandbox-sandbox-1 --format '{{range .Mounts}}…'`.

## Residual risks & hardening (status)

1. **Secrets in the container env + unrestricted egress = a live exfiltration
   path.** The container carries `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` (for
   OpenCode) and has open outbound network, so a code-exec tool driven by
   *untrusted content* (indirect prompt injection in a fetched page, a poisoned
   CSV) could read those keys (or `/workspace` data) and POST them out (`curl`,
   DuckDB `httpfs`/`getenv`, a `COPY … TO` then upload). This is the
   **lethal-trifecta "sensitive-data + exfiltrate" pair** at the sandbox layer,
   and it is **present in the default config**. The perimeter trifecta guard +
   provenance taint (`UntrustedContentTaint`) reduce the *"untrusted-in →
   sensitive-tool"* path, but neither the keys-in-env nor the raw egress is itself
   restricted. **Status: not implemented — tracked** (adopt-tracker). Ranked
   options: **(a)** restrict container egress (allowlist/proxy) — kills the leg for
   both env *and* files; **(b)** give the sandbox a **dedicated, spend-capped,
   rotatable** model key, not the operator's primary one, so a leak is bounded;
   **(c)** keep model keys out of the shared container env (inject only into
   OpenCode's process); **(d)** a no-network / read-only DuckDB compute mode.
2. **`/source` mount exposes secrets during self-improvement** — mitigated by
   keeping that flow HIGH-risk + human-gated (do not relax that).
3. **The container overlay IS the host disk** — a runaway in-container process can
   fill the host disk (the 2026-07-08 ffmpeg incident). Operational, not a
   confidentiality issue; see the disk-hygiene notes in `prax/CLAUDE.md`.

## Rules for contributors

- **Never** run model-authored/arbitrary code in-process on the Prax host. Every
  code-exec tool **must** dispatch through `prax_sandbox_client` (`get_client()`)
  and gate on `settings.sandbox_available`. Moving execution in-process (e.g.
  `import duckdb; duckdb.sql(user_sql)` in the Prax process) would turn
  `FROM '/etc/…'` into a **host** file read — the exact failure this boundary
  prevents.
- Treat the container's network egress as reachable by untrusted content; design
  new code-exec tools accordingly (compose with the trifecta guard).
- The persistent sandbox mounts only `/workspace`. If a feature needs more mounted
  in, that's a security-review change, not a convenience one.
