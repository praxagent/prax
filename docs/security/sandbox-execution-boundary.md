# The sandbox execution boundary

[← Security](README.md)

This doc states, precisely, **where Prax's code-execution tools run, what they can
and cannot reach, and why the container — not per-command filtering — is the trust
boundary.** It's the companion to [network-exposure.md](network-exposure.md)
(which covers *inbound* binding): this covers the *execution / egress* boundary.

## The model: the container is the boundary

The code-execution tools that dispatch through `prax_sandbox_client` run
**inside the sandbox Docker container, never on the Prax host**. Not every tool
that runs a model-authored string takes that path — see the Known gap below.

| Tool | Runs where |
|---|---|
| `sandbox_shell`, `run_python` | container (`run_shell` → `exec_in_sandbox` → `container.exec_run`) |
| `data_query` (DuckDB) | container (same path; host never even loads duckdb) |
| `lean_check` | container |
| browser tools | the sandbox's Chromium over CDP |
| `desktop_*` | **container only when `RUNNING_IN_DOCKER=true`** (the compose image); on a native install they run on the **Prax host** via `prax/utils/shell.py` — see the Known gap below |
| `delegate_sandbox` (headless direct code-execution sub-agent) | container — writes+runs code via `sandbox_shell`; registered whenever `SANDBOX_ENABLED` |

**The `prax_sandbox_client` dispatch has no host-`subprocess` fallback.** It
(`control_plane` → `exec_in_sandbox`) resolves the running container and
`exec_run`s into it; if the container is absent it **raises**, and the tool
returns an error. `data_query` additionally refuses when `SANDBOX_ENABLED=false`,
and the regression test `test_data_query_never_executes_on_the_host`
(`tests/test_data_tools.py`) pins that duckdb is never loaded in-process and the
module has no `subprocess`/`os.system`/`exec`.

**Known gap (2026-09): `prax/utils/shell.py:run_command` does fall back to the
host.** It routes to the sandbox only when `settings.sandbox_persistent` is true,
and that property is literally `settings.running_in_docker`
(`prax/settings.py`, `RUNNING_IN_DOCKER`, default false); otherwise it calls
`subprocess.run` on the Prax host. The native install (README host install,
`make run-local-all`, `deploy/systemd/prax.service`) does not set
`RUNNING_IN_DOCKER`, so there every caller of that helper executes on the host as
the Prax OS user: all six `desktop_*` tools in `prax/agent/sandbox_tools.py`
(`desktop_open` passes the model-supplied string to `bash -c`), plugin
`caps.run_command` (`prax/plugins/capabilities.py`), and
`prax/services/mermaid_validator.py`. No `desktop_*` tool is in
`prax/agent/action_policy.py`'s `_HIGH` set, so nothing gates the call. The fix
direction is to route that helper on `settings.sandbox_available` and raise when
the container is unreachable, matching the contract above.

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
| **Prax host filesystem** | ❌ No | prax-sandbox's `docker-compose.yml` and (since 2026-09) prax's own `docker-compose.yml` / `docker-compose.lite.yml` all mount only the workspace at `/workspace`. `FROM '/etc/passwd'` reads the *container's* `/etc`, not the host's. The `docker-compose.dev.yml` overlay is the one exception — see the `/source` row. |
| **`/workspace`** | ✅ Yes (rw) | Which directory that is depends on how the sandbox was started. `make run-local-all` (`Makefile`, `_local-sandbox`) mounts the **user's own** workspace, `WORKSPACE_DIR/<PRAX_USER_ID>`. `deploy/update.sh` passes `WORKSPACE_DIR` through unchanged and defaults it to the **whole `workspaces/` tree**, so unless the operator sets it to the per-user directory, every user's workspace is visible from the container. Either way the mount includes `plugins/` — see the Known gap below. |
| **Container's own image fs** (`/opt`, `/etc`, `/tmp`, installed pkgs) | ✅ Yes | It's the image — no host secrets live here. `/tmp` is internal (never delivered to the user). |
| **Prax source / host `.env` / DB / secrets on disk** | ❌ No · ⚠️ partial with the dev overlay | Neither prax-sandbox's compose nor prax's `docker-compose.yml` / `docker-compose.lite.yml` mount them: the `sandbox` service's only volume is the workspace. (Until 2026-09 prax's two compose files bind-mounted the whole checkout **read-write at `/source`** — `.env`, `.git`, `identity.db*`, `conversations.db*` included — plus `<workspace>/.sandbox/*` at `/root`, `/root/.claude`, `/root/.codex`, `/root/.config/opencode`, leftovers of the removed coding agents. Removed; `tests/test_compose_sandbox_service.py` fails CI if any mount outside `/workspace` comes back.) **Residual:** the `docker-compose.dev.yml` overlay still bind-mounts `./prax`, `./app.py`, `./config.py` and `./tests` read-write at `/source/*` — the harness's own code, though not `.env`, `.git` or the DBs — grandfathered in that test as a ratchet and tracked separately. |
| **Container ENVIRONMENT variables** | ⚠️ **Yes — and they can hold secrets** | See below — this is a real exposure. |
| **Network egress** (pip, curl, DuckDB `httpfs`, …) | ✅ Yes (unrestricted, default) | The container has outbound network — see the residual-risk below. |

### Environment variables ARE readable by any exec tool

The container's env is fully readable by any code-exec tool (`printenv`,
`os.environ`, even DuckDB's `SELECT getenv('X')`). So *anything the operator puts
in the sandbox's environment is reachable by every exec tool* — treat the sandbox
env as readable-by-the-model.

**Status (2026-09): keyless on every compose path.** Previously the compose
passed `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` into the container (for OpenCode),
so those keys were readable + exfiltratable by a code-exec tool driven by
untrusted content — a live lethal-trifecta pair. The coding-agent CLIs were
**removed from the sandbox image** (prax-sandbox #4), the multi-round
coding-session tools were **removed** from Prax entirely (#142), and
prax-sandbox's own `docker-compose.yml` passes **no** keys — that is the container
`make run-local-all` and `deploy/update.sh` start. Prax's own `docker-compose.yml`
and `docker-compose.lite.yml` lagged until 2026-09: their `sandbox` service still
set `ANTHROPIC_API_KEY=${ANTHROPIC_KEY}`, `OPENAI_API_KEY=${OPENAI_KEY}` and
`CLAUDE_CODE_DISABLE_NONINTERACTIVE_CHECK=1`, so whatever Prax's `.env` held under
those names was `printenv`-readable in the `docker compose up` deployment the
Quick Start documents. Those lines are gone; `tests/test_compose_sandbox_service.py`
fails CI if any credential-shaped env key (`*_KEY`, `*_TOKEN`, `*_SECRET`,
`*_PASSWORD`) reappears on a `sandbox` service. A user who reinstalls a
coding-agent CLI themselves adds a *dedicated, spend-capped* key.

**Residual + stronger mitigations (tracked):**
- **Secret-injecting egress proxy (strongest — the real wall).** Run Prax with NO
  API keys at all; route external calls through a local proxy that holds the keys,
  injects them, and returns the response. Prax can't exfiltrate a key it never
  holds (this also removes the *host* `.env` from the model's reach, cf. the
  `source_grep` fix). Prax already supports `OPENAI_BASE_URL` → the LLM half is a
  small step (LiteLLM-proxy or a ~100-line reverse proxy); a transparent forward
  proxy generalises it and adds the egress allowlist. Design: `secrets-proxy.md`
  (planned). This is the concrete build of "make the secret unreachable" — the
  boundary the OpenAI long-horizon-safety assessment names as the real wall (an
  in-code guard the agent can edit is only a speed bump).
- **Restrict container egress** (allowlist/proxy) so even a read secret / a
  `/workspace` file can't leave — breaks the exfiltration leg for env *and* files.
- **Dedicated, spend-capped, rotatable key** for any opted-in coding-agent CLI —
  bounds the blast radius, never the operator's primary key.
- Don't rely on the model "not looking" — assume untrusted content can drive it.

### The `/source` mount is static, not flow-scoped

Earlier revisions of this page described `/source` as mounted "only during" the
HIGH-gated self-improvement flow. **There is no such mechanism** (as of 2026-09,
nothing in `prax/services/codegen_service.py` or `prax/agent/self_improve_agent.py`
adds or removes a mount). The mount was a static line in the compose file that
started the sandbox: until 2026-09 **always present** on prax's
`docker-compose.yml` / `docker-compose.lite.yml` (whole repo, read-write,
including the gitignored `.env`) — now **removed** from both — and **never
present** on prax-sandbox's `docker-compose.yml` (the `make run-local-all` /
`deploy/update.sh` path). What remains is the `docker-compose.dev.yml` overlay,
which still mounts `./prax`, `./app.py`, `./config.py` and `./tests` read-write at
`/source/*` whenever it is used — equally static. The self-improve tools
(`self_improve_read/write/patch/…`) are HIGH-risk and human-gated for their own
reasons; that gate does not control what the container can see. Check the live
shape with `docker inspect prax-sandbox-sandbox-1 --format '{{range .Mounts}}…'`.

## Residual risks & hardening (status)

1. **Unrestricted container egress → data-exfiltration leg.** The model keys are
   out of the container on every compose path (prax-sandbox's since 2026-07,
   prax's own since 2026-09 — see above), but the container
   still has **open outbound network**, so a code-exec tool driven by *untrusted
   content* (indirect prompt injection in a fetched page, a poisoned CSV) could
   still POST `/workspace` data — or any secret a user opted back in — outward
   (`curl`, DuckDB `httpfs`/`getenv`, a `COPY … TO` then upload). The perimeter
   trifecta guard + provenance taint (`UntrustedContentTaint`) reduce the
   *"untrusted-in → sensitive-tool"* path; the raw egress itself isn't restricted.
   **Status: tracked.** Ranked options: **(a)** the **secret-injecting egress
   proxy** (keyless Prax; keys live only in the proxy) — the strongest fix, and it
   removes the *host* `.env` from reach too; **(b)** restrict container egress
   (allowlist/proxy) — kills the leg for env *and* files; **(c)** a dedicated,
   spend-capped, rotatable key for any opted-in coding-agent CLI; **(d)** a
   no-network / read-only DuckDB compute mode.
2. **`/source` mount — removed from the everyday compose files 2026-09; residual
   in the dev overlay** — see "The `/source` mount is static" above.
   `docker-compose.yml` / `docker-compose.lite.yml` no longer mount it;
   `docker-compose.dev.yml` still mounts four source paths read-write (no `.env`
   or DBs). The self-improve flow stays HIGH-risk + human-gated (do not relax
   that), but that gate does not scope any mount.
3. **Known gap (2026-09): `/workspace/plugins/` is auto-imported in-process.**
   `prax/plugins/loader.py` tags any plugin under a workspace plugins dir that is
   not `shared/…` as `PluginTrust.WORKSPACE` and imports it **in the Prax
   process** on `load_all()` with no security scan, no sandbox test and no
   acknowledgement (those gates live only in `plugin_write`, `hot_swap` and
   `import_plugin_repo`); `prax/plugins/capabilities.py` auto-approves every
   secret for the WORKSPACE tier. `load_all()` runs on a user's first turn per
   process (`orchestrator._register_workspace_plugins`) and on the
   unauthenticated `POST /plugins/<name>/acknowledge` route. Because `/workspace`
   is the user's workspace root (rw), a `sandbox_shell` write to
   `/workspace/plugins/custom/<x>/plugin.py` — i.e. model-authored code in the
   container — becomes model-authored code **in the harness** at the next load.
   That is exactly the escalation this page says the container prevents.
4. **Known gap (2026-09): the trifecta guard does not see `delegate_sandbox`.**
   `prax/agent/trifecta.py` classifies `delegate_<spoke>` tools only by
   membership in its `_DELEGATE_UNTRUSTED/_PRIVATE/_SINK` sets and does not fall
   through to the name lists, so `delegate_sandbox` (and `delegate_content_editor`,
   `delegate_plugins`, `delegate_tasks`, `delegate_environment`, among others) has
   no leg at all — the sink with unrestricted egress is invisible to the guard.
   The hub-level `sandbox_shell` **is** classified as a sink. The guard itself is
   off by default (`LETHAL_TRIFECTA_GUARD`).
5. **The container overlay IS the host disk** — a runaway in-container process can
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
- The persistent sandbox should mount only `/workspace`. If a feature needs more
  mounted in, that's a security-review change, not a convenience one.
  (`docker-compose.yml` / `.lite.yml` comply since 2026-09 and
  `tests/test_compose_sandbox_service.py` enforces it; the `docker-compose.dev.yml`
  overlay's `/source/*` mounts are the grandfathered exception — tracked above.)
- Do not add another caller of `prax.utils.shell.run_command` for model-supplied
  strings until it stops falling back to the host.
