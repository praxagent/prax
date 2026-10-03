# OpenWorker (Andrew Ng) — a governance-first desktop agent

**Verdict: document + adopt two patterns; the product itself is a peer, not a
replacement.** Credit: [OpenWorker](https://github.com/andrewyng/openworker),
Andrew Ng and contributors (MIT, open beta, ~18.4k stars, 556 commits at the
time of reading). Of everything we've read, it is the closest to Prax's own
stance — governance built in rather than bolted on, bring your own model, the
agent loop and keys outside the sandbox wall — so most of it confirms rather
than teaches. Two patterns are better than what Prax has:

1. **Hard floors.** A declared set of operations that are human-only *always* —
   no trust score, auto-approve rule or setting may lower them. Prax has no
   such set, and its earned-trust rule can lower two login steps today.
2. **Park, don't drop.** An unattended run that needs approval parks the
   request in an inbox and resumes when someone decides. Prax fails closed
   (right) but throws the work away (wasteful).

## What it is

A desktop agent that aims to deliver "finished work" rather than chat: security
code reviews, cloud audits, incident triage, everyday tasks. A native shell and
React GUI (Tauri), a local Python agent server built on
[aisuite](https://github.com/andrewyng/aisuite) (Ng's provider-abstraction
library), and 25+ connectors — GitHub, Slack, Jira, Notion, Linear, HubSpot,
Outlook, monday.com, Gmail, Google Calendar — plus the terminal and local
files. Models from OpenAI, Anthropic, Gemini, DeepSeek, Qwen, Mistral, Grok,
Ollama and others. Agents are "specialist coworkers" shipped with their tools,
working style and check-ins; the security coworkers ship first, and they
combine "deterministic scanners (like semgrep) plus model reasoning".

Governance comes in four tiers:

1. **Hard floors** — dangerous operations are human-only, always.
2. **An earned-autonomy ladder** — approval-gated by default, with graduated
   rules for what may run unasked.
3. **Audit trails** recording every tool call with its approval provenance:
   user-approved, auto-approved or denied.
4. **Sandbox isolation** — commands run in NVIDIA OpenShell on Linux, the macOS
   sandbox, or a hidden Windows account, away from keys and user files: "The
   agent loop, the model keys and the connectors stay outside the wall."

"Before anything consequential … it checks in and you approve or redirect."
Unattended runs "never self-approve; requests park in an inbox".

## Against Prax

| OpenWorker | Prax |
|---|---|
| Hard floors: human-only, always | **Gap.** HIGH-risk tools need confirmation, but there is no declared floor. `earned_trust.py` can downgrade `browser_click`, `browser_fill`, `browser_request_login` and `browser_finish_login` from HIGH to MEDIUM after 50 observations at ≥90% success — and that success rate is the tools' own report, which the September LeetCode incident showed can be false |
| Earned-autonomy ladder | **Have it**, narrower: `earned_trust.py` (recursion limits and the downgrade above) |
| Approval provenance on every tool call | **Partly.** Out-of-band approvals record who decided in TeamWork; the per-call audit entry doesn't say whether the call ran user-approved, auto-approved (earned trust, smart auto-approve, a timed grant) or unconfirmed |
| Unattended runs park approvals in an inbox | **Gap.** Scheduled and task-runner turns wait `APPROVAL_WAIT_SECONDS` and are then refused. Fail-closed is right; losing the work is not |
| Agent loop, keys and connectors outside the sandbox | **Have it**: Prax runs outside prax-sandbox, keys live in the secrets proxy |
| Deterministic scanners + model reasoning | **Have the principle** ([neurosymbolic lens](neurosymbolic-lens.md): symbols as checkers). No security-review coworker; semgrep in the sandbox would be the cheap version |
| BYO model through a provider abstraction | **Have it** (`llm_factory`, OpenRouter, cross-provider failover) |
| Desktop app, local-first | Different product: Prax is server-side, multi-channel (TeamWork, Discord, SMS) |

## Adopt

**1. Hard floors, declared and enforced last.** A named set of operations —
logging in, entering credentials, paying, sending a message or email *as the
user*, deleting user data, changing Prax's own governance or policy — checked
*after* every rule that could lower risk (earned trust, auto-approve, timed
grants), so nothing can lower them. The immediate fix inside it: the two login
steps come off the earned-trust downgrade list. Trust earned by clicking
reliably says nothing about whether to log in unasked.

**2. Parked approvals for unattended runs.** When a scheduled or task-runner
turn needs approval, create the TeamWork request, save the turn's state (the
durable-checkpoint machinery exists behind `CHECKPOINT_BACKEND`), end the turn
with "waiting for your approval", and resume it when the person decides. A
request left undecided past a deadline expires, and the run reports that it
didn't happen. Never self-approve.

**3. (Small) Approval provenance on every audit entry** — `user-approved`,
`auto-approved (rule)` or `no approval needed` — so "why did this run?" has an
answer per call.

## Don't adopt

- **The product.** A desktop, single-user coworker; Prax is a multi-channel
  server agent with a shared workspace. Different shapes, same values.
- **aisuite.** Prax's `llm_factory` already abstracts providers and adds
  failover and the keyless proxy path; switching gains nothing.
- **The connector catalogue** as a goal in itself. Prax's plugin system and MCP
  cover integrations; connectors get added where a user needs them.

## Adopt tracker

| Item | Status |
|---|---|
| Hard floors (declared set, enforced after every risk-lowering rule); take the two login steps off the earned-trust downgrade list now | **queued** — the login part is a real gap today |
| Parked approvals for unattended runs (checkpoint, end with "waiting", resume on decision, expire honestly) | **queued** |
| Approval provenance on every audit entry | **queued**, small |
