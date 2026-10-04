# DeepSeek Harness (`dsh`)

**Verdict (re-assessed from the code, 2026-10-01): document + adopt two ideas; bank one; don't adopt the harness.** This replaces a September README-only note, and corrects it (below).
DeepSeek Harness is a peer, not a model for Prax's trust stance. Its own safety
notice says it "has not undergone a security audit and must not be treated as
secure", and network access sits outside its sandbox entirely. Three things are
worth taking:

1. **Report enforcement as `full` or `partial`, and check it from inside.**
   Every dsh sandbox backend says whether it can actually enforce what it
   promises (an older kernel's Landlock cannot govern everything). Prax's
   containment (the loopback-only drop-in, Docker taken away, the proxy as the
   only way out) is installed by hand, and nothing in Prax checks it is
   actually active. **Adopt:** a startup self-check that tests each boundary
   from inside the process and reports it.
2. **Keep credentials out of child environments: Prax already does, by
   design; one gap is coming.** dsh drops `*KEY*`/`*SECRET*`/`*TOKEN*`/`*PASSWORD*`
   from child environments. Prax never puts keys there in the first place: no
   `EnvironmentFile`, pydantic reads `.env` without exporting it, and
   `_export_proxy_env_from_dotenv` (`prax/settings.py`) exports only an
   allowlist of proxy and CA variables. *(Correction, 2026-10-04: not so in
   practice. Flask's `app.run()` loads the whole `.env` into the environment
   unless told not to, so every key was there and children inherited it.
   Fixed: Flask's loading is off and `_export_dotenv_config` exports `.env`
   minus credentials.)* In keyless mode the provider keys are
   not in Prax at all. **The gap:** `HTTPS_PROXY` is on that allowlist. Once
   the forward-proxy token is enabled (prax-secrets-proxy #5), it carries
   `http://prax:<token>@…`, and all 69 host subprocess calls would inherit
   Prax's proxy credential and, with per-program rules (#6), Prax's identity.
   **Adopt:** strip the userinfo from the proxy variables for child processes,
   and give a child that needs egress its own identity.
3. **A reminder before the hard stop** (bank). dsh nudges the model at 3, 5 and
   8 identical calls but never blocks. Prax now blocks (turn budgets and spoke
   limits, #243) without nudging first. A reminder would let a turn recover
   before it is cut off.

## Correction to the earlier note (2026-09)

This page replaces a README-only note written in September (at about 36.8k
stars; see git history for #226). Its verdict was "document-don't-adopt: no
governance layer", based on a README that says nothing about permissions,
sandboxing, approvals or audit. **Reading the code shows that was partly
wrong.** `dsh` has:
- approval prompts that deny when nobody answers;
- deny-only "monotonic" guards in the tool pipeline;
- a same-host process sandbox (bubblewrap, Landlock or seatbelt);
- a frank safety notice.

What it lacks is still real: network policy is "outside this vocabulary";
plugins run with whatever access the process has; there is no audit trail of
the kind governance needs; and by its own notice it is unaudited.

So the "governance gap" observation stands only in its narrower form:
- **Egress control** and **out-of-process enforcement** are what other harnesses
  leave out.
- **Approvals and a sandbox** are not; they are table stakes now, and `dsh` has
  them.

The earlier note's "watch whether it adds a governance layer" is answered:
it already had part of one. The rule the earlier note itself states,
"absence of evidence in a preview README is not proof of absence in the
codebase", is the lesson.

Still valid from the earlier note:
- **A vendor harness.** DeepSeek supplies a model Prax runs (the `low`/`base`
  tier moved to `deepseek-v4-flash` on 2026-08-09) and a harness to run it in.
  In the [Niklaus comparison](crush-charm-coding-agent.md), vendor harnesses
  dropped on small models.
- **Cordis stays parked.** The paper now has an arXiv id (2608.25512) but is
  unread; don't summarise it from its title.
- **`dsh` as a harness-lift target stays parked**: a developer preview is a
  moving target.

Source: [deepseek.com/en/harness](https://www.deepseek.com/en/harness/) (behind
an AWS WAF challenge, so not readable without a browser) and the repository
[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
(MIT, TypeScript/Node, about 241k stars, commit `639ed01` of 2026-09-29), which
is what this note is based on. Launched 2026-08-13 alongside DeepSeek-V4-Pro;
developer preview with "compatibility-breaking changes". Press: [VentureBeat](https://venturebeat.com/technology/deepseek-harness-launches-as-open-source-rival-to-claude-code-alongside-v4-pro-on-api-with-higher-prices),
[The New Stack](https://thenewstack.io/deepseek-harness-open-source-plugins/).
`BENCHMARK.md` gives no scores, only how to run the SDK.

## What it is

An agent harness in which "everything is a plugin": models, tools, skills,
sessions, sandboxes, filesystems, loops, orchestration and the UI. It is built
on the Cordis framework ([arXiv 2608.25512](https://arxiv.org/abs/2608.25512)),
vendored and republished under `@deepseek-ai`. It runs as `npx @deepseek-ai/dsh
web` (a local web UI on `127.0.0.1:3080`), a desktop app, or through a Python
SDK. About 60 package families cover subagents (in-process, forked, or
out-of-process **Claude Code, Codex and ACP** agents), MCP, LSP, browser and
computer use, scheduling, webhooks, compaction, SSH, credentials and
programmatic tool calling (PTC: a `run_code` tool whose sub-calls each pass the
full tool pipeline).

**Tool pipeline.**
1. Every call is logged before it runs.
2. A pre-execute waterfall runs hooks, permission and sandbox.
3. **Monotonic guards** follow; they can only deny or abstain.
4. An approval prompt that is absent or can't be answered is a denial.
5. An around-dispatch layer handles timeouts and retries.
6. A post-execute layer can block or replace a result.
7. The frozen outcome is recorded as the single model-facing result.

**Sandbox.** It confines processes on the same host: bubblewrap or Landlock on
Linux, seatbelt on macOS, ACLs on Windows. The modes are Codex's
(`read-only`, `workspace-write`, `danger-full-access`) and cover file effects
only: "Network and process visibility are outside this vocabulary." An escalated
retry is a new call with a wider policy, and needs approval.

## Against Prax

| dsh | Prax |
|---|---|
| Monotonic guards that can only deny, run after the hooks and permission layer | **The same idea** as hard floors (#247), enforced after earned trust, auto-approve and every other risk-lowering rule |
| No answer to an approval means deny | **The same** (fail-closed approvals; parked approvals, #248, for unattended runs) |
| Sandboxes report `full` / `partial` enforcement | **Missing.** The systemd drop-ins and the proxy route are opt-in and installed by hand; Prax does not check them from inside |
| Child processes get a scrubbed environment | **Keys never enter Prax's environment** (only a proxy/CA allowlist is exported; *corrected 2026-10-04: Flask's `app.run()` exported the whole `.env` until then*). Gap: `HTTPS_PROXY` will carry the proxy token once #5 is enabled, and children inherit it |
| Repeated-call reminders at 3/5/8, advisory only | Hard limits (spoke-call limit, failure limit, cost and time budgets, #240/#243), no reminder first |
| No network policy in the sandbox | Egress decided per host, method and path, out of process (secrets-proxy egress policy, sandbox egress gate) |
| Keys held by the harness | Keyless Prax: real keys live only in the secrets proxy |
| Plugins load with whatever access the process has (the safety notice says so) | Plugin trust tiers, `permissions.md` ceilings, the capability gateway |
| Claude Code / Codex as subagents | Removed from the sandbox image on purpose (2026-07-20) |
| "Report orthogonal outcomes independently" (a run can time out *and* exit 0) | Worth a check of `run_command`-style results; not audited here |

## Adopt

**Enforcement self-check.** At startup, and in `make status`, Prax tests its
own boundaries from inside and reports each as enforced, partial or absent:

- a TCP connect to a TEST-NET address (`192.0.2.1`, which never routes) must
  fail when the loopback-only drop-in is meant to be active;
- the Docker socket must be unreachable when `50-no-docker.conf` is meant to be
  active;
- the outbound proxy must actually be in use when keyless mode is configured.

A deployment whose docs promise containment would then show it, and a missed
`daemon-reload` would show up as `absent` instead of passing silently. This
addresses a root cause from the September suite review: docs asserting
guarantees the code does not provide. The behaviour change sits behind a flag;
the report itself is read-only.

**Proxy credential out of child environments.** Before enabling the forward-proxy
token, make the 69 host subprocess calls go through one helper that removes the
userinfo from `HTTP(S)_PROXY`/`ALL_PROXY`. A child that needs egress then gets its
own per-program identity. The `credential_registry` drift-guard pattern can make
a bare `subprocess` call fail CI.

## Bank

- **A reminder before the hard stop**: at half of `SPOKE_CALL_LIMIT`, tell the
  model what it has repeated and ask it to change approach. Advisory only; the
  hard limits stay.
- **The defensive-patterns page** (`docs/defensive-patterns.md` in their repo) is
  a good model for ours: each rule is a defect that shipped, stated as the rule
  that stops it recurring.

## Don't adopt

- **The harness.** It has no egress control, no audit trail of the kind
  governance needs, and full-access plugins. A peer to compare against, not a
  base.
- **Rebuilding Prax as "everything is a plugin".** Prax's seams (spokes, the
  plugin gateway, `build_agent_loop`) already sit where the trust boundaries
  are. Making every layer swappable also makes the governance layer swappable.
