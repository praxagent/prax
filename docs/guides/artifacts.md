# Artifacts

Self-contained HTML pages Prax makes for you and keeps updating: a plan, a
table, a chart, a dashboard, a small interactive tool. They stay useful after
the chat has scrolled past. Off unless `ARTIFACTS_ENABLED=true`.

Idea credit: Telepath's [Television](https://television.run), which pins
agent-made artifacts for a person and sandboxes them; see TeamWork's
`docs/comparisons/television.md`.

## How it works

- **Prax makes them.** `artifact_publish(title, html, artifact_id="")` writes
  `artifacts/<id>/index.html` and `manifest.json` in your workspace. Every
  write is a git commit, so earlier versions can be recovered. Passing an
  `artifact_id` replaces that artifact's page and bumps its version.
  `artifact_list` shows what exists.
- **TeamWork shows them, privately.** Prax puts `[artifact:<id>]` in its reply.
  TeamWork's viewer fetches the page from Prax (`GET /teamwork/artifacts/<id>`,
  behind the same credential as TeamWork's other Prax panels). It renders the
  page in a **sandboxed frame**: scripts run, but with an opaque origin and no
  network. That keeps them away from TeamWork's session and `/api/*`. The
  viewer picks up new versions on its own.
- **A public link is a separate act, and a person decides it every time.**
  `artifact_share_public(artifact_id, hours)` puts the page at an unguessable
  `/shared/<token>/<name>` path on Prax's public app, through the ngrok tunnel
  (`NGROK_URL`).
  - **Who decides.** A public link has no password: anyone who has it can
    open it. So this is an **always-on hard floor**: it holds even when
    `HARD_FLOORS_ENABLED` is off. It runs only when a person approves that
    exact share in TeamWork (a timed "allow for an hour" grant does not count),
    or when your own message asks for it by name ("share the retention chart
    publicly"). "Yes" or "go ahead" is not enough.
  - **Expiry.** The link expires (`ARTIFACT_PUBLIC_HOURS`, default 24, at most
    168) and shows the latest version until then.
  - **Revoking.** `artifact_unshare(artifact_id)` revokes every public link to
    the artifact.
  - **What the page can do.** It is served with
    `Content-Security-Policy: sandbox allow-scripts` plus no network, so it
    cannot use the app's origin either.
- **Never TeamWork itself.** Only Prax's public app is behind the tunnel;
  TeamWork must not be exposed (see `docs/security/network-exposure.md`).

## Settings

| Setting | Default | |
|---|---|---|
| `ARTIFACTS_ENABLED` | `false` | Adds `artifact_publish`, `artifact_list`, `artifact_share_public`, `artifact_unshare` and the `/teamwork/artifacts` routes |
| `ARTIFACT_MAX_BYTES` | `2000000` | The largest page accepted |
| `ARTIFACT_PUBLIC_HOURS` | `24` | Default public-link lifetime (max 168) |

## Limits

- **No network inside an artifact.** Data, scripts, styles and images must be
  inline (inline SVG or `data:` URIs). This is deliberate: an artifact written
  while Prax was reading an untrusted page can't phone home.
- **Discord and SMS** can't show an artifact. Prax says it is in TeamWork, or
  shares a public link if you ask.
- **The same rule covers every public share.** `workspace_share_file` and
  `course_publish(public=True)` are gated the same way, and the share registry
  refuses anything made outside a person's decision. See
  [public-exposure.md](../security/public-exposure.md).
