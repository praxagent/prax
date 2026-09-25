# Meta Muse Secure VM — runtime cell + Sentinel — assessment

**Sources:**
- Meta's ["How We Built Safety Into Muse"](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse)
  (Tarek Sheasha, Meta Superintelligence Labs, 2026-09-08). [security.muse.ai](https://security.muse.ai)
  redirects to it; the full text was read.
- David Singleton's follow-up post, [x.com/dps/status/2103161493722419334](https://x.com/dps/status/2103161493722419334)
  (2026-09-24).

Muse is Meta's personal agent: every user gets a cloud VM that they share with
it. Assessed 2026-09-24.

**⚠️ Source limits.**
- Every Muse mechanism below is Meta's own description of its own system.
  Nothing was run, and the promised external audits cover only the
  forthcoming Confidential VM. Read the Muse side as design statements, not
  verified properties.
- The Prax side *was* checked against the code; file references are given.
- An earlier draft of this note, written from a summarising fetch, described
  Sentinel as "rules-based, not an LLM". **The post never says that.** It calls
  Sentinel "a separate host-side agent", driven by policy the user sets, that
  also "generates a user-visible purpose for the request". Whether a model sits
  inside it is not stated.

**Verdict: document + adopt four things.**
- Prax already matches Muse in three places:
  - keyless model credentials (the secrets proxy *is* credential surrogation);
  - a separate execution cell (the sandbox container, rootless in production);
  - untrusted-input labelling (provenance tainting in the agent loop).
- Muse's lead is one principle applied everywhere: **authority lives outside
  the cell the agent runs in, and the agent's consent path never goes through
  the agent.** Prax breaks that principle in three places:
  - its HIGH-risk confirmation is **self-confirmable by the model**
    (July review #8, still open);
  - its governance runs **in the agent's own process**;
  - **nothing** governs the sandbox's network egress.

---

## What Muse describes

**Two security domains on one box.** "The right mental model is two isolated
security domains on one box, not an LLM powered agent with root."

- **Runtime cell.** It holds the harness itself (the `hatch` daemon), the user's
  workspace, and every binary the agent runs. It runs as a `systemd-nspawn`
  container with its own full Debian root filesystem.
  - Root inside maps to an unprivileged host user.
  - It has a virtual network interface and filtered syscalls (no `io_uring`).
  - `CAP_SYS_PTRACE` and `CAP_NET_ADMIN` are dropped.
  - It is "expected to process untrusted data".
- **Outside the cell**, as separate systemd units:
  - **`hatch-safety`** — independent models and classifiers on every inference
    request and response, outside the cell "so attackers cannot disable these
    protections".
  - **`privsep`** workers — run built-in connector logic with tightly scoped
    credentials. The connector CLIs inside the cell "simply parse their
    arguments, open any files the caller is already allowed to access, and pass
    typed arguments and file descriptors over a Unix socket".
  - **`hatch-authd`** — the credential store (OAuth tokens, passwords, kept in
    the user's VM, not centrally), and it mints the surrogate tokens.
  - **Sentinel** — "the sole permission authority for connector actions and
    network egress".
  - Postgres for durable state, "separate from the runtime cell and from the
    credential store".
  - Constrained proxies for inference and telemetry.
- **IPC** is Unix sockets with `SO_PEERCRED` and peer ACLs: "kernel
  authenticated, least-privilege interprocess communication with no secrets to
  steal".
- **Three separate authorities**:
  - privsep decides *where* credential-capable code runs;
  - authd decides *which* credentials a caller may receive (each worker is
    identified by its cgroup and has a credential allowlist);
  - Sentinel decides *whether* an action may happen.

**Sentinel.**
- **Connectors.** A connector call inside the cell submits the connector,
  method, class of action, scope, and the user's request. Sentinel evaluates
  the **user-set** connector policy and returns allow, deny, or ask.
- **Egress.** Traffic reaches Sentinel through a forward proxy, enforced with
  userns, veth boundaries and eBPF. Sentinel sees L4 and L7: hostname, resolved
  *and* final IP, port, protocol, method, path, and the decoded request. SSRF
  checks run after DNS resolution.
- **Credential insertion.** Code in the cell only ever holds surrogate tokens.
  After a request is authorised, Sentinel swaps in the real credential from
  authd at the boundary.
- **Tainted egress.** Every tool process starts clean and becomes tainted when
  it reads user data. A clean request that fits a narrow auto-allow policy goes
  through; tainted or unverifiable processes fall back to approval.
  Implemented with eBPF cgroup programs plus eBPF on **new LSM hooks Meta
  added** for taint propagation.

**Human in the loop.**
- An "ask" creates a pending approval and **execution stops**.
- The dialog appears in the client UI, "not via their conversation with Muse",
  and the answer goes **directly to Sentinel**.
- Approvals are "strict capabilities, not conversational suggestions", bound
  to the connector or destination and the use case.
- Scopes available: one-time, session, task, time-bounded, or perpetual.
  Sentinel chooses which to offer and enforces that later calls match exactly.
- Read-only, previously allowed, or low-risk actions don't ask: "put friction
  where consent matters".

**Least privilege.**
- Read and write access are separated wherever the service supports it.
- Controls go finer than OAuth scopes: for example, Gmail read access without
  Gmail settings.
- The email connector **filters one-time codes, password-reset links and magic
  links** (deterministic filters plus a classifier), so an agent holding your
  inbox cannot impersonate you elsewhere.

**Defense in depth against prompt injection.** Framed explicitly around Simon
Willison's lethal trifecta:
1. a model trained to resist it (Muse Spark 1.3, "close to SOTA", with their
   own evals);
2. untrusted-input labelling in the harness;
3. an ensemble of injection classifiers, trained independently of the model,
   run on all external data entering context;
4. human approval for anything that moves data out of the VM;
5. beneath all of it, the deterministic boundaries above.

**Browser.**
- Chromium runs behind a virtualisation layer. The user can watch and take
  over at any time.
- Logins go through a client UI (integrated with password managers) straight
  into authd, and are "injected into the browser window at the point of need".
- A broker outside the cell owns the CDP connection.
- The browser sub-agent sees an **accessibility-tree snapshot, not the DOM**,
  so it cannot read filled credentials back.
  - It has no JavaScript, no script verbs, and no exec in the browser process,
    and devtools are disabled.
- **The agent is paused while the user has control or while a credential is
  being filled.**
- Classifiers block the action or ask the user when they see:
  - off-task personal-data egress;
  - injection in the DOM, in images or media, or in downloaded files;
  - a high-risk form submission.
- Navigation is checked against Meta's malicious-site lists.

**Purchases.**
- Checkout pages are detected and need approval with the exact details, every
  time.
- The wallet (Stripe Link) issues single-use card numbers bound to one
  merchant, one amount, and a time window.

**Also.**
- Bug bounty up to $300k, and up to $130k for a single-user prompt injection.
- Continuous VM backups; users can download every file, including Muse's
  memory about them.
- PII-sanitised trajectories train the model by default, with an opt-out.
- A Confidential VM "later this year" is meant to stop Meta itself reading the
  VM; today "it does not prevent Meta from accessing data when necessary".

## Prax, mechanism by mechanism (checked 2026-09-24)

| Muse | Prax today | Gap |
|---|---|---|
| Approvals: out-of-band client dialog, answer goes straight to Sentinel, scoped capabilities (one-time / session / task / time-bounded / perpetual), execution stops meanwhile | `governed_tool.py:494` blocks the first HIGH call with *"Please confirm with the user … call {tool} again with the same arguments"*; the second call runs. Nothing verifies a user said yes. July review **#8**, still open. TeamWork **already has** the right primitive: `require_approval` in `routers/external.py:209` gives single-use approvals bound to the exact action, where the decider must differ from the requester. It only gates external agents acting on TeamWork, not Prax's own tools | **The most important gap, and mostly wiring.** Prax's "human in the loop" is a speed bump the model clears itself. |
| Authority outside the agent's cell (Sentinel, authd, privsep, hatch-safety) | The agent runs on the host (`app.py` under systemd confinement). Governance and the trifecta guard run in the same process; only tool *execution* is in the sandbox | An injected Prax holds the code meant to restrain it. |
| Sentinel owns **all** egress: L4 + L7, post-DNS SSRF | The forward proxy injects keys but **passes unknown hosts through** (`prax-secrets-proxy/secrets_proxy/mitm_addon.py:109`). Sandbox egress is unrestricted. SSRF helpers are per-tool (`validate_url`, `safe_request`) | Nothing outside the agent decides what leaves the box. |
| Tainted egress (per process, kernel-tracked) | Trifecta legs per turn, at tool granularity; provenance tainting of tool results | Cannot see inside one `sandbox_shell` call: read the workspace, then `curl` it out. |
| Surrogates for every credential, including site passwords filled at point of need | Provider keys: yes (the secrets proxy). Site logins: **two paths**. `browser_request_login` has the user log in through VNC, so the agent never sees the password — the right shape. `browser_login` (`browser_tools.py:215`) **returns the real password from `sites.yaml` into the model's context** and the trace | Keep the first path, remove the second. |
| Agent paused while the user drives the browser or a credential is filled | No pause: the agent can act while the user has VNC control | Small, real. |
| Browser sees an accessibility tree, no page JS, devtools off | No JavaScript-evaluation tool among Prax's browser tools (navigate / read_page / click / fill / press / find / screenshot). `read_page` format not compared | Holds on JS. |
| Privsep: connector logic runs outside the agent, per-worker credential allowlists | IMPORTED plugins run in a subprocess and call capabilities back in the parent, with per-plugin approved secrets. Built-in tools run in-process | Same direction, weaker boundary. |
| `hatch-safety` injection classifiers, outside the cell, can't be disabled | No injection classifier | Real, but must pass the eval gate (false positives cost too). |
| Email connector strips one-time codes and reset links | No email connector; SMS and X/social readers exist | For when an email channel arrives (it is gated on injection evals in the adopt tracker). |
| Malicious-site blocklist on navigation | SSRF / private-IP checks only | Minor. |
| Download everything, memory included; continuous backup | TeamWork file browser over a git-backed workspace with history; nightly backup. Memory (Qdrant/Neo4j) is not browsable as files and not in a one-click export | Minor. |
| Confidential VM (stop the operator reading) | Self-hosted: the user is the operator | Structural advantage, not a gap. |
| Trajectories train the model by default | Prax trains nothing on user data | Not a gap. |

**Where Prax is already ahead:**
- open source today;
- self-hostable, so no operator-access problem to solve;
- keyless model credentials in production;
- a git-backed workspace with history;
- per-tool risk tiers with an audit trail;
- a pre-registered eval gate for behaviour changes.

## Status — built 2026-09-24 (all opt-in; see [out-of-band-approvals.md](../security/out-of-band-approvals.md))

All four adopts below are built, each verified end to end against live
services:
1. **Out-of-band approvals.** Flag `OUT_OF_BAND_APPROVALS_ENABLED`. Built from
   TeamWork's approval dialog and scoped grants plus Prax's gates.
2. **Passwords out of context.** Flags `BROWSER_SECRETS_OUT_OF_CONTEXT` and
   `BROWSER_PAUSE_FOR_USER`, plus TeamWork's Take control toggle.
3. **An egress authority.** prax-sandbox's egress gate, with Prax answering
   its questions through the same dialog.
4. **Coarse taint.** Per turn and per container.

The comparison table in the security doc says what now matches Muse and what
still does not: the harness itself is not in a cell, the Prax process's own
egress is not gated, and there is no classifier layer.

Building it exposed a TeamWork data-loss bug. TeamWork shared **one** SQLite
connection across concurrent sessions, so a session closing rolled back
another's in-flight insert, which still answered 200. It is fixed with a
connection per session, and a regression test covers it.

## Adopt (ranked)

1. **Real, out-of-band approvals.** This fixes July #8.
   - A HIGH-risk or trifecta-closing call creates a TeamWork approval and the
     turn *stops*. The user answers in the TeamWork UI, never in chat, and the
     decision reaches the governance layer directly.
   - Grants are capabilities bound to the tool and its arguments: one-time by
     default, with Muse's session / task / time-bounded scopes as later options.
   - TeamWork's `require_approval` already implements the single-use,
     action-bound, requester≠decider core. Prax needs a client for it and a
     resume path for a stopped turn (the durable-checkpoint work is the seam).
   - Flag-gated, because it changes every HIGH call.
2. **Passwords never reach the model.**
   - Retire `browser_login` in favour of the existing `browser_request_login`
     VNC flow.
   - If stored credentials stay, fill them server-side at the selector, so the
     value never enters context or trace.
   - Pause the agent while the user holds the browser.
3. **An out-of-process egress authority ("Sentinel-lite").**
   - Make the forward proxy the sandbox's only exit (an internal Docker network
     whose only route is the proxy), deny-by-default, with allow / deny / ask at
     host and HTTP level and post-DNS SSRF checks. "Ask" reuses adopt 1.
   - Flag- and eval-gated. Tool-level deny-by-default (`UNKNOWN_TOOL_HIGH_RISK`)
     was rejected on measured cost regressions in the 2026-07-08 flag campaign
     and deleted on 2026-08-07. Muse's own answer, "put friction where consent
     matters", is what the policy has to achieve.
4. **Process-level taint for sandbox egress** — parked until 3 exists. Once the
   proxy is the only exit, treat egress from the sandbox after workspace-reading
   calls as the private-data leg. Start per container and per turn; Muse needed
   new LSM hooks to do it per process.

**Document, don't adopt yet:**
- an injection-classifier layer outside the agent (behind the eval gate);
- email-style filtering of one-time codes and reset links (bank for the email
  channel);
- a known-malicious-site list;
- memory in the "download your data" export;
- purchases and single-use cards (not a product direction).

**Not adopted:** moving the agent into the cell. It is the cleaner placement,
but it re-architects the harness, and adopts 1 and 3 bound what an injected
agent can do: it cannot approve its own actions, and it cannot reach anything
the exit does not allow.
