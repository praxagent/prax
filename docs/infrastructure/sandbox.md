# Providing Prax a sandbox

[← Infrastructure](README.md)

Prax delegates code execution, the browser, and the desktop to a **sandbox** — a
Docker environment that now lives in its own project, **prax-sandbox** (a sibling
repo, installed as the `prax_sandbox_client` dependency). Prax runs **with or
without** a sandbox, and the sandbox can be **local** or **remote**. This page is
about *how to give Prax a sandbox*; the sandbox's own internals (OpenCode, the
image, the desktop, the browser/CDP) are documented in the prax-sandbox repo
(`docs/sandbox.md`, `docs/desktop.md`, `docs/browser.md`).

## On / off

`SANDBOX_ENABLED` (default `true`). Set `SANDBOX_ENABLED=false` to run Prax as a
**pure harness**: no sandbox tools, no `delegate_sandbox` / `delegate_desktop`, no
`run_python`, no container dependency. Chat, memory, notes, scheduling, and the
channels all work without it.

## Local sandbox (the default, and the functional example)

Run the sandbox container next to Prax. With docker-compose it comes up
automatically (`docker compose up`). Standalone, the **prax-sandbox repo is the
functional reference**:

```bash
cd ../prax-sandbox && make build && docker compose up -d
```

Prax reaches it in-process — the control plane holds the docker socket and talks to
`sandbox:4096` (OpenCode) and `sandbox:9223` (CDP). Relevant settings:
`SANDBOX_HOST`, `SANDBOX_IMAGE` (`prax-sandbox:latest`), `SANDBOX_TIMEOUT`,
`SANDBOX_MAX_CONCURRENT`, `SANDBOX_DEFAULT_MODEL`, `SANDBOX_MAX_ROUNDS`.

## Remote sandbox

Run the sandbox on another box behind the **control daemon** and point Prax at it:

```bash
SANDBOX_DAEMON_URL=https://sandbox-host:8843
SANDBOX_DAEMON_TOKEN=…
SANDBOX_TLS_VERIFY=true            # true | false | path to a CA bundle
# SANDBOX_CLIENT_CERT=… SANDBOX_CLIENT_KEY=…   # opt-in mTLS
```

The same `SandboxClient` switches to an HTTPS + bearer transport — coding sessions,
shell, the file API, and CDP all work over the wire (live output streams back over
SSE; solutions are pulled into Prax's git workspace on finish). Empty
`SANDBOX_DAEMON_URL` → in-process. See the prax-sandbox repo's `docs/remote.md` for
deploying the daemon (TLS, tokens, Tailscale-or-not).

## How Prax uses it

> **2026-07 — no coding-agent crutch by default.** The sandbox image no longer
> ships the OpenCode/Claude-Code/Codex CLIs or a coding-agent server, and the
> **coding-SESSION tools (`delegate_sandbox`, `sandbox_start/message/review/
> finish/abort/search/execute`) are gated OFF** behind `SANDBOX_CODING_AGENT_
> ENABLED` (default false). Prax codes **directly** — `run_python`,
> `workspace_save`/`workspace_patch` (syntax-linted), `source_read`/`source_grep`,
> `sandbox_shell`. This removes a black-box dependency *and* the need for any
> model key inside the sandbox (see `docs/security/sandbox-execution-boundary.md`).
> The pure-execution sandbox tools below are unaffected.

- `run_python` runs throwaway Python in the sandbox's scratch venv; `sandbox_shell`
  runs a shell command in the container. (The **coding-session** spoke —
  `delegate_sandbox` + the `sandbox_start/…` lifecycle — is opt-in per the note
  above.)
- **`data_query`** (opt-in, `DATA_TOOLS_ENABLED`) runs a **DuckDB SQL** query in
  the sandbox for deterministic number/tabular crunching — DuckDB reads
  CSV/Parquet/JSON files directly (`SELECT … FROM '/workspace/active/x.csv'`), so
  most "process these numbers" tasks are one query rather than a coding session.
  Needs `duckdb` + `pandas` in the sandbox image (installed into `/opt/prax-venv`;
  the tool addresses that venv's python by absolute path, like `lean_check` pins
  `/opt/elan`). Spoke-internal (`prax/agent/data_tools.py`); classified LOW —
  it's read/compute in the already-isolated container. Degrades with a clear
  message when the flag, libs, or sandbox are absent.
- The **desktop spoke** drives the Linux desktop via the `desktop_*` tools.
- `browser_service` connects Playwright to the sandbox's Chrome over CDP, and falls
  back to a local headless Chrome when there is no sandbox.
- Self-improvement coding agents (Claude Code / Codex / OpenCode) run inside the
  sandbox against the mounted source.

All of these are thin wrappers over the `prax_sandbox_client.SandboxClient` facade,
configured by `prax/services/sandbox_bridge.py` (which builds a `SandboxConfig` from
Prax settings + Prax callbacks). Prax depends on prax-sandbox via a uv path source
(`../prax-sandbox`).
