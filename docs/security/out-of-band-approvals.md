# Containment outside the agent: approvals, secrets, the browser, egress

**Principle.** An agent that reads untrusted content can be talked into things.
So the checks that stop it must not run through the agent. A person's consent
has to reach the enforcement point without passing through the model. Secrets
must be usable without the model ever holding them. And what leaves the box
must be decided by something the model cannot argue with.

Four opt-in mechanisms implement that. All default off, per the flag rule.
Recommended configuration:

```bash
# prax/.env
OUT_OF_BAND_APPROVALS_ENABLED=true
BROWSER_SECRETS_OUT_OF_CONTEXT=true
BROWSER_PAUSE_FOR_USER=true
EGRESS_GATE_URL=http://127.0.0.1:8790
EGRESS_GATE_TOKEN=<the gate's EGRESS_ADMIN_TOKEN>
# deploy: SANDBOX_EGRESS=1 EGRESS_ADMIN_TOKEN=<same token> (and SANDBOX_LIMITS=1)
```

They need TeamWork with approval support (`/api/external/approvals`,
`/api/approvals`) and prax-sandbox with `docker-compose.egress.yml`.

**TeamWork must authenticate its UI**, because approvals are decided only by
an authenticated person. Set `INTERNAL_API_KEY` in TeamWork's `.env` and log in
once in the browser (the session lasts 30 days), or run behind the
authenticating proxy. Without either, TeamWork **refuses every decision**,
and Prax therefore refuses every action that needs one. That is fail-closed on
purpose: an unauthenticated route cannot tell your browser from a program.

## 1. Out-of-band approvals — `OUT_OF_BAND_APPROVALS_ENABLED`

**Before.** The HIGH-risk gate and the lethal-trifecta gate in
`prax/agent/governed_tool.py` blocked the first call and told the model:
"confirm with the user, then call again with the same arguments". The second
call ran. Nothing checked that a person had been asked, so the model could
confirm itself. That was July review #8. An injected model is exactly the
party that would.

**Now:**
1. The call creates an approval in TeamWork. It is bound to the tool and a
   SHA-256 of its **full** arguments, and carries a readable summary.
2. The call **waits**, heartbeating so the run is not abandoned as idle,
   while TeamWork shows the person a dialog. The dialog sits in the UI, not
   in the chat.
3. The person picks **Allow once**, **Allow for 1 hour** (that tool, for Prax,
   for an hour; every use still recorded) or **Deny**.
4. Prax reads the decision from TeamWork with its own credential, spends the
   approval once, and runs the call. Anything else — denied, unanswered,
   TeamWork unreachable, request vanished — is a refusal.

One approval covers both gates for the same call. `prax/agent/human_approval.py`
drives the gates; `prax/services/approval_service.py` holds the shared core.

**Why the model cannot approve itself.**
- **The model never carries the decision.** Prax reads it from TeamWork, not
  from anything the model says.
- **TeamWork's decision route requires a person.** It needs a logged-in UI
  session cookie. The UI key header alone is refused, since that is the
  scripts path. Any request with `X-API-Key`, `Authorization` or
  `X-Agent-Signature` also gets a 403.
- **The external API cannot decide either.** An agent can only ask, check and
  spend its **own** requests.
- **No model tool can reach the decision route.** Prax's fetch tools block
  loopback, and the sandbox cannot reach the host.

**How strong it is.** An approval is exactly as strong as your UI login: the
`INTERNAL_API_KEY` session cookie, or the proxy's identity. The operator can
choose `APPROVALS_ALLOW_UNAUTHENTICATED=true` in TeamWork to skip that. It is
off by default, because it lets any program that can reach the port approve.

Verified end to end (2026-09-24) against a live TeamWork:
- An approved call ran about 2 s after the click.
- A denied call was refused.
- Retrying the denied call asked the person again, and was refused when
  nobody answered.
- Only the approved call executed.

## 2. Secrets stay out of the model — `BROWSER_SECRETS_OUT_OF_CONTEXT`

`browser_login` returned the stored site password (`SITES_CREDENTIALS_PATH`)
into the model's context, and so into the trace. With the flag it is replaced
by `browser_fill_login(domain, username_selector, password_selector)`. That
tool types the stored values into the page and reports only which fields it
filled. `browser_credentials` never showed the password.

**Stored credentials go only to their own site.** The open page must be HTTPS
on the credential's domain, an alias of it, or a subdomain of either. This is
checked before the username and **again before the password**, because the
page may move. Without the check, an injected page could get the model to
"log in to github.com" while the browser shows `attacker.example`, and the
page would read the password.

The other login route, `browser_request_login`, already has this shape: the
person logs in over VNC, and the agent never sees anything.

Model-provider keys are kept out of Prax entirely by the secrets proxy; see
[secrets-proxy.md](secrets-proxy.md).

## 3. The agent yields the browser — `BROWSER_PAUSE_FOR_USER`

While a person drives the shared browser, every browser action stands down
with "Paused: the user is controlling the browser". That covers Playwright
navigate, click, fill, press and fill-login, and the sandbox CDP tools. A
person counts as driving when:
- a VNC login is in progress, or
- TeamWork reports **Take control** (the browser panel toggle), or any input
  through the panel in the last 30 s.

If TeamWork is unreachable, the agent proceeds. This is a lock on shared
input, not a security boundary, and it must not take the browser down with
TeamWork.

## 4. The egress gate — `EGRESS_GATE_URL` + prax-sandbox `docker-compose.egress.yml`

The sandbox moves onto an internal Docker network with no route anywhere. Its
only exit is prax-sandbox's egress gate, a policy proxy (see that repo's
README). The gate:
- allows, denies, or holds each destination;
- resolves names itself and refuses private, loopback and link-local
  addresses (SSRF);
- logs every decision.

When the policy says **ask**, Prax's `egress_gate_service` puts the question
to a person through the same TeamWork dialog ("The sandbox wants to connect to
example.net:443") and posts the answer back to the gate. **Allow for 1 hour**
works here too.

**Taint.** Prax marks the gate tainted until the last such turn ends when a
turn:
- has read private data (the trifecta's private leg), or
- is **about to** run code in the sandbox, whose `/workspace` *is* the user's
  data. Taint is set before the command runs, since a
  `cat … | curl …` is one call.

While tainted, the policy's `clean_only` destinations are asked about rather
than allowed.

Taint does not fail open:
- Updates are sent synchronously, one at a time, so a "clean" can never
  overtake a later "tainted".
- Each turn's taint is a lease that expires if the turn never cleans up.
- Prax re-asserts taint every minute, well inside the gate's 5-minute TTL.

A person's answer to a gate question is spent only after the gate accepts it,
and Prax stops asking once the gate's own deadline has passed.

Verified end to end (2026-09-24) against the real sandbox image, gate and
TeamWork:
- An asked destination returned 200 after a person allowed it.
- Another was blocked after a person denied it.
- A governed `sandbox_shell` call tainted the gate, and the end of the turn
  cleared it.

## Compared with Meta's Muse (see [research note](../research/meta-muse-secure-vm.md))

| Muse | Prax + TeamWork + prax-sandbox, with the flags on |
|---|---|
| Approvals in the client UI, straight to Sentinel, never via chat | **Same shape.** TeamWork dialog → decision read by governance; agent credentials refused on the decision route |
| Scopes: one-time, session, task, time-bounded, perpetual | One-time, 1 hour, 1 day; revocable. No task-scoped or perpetual grants (perpetual deliberately omitted) |
| Execution stops while asked | Yes: the call blocks until decided or `APPROVAL_WAIT_SECONDS` |
| Sentinel: sole authority for **all** egress, L4 + L7, SSRF after DNS | For the **sandbox**: sole exit, L4 for HTTPS / L7 for plain HTTP, SSRF after DNS with the checked IP pinned. Not L7 for HTTPS (no TLS interception). **Prax's own host process is not behind the gate** |
| Kernel-level per-process taint | Per-container, per-turn taint. Coarser |
| Surrogates for all credentials, including site passwords | Provider keys via the secrets proxy; site passwords filled without entering context. User OAuth tokens: Prax has no OAuth connector store to surrogate |
| Agent paused while the user drives the browser | Yes (flag) |
| Harness itself inside the isolated cell | **No.** Prax runs on the host under systemd confinement; only tool execution is in the sandbox |
| Injection classifiers outside the cell | **No.** Not built; would need the eval gate |

**The honest claim:**
- Prax matches Muse's consent model: out-of-band, scoped, cannot be approved
  by the agent.
- It matches Muse's credential principle for provider keys and site passwords.
- It matches Muse's egress model for the sandbox, where the agent's code runs.
- It does **not** match three things: the harness living in the cell, egress
  control of the harness process itself, and the classifier layer.
