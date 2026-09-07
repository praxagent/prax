# Security Policy

> **Draft — 2026-09, pending maintainer review.** Written from the code and
> docs as they stand; the maintainer has not yet confirmed the process below.

## Supported versions

Only the `main` branch of `praxagent/prax` is supported. Release tags are cut by
release-please for the changelog; fixes land on `main` and are not backported.

## Reporting a vulnerability

Please do **not** open a public issue for a suspected vulnerability.

- Preferred: open a private security advisory at
  <https://github.com/praxagent/prax/security/advisories/new>.
  **Maintainer to-do:** as of 2026-09 private vulnerability reporting is *not*
  enabled on this repository (GitHub API `private-vulnerability-reporting` →
  `enabled: false`); until it is switched on under *Settings → Code security*,
  that link will not accept reports from non-maintainers.
- Otherwise: email the maintainer at <tim@praxagent.ai> (the author address on
  this repository's commits).

Include the affected repository and commit, how you were running it
(`docker compose`, `make run-local-*`, the systemd unit), a reproduction, and
the impact you believe it has.

We ask for a coordinated-disclosure window of **90 days** from the report to
public disclosure, shorter by agreement once a fix has shipped on `main`.

## Scope

The repositories that make up the suite, all under
<https://github.com/praxagent>:

- `prax` (this repository — the harness)
- `prax-secrets-proxy` (credential-injecting egress proxy)
- `prax-sandbox` (execution / browser / desktop container)
- `teamwork` (web UI)
- `prax-plugins` (official plugin collection)

A problem in a third-party dependency should go to that project first; tell us
as well if the way Prax uses it makes the impact worse.

## What is not a vulnerability

The threat model is written down in [`docs/security/`](docs/security/README.md).
In particular:

- Prax's HTTP API (`:5001`) binds loopback by default (`PRAX_HOST`); the
  `make run-local-all*` launches also bind TeamWork's backend (`:8000`) to
  loopback via `TEAMWORK_HOST=127.0.0.1`, but TeamWork's own CLI defaults to
  `0.0.0.0` — set `TEAMWORK_HOST` yourself on a manual launch. Both are
  designed to sit behind a network boundary — Tailscale, or an
  authenticating reverse proxy plus a firewall.
  [`docs/security/network-exposure.md`](docs/security/network-exposure.md)
  lists exposing `:5001` (or the Vite dev server) to the internet as an
  anti-pattern. A report whose precondition is a deployment that ignores that
  guidance is a deployment problem, not a vulnerability in Prax.
- Anything that requires an attacker who already has shell access to the Prax
  host, or can already read its `.env`.
- Prompt-injection outcomes that a documented control explicitly does not cover
  (for example, the lethal-trifecta guard `LETHAL_TRIFECTA_GUARD` is default-off
  — see [`.env-example`](.env-example) and
  [`docs/security/tool-risk.md`](docs/security/tool-risk.md) for the risk model).

A report that shows a **documented control failing to do what the docs say**
is in scope — that is exactly what we want to hear about.

## Known limitations (please read before reporting)

[`docs/security/README.md`](docs/security/README.md) indexes the security model
and its documented gaps. Things already known as of 2026-09:

- Prax applies **no application-level authentication** to its own HTTP routes
  (`/teamwork/*`, `/plugins/*`, `/api/users/*`). `TEAMWORK_API_KEY` is only
  *sent* to TeamWork's `/api/external`; nothing checks it on inbound requests.
  The network is the boundary: anything that can reach `:5001` can drive a
  tool-enabled agent turn.
- The documented Twilio setup runs `ngrok http 5001`
  (`scripts/ngrok-launch.sh`), which publishes that whole surface to the
  internet, not only the Twilio webhook routes. This is being treated as a
  configuration/docs bug; new *consequences* of it are welcome reports, the
  exposure itself is known.
- The sandbox boundary described in
  [`docs/security/sandbox-execution-boundary.md`](docs/security/sandbox-execution-boundary.md)
  has known deviations: on host (non-Docker) installs
  `prax/utils/shell.run_command` executes on the host unless
  `RUNNING_IN_DOCKER` is true, and `prax`'s own compose files still pass the
  model keys into the sandbox service. See the *Known gap* notes in
  [`.env-example`](.env-example).
