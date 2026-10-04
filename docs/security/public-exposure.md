# Public exposure: TeamWork is trusted, public links are not

Prax treats two surfaces differently, and enforces the difference in code.

| | TeamWork | A public link (ngrok, or any tunnel to the internet) |
|---|---|---|
| Who can reach it | People who can log in to your TeamWork | **Anyone who has the link.** There is no password |
| What Prax may put there | Its normal output: chat, files, notes, artifacts | **Only what a person approved, that exact thing, that time** |
| Whose job it is to keep it safe | **Yours, as the deployer** (below) | Prax's: the gate below |

## Securing TeamWork is your job

Prax shows you its work in TeamWork without asking, because it treats
TeamWork as private. That only holds if you deploy it privately:

- **Keep TeamWork's login on.** Set `INTERNAL_API_KEY` and sign in. Don't run
  it unauthenticated on a shared network.
- **Reach it privately.** Use loopback, Tailscale (`tailscale serve`, not
  `tailscale funnel`), or an SSH tunnel. Avoid putting TeamWork behind ngrok,
  cloudflared or funnel: a public tunnel to TeamWork publishes everything in
  it (chat, files, terminal, desktop), and its login then becomes the only
  protection. If you must, follow "Public tunnels" in
  [network-exposure.md](network-exposure.md) and TeamWork's
  `docs/security/exposure.md` first.
- **Set `PRAX_API_KEY` in both `.env` files.** Then Prax's own routes
  (`/teamwork/*`, `/plugins/*`, `/api/users/*`) answer only TeamWork.

If TeamWork is reachable by people you don't trust, everything Prax shows you
there is reachable by them too. No gate inside Prax can fix that.

## Nothing goes public without a person's decision

**Which tools need a decision.** Every way of putting something on the public
link is an **always-on hard floor**, which holds even when
`HARD_FLOORS_ENABLED` is off:

| Tool | Publishes |
|---|---|
| `artifact_share_public` | an artifact (expires: `ARTIFACT_PUBLIC_HOURS`, max 168) |
| `workspace_share_file` | a workspace file |
| `course_publish(public=True)` | a course site (`public=False` stays in TeamWork and is not gated) |

**What counts as a decision.** A call runs only on one of two things:

- **A person's approval in TeamWork** for that exact call. The approval
  prompt says it will be public: *"anyone who has the link can open it, with
  no password"*. A timed "allow for an hour" grant does **not** count.
- **Your own message** asking for it by name: a share verb plus the thing
  ("share report.pdf publicly", "publish the linear algebra course
  publicly"). "Yes" or "go ahead" is refused, because that's how a
  confirmation meant for one thing unlocks another.

**The registry check, so a new path can't skip the gate.** The share registry
(`prax/services/share_registry.py`) is the only way anything becomes publicly
reachable. It refuses to register a share unless the call is running inside a
person's decision (`prax/services/exposure_gate.py`). A plugin, or a future
feature, that tries to publish directly gets `ExposureNotApproved`, not a
public link. Every share records how it was approved (`approved_by`:
`person:<approval id>` or `user_message`).

**No silent public fallbacks.** `workspace_send_file` used to fall back to a
public link when it couldn't deliver a file directly. It no longer does: the
file stays in TeamWork's file browser, and Prax offers a link you'd approve.

## Over a tunnel, only what was shared is reachable

An ngrok tunnel to Prax's port publishes **every** route on it. These
remain reachable that way:

- **What you shared:** `/shared/<token>/…` (unguessable, expiring for
  artifacts), `/courses/…` and `/notes/…`, all checked against the registry.
  Shared HTML runs under `Content-Security-Policy: sandbox allow-scripts`, so
  it can't use the app's origin.
- **Routes with their own authentication:** Twilio webhooks (signed), `/mcp`
  (its own bearer), and health checks.
- **Prax's private routes: closed.** With `PRAX_API_KEY` unset, a request that
  came through a proxy or tunnel (it carries `X-Forwarded-*` / `Forwarded`
  headers) is refused with 403 (`PRAX_TUNNEL_REQUESTS_NEED_KEY`, default on).
  TeamWork's own calls to Prax are direct and carry no such headers. With
  `PRAX_API_KEY` set, the key decides. Turn the guard off only if a reverse
  proxy *you* run sits in front of Prax; better, set the key.

## Recommended settings

- `PRAX_API_KEY` set, in Prax's and TeamWork's `.env`.
- `SHARE_LINK_TTL_ENABLED=true`, so file and course shares expire too (default
  7 days, `SHARE_LINK_TTL_SECONDS`). Artifact links always expire.
- `OUT_OF_BAND_APPROVALS_ENABLED=true`, so approvals happen in TeamWork, where
  the model cannot answer for you.
- Review what is public with `workspace_list_shares`, and revoke with
  `workspace_unshare_file` / `artifact_unshare`.
