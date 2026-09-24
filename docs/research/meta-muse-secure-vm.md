# Meta Muse Secure VM — runtime cell + Sentinel — assessment

**Sources:** David Singleton's post ([x.com/dps/status/2103161493722419334](https://x.com/dps/status/2103161493722419334),
2026-09-24) and the security write-up it links,
[security.muse.ai](https://security.muse.ai) → Meta's
["Security and safety for AI agents: our approach with Muse"](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse).
Muse gives every user a cloud VM ("Muse Secure VM") that they share with their
agent. Assessed 2026-09-24.

**⚠️ Source limits.** The blog post was read through a summarising fetch, so the
quotes below are the summary's. Nothing was run. Every mechanism is Meta's own
description of its own system: external audits and source availability are
promised "later in 2026", not delivered. Treat each claim as a design statement,
not a verified property. The Prax side of the comparison *was* checked against
the code, with file references.

**Verdict: document + adopt three things.** Prax already has the same shape in
two places: keyless model credentials (the secrets proxy is credential
surrogation) and a separate execution cell (the sandbox container, rootless in
production). Muse is ahead in one structural way: **every sensitive action and
every byte of egress is decided by a rules-based authority that runs outside
the cell the agent lives in.** Prax's governance runs *inside the same process*
as the model loop, and nothing governs the sandbox's network egress at all.

---

## What Muse describes

- **Runtime cell.** The agent itself runs in a `systemd-nspawn` container with a
  full Debian image. Root inside maps to an unprivileged host user; `io_uring`
  is blocked; `CAP_SYS_PTRACE` and `CAP_NET_ADMIN` are dropped. Activity inside
  is "unfettered", and the user can browse and download every file in it.
- **Sentinel.** A host-side, **rules-based (not an LLM)** process that is "the
  sole permission authority" for connector actions and **all network egress**.
  It decides allow / deny / ask-the-user at L4 (host, IP, port) and at L7
  (method, path, decoded request), and validates DNS against SSRF.
- **Tainted egress.** "Kernel-level data flow tracking": a request from a
  process that has *not* read user data can be auto-allowed under a narrow
  policy; a request from one that *has* needs approval.
- **Credential surrogation for everything.** OAuth tokens and user passwords
  live in `hatch-authd`, in a separate container. The agent holds surrogate
  tokens, and Sentinel swaps in the real secret at the network boundary. Browser
  logins are captured by a custom UI straight into `authd`, so the main agent
  never sees them.
- **Least-privilege IPC.** Unix sockets with `SO_PEERCRED` and peer ACLs.
  Connector workers run outside the cell, each identified by its cgroup with an
  explicit credential allowlist ("a calendar worker cannot ask authd for an
  email credential").
- **Injection defence in depth.** Model training and evals; external data
  labelled untrusted in the harness; an **ensemble of injection classifiers**
  independent of the model; human approval for exfiltration; deterministic
  boundaries "even if Muse is persuaded to behave badly".
- **Browser.** The browser sub-agent sees an accessibility-tree snapshot, not
  raw DOM, and cannot run page JavaScript. Purchases need human approval of the
  exact details every time and use single-use card numbers.
- **Admitted limits.** Prompt injection "remains an open problem"; Meta can
  still access VM data when operationally necessary. A "Confidential VM" meant
  to prevent that is planned, not shipped.

## Prax, mechanism by mechanism (checked 2026-09-24)

| Muse | Prax today | Gap |
|---|---|---|
| Agent runs **inside** the cell; authority lives outside | The **agent runs on the host** (`app.py` under systemd confinement); only tool *execution* happens in the sandbox container | Different trust placement. An injected Prax is a host process holding the governance code that is supposed to restrain it. |
| Sentinel: out-of-process, rules-based, sole egress + connector authority, L4/L7, SSRF DNS | `governed_tool.py` risk tiers and `trifecta.py` run **in-process**, per tool call. SSRF helpers (`validate_url`, `safe_request`) are per-tool. The forward proxy **passes unknown hosts through untouched** (`prax-secrets-proxy/secrets_proxy/mitm_addon.py:109`). Sandbox egress is unrestricted, and the 2026-09 review found the HIGH-risk confirmation gate never fires in the shipped topology (finding 17) | **The largest gap.** Nothing outside the agent process decides what may leave the box. |
| Tainted egress (process-level, kernel-tracked) | Trifecta legs recorded per turn at *tool* granularity; provenance tainting of untrusted tool results (`loop_middleware.py`) | Same idea at the agent layer, but it cannot see inside a single `sandbox_shell` call: a script that reads the workspace and then `curl`s out is one tool call. |
| Surrogates for **all** credentials, user passwords included | Provider keys: yes, the secrets proxy (keyless Prax). User site passwords: **`browser_login` returns the real password into the model's context** (`prax/agent/browser_tools.py:215`, from `sites.yaml`) | Concrete and small to fix: the model should never see a password. |
| Per-worker cgroup credential allowlists, `SO_PEERCRED` IPC | Per-plugin approved secrets enforced in-process (`capabilities.py`); TeamWork↔Prax is HTTP with an opt-in shared key | Partial; same in-process caveat as above. |
| Injection classifier ensemble | Provenance labels only; no classifier | Real gap, but a classifier has false positives and must go through the eval gate. |
| Accessibility tree, no page JS | Prax's browser tools have no JavaScript-evaluation tool (navigate / read_page / click / fill / press / find / screenshot) | Holds on the JS point. The `read_page` format was not compared. |
| File explorer over the cell + "download your agent data" | TeamWork's file browser over a git-backed workspace (with history) | Memory lives in Qdrant/Neo4j and is not browsable as files, and there is no one-click export that includes it. |
| Confidential VM (stop the operator reading your data) | Self-hosted: the user *is* the operator | Not a gap; a structural advantage Muse is still building towards. |

Where Prax is already ahead: open source today (Muse promises source later),
self-hostable, keyless model credentials in production, a git-backed workspace
with history, risk-tiered tool governance with an audit trail, and an eval gate
for behaviour changes.

## Adopt

1. **An out-of-process egress authority ("Sentinel-lite") — the big one.** Make
   the forward proxy the *only* network path out of the sandbox container (an
   internal Docker network whose sole route is the proxy), and give it
   deny-by-default policy with allow / deny / ask at host and HTTP level, plus
   DNS-time SSRF checks. The pieces exist: the mitmproxy forward proxy, rootless
   Docker networking, and TeamWork's approval flow (Buzz adoption) for "ask".
   Must be flag-gated. Must be evaluated rather than assumed: deny-by-default for
   *tool classification* (`UNKNOWN_TOOL_HIGH_RISK`) was rejected on measured
   cost regressions in the 2026-07-08 flag campaign and deleted on 2026-08-07,
   and an egress policy that constantly asks is its own failure mode.
2. **Password surrogation for browser logins — small, do first.** Replace
   `browser_login` returning the password with a tool that fills a stored secret
   into a selector *server-side*, so the value never enters the model's context
   or the trace. Same principle as keyless Prax, extended from provider keys to
   the user's own credentials.
3. **Process-level taint for sandbox egress — research, depends on (1).** Once
   the proxy is the only exit, it can tell apart requests from the sandbox
   container and requests from Prax, and treat sandbox egress after
   workspace-reading calls as the private-data leg. Muse does this with kernel
   tracking; an honest first version is coarser (per-container, per-turn).

**Document, don't adopt yet:** the injection-classifier ensemble (park behind
the eval gate; false positives are a product cost); "download your agent data"
including the memory stores (worth doing, small, not security); and single-use
cards / purchase flows (not a Prax product direction).

**Not adopted:** moving the agent itself into the cell. It is the cleaner trust
placement, but it is a re-architecture of the harness. Adopting (1) gets most of
the benefit, because what an injected agent can *do* is bounded by the exit it
must pass through.
