# Virtual company: teammates in TeamWork, with Prax as CTO

**Status:** design, not started (2026-10-04). Nothing here is built.
**Repos:** prax (runtime, governance, budgets) and teamwork (display, identity,
sidebar). The TeamWork UI parts of this plan should move into
`teamwork/docs/` once they are built (docs federate by ownership).
**Trigger:** 2026-10-04, after OpenAI shipped ChatGPT dots: TeamWork was
designed for several independent agents with their own personas, working like
coworkers that the user or Prax manages, and that design has gone dormant.

## Recommendation in one paragraph

Build it, in the order below, and start smaller than the vision. **Phase 0**
fixes what is wrong today in both modes: the sidebar offers DMs with Planner,
Researcher, Executor, Auditor and Health Monitor, and every one of them is
answered by Prax. **Phase 1** is one hired teammate. She has a persona and a
job-scoped tool allowlist that the governance layer enforces. She works a
Kanban backlog inside a daily run window and under a daily spend cap, and she
posts progress in her own channel. It can be tested in one afternoon for a
few dollars. Hiring from a "+" under Direct Messages, Prax proposing hires,
the `#random` chatter, and several teammates come after Phase 1 has run cleanly.
The one rule that must not slip is that a teammate's scope lives in code
Prax runs, outside anything the teammate (or Prax) can write. Writing the
scope in the prompt does not limit anything.

**Naming.** The design calls them **teammates**. "Agent" is overused and cold,
"employee" sounds like HR, and "worker" sounds like a background job.
"Teammate" fits the TeamWork brand: *hire a teammate*, *your teammates*.
Alternatives considered:
- **colleague**: warm and professional, but a little formal for a sidebar label;
- **crew / crewmate**: playful, but collides with the CrewAI brand and reads as
  a game.

Internal parts of Prax (Planner, Researcher, Executor, Auditor, Health
Monitor) are **not** teammates. In this document they are *system roles*.

---

## 1. What ChatGPT dots are (sources, read 2026-10-04)

All four pages return HTTP 403 to plain fetchers, so they were read as
rendered pages in a headless Chromium on the dev sandbox:

- [chatgpt.com/features/dots](https://chatgpt.com/features/dots/), the
  feature page;
- [Introducing dots](https://openai.com/index/introducing-dots/), dated
  2026-09-29;
- [How we build safety, security, and privacy into dots](https://openai.com/index/how-we-build-safety-security-and-privacy-into-dots/),
  linked from the feature page;
- the Help Center articles
  [Getting started with your dot](https://help.openai.com/en/articles/20001530)
  (linked as "eligible markets") and
  [Dots privacy, security, and safety FAQs](https://help.openai.com/en/articles/20001529).

Not read: the dots system card and the
[auto-review docs](https://learn.chatgpt.com/docs/sandboxing/auto-review).

What OpenAI says, quoted:

- **What a dot is.** "Remarkably capable, always-on agents built to handle
  everything—powered by GPT-6 Astra." It "uses its own computer to move your
  projects forward" and "keeps making progress between conversations. It
  brings you work to review and decisions that need your judgment" (feature
  page).
- **Its own machine.** "Dots can do nearly anything using their own cloud
  computer, their own browser, and the apps you've connected. You can open
  your dot's computer at any time to inspect its work." Using your laptop is
  optional and "starts turned off" (intro; help).
- **Apps.** "Through our ecosystem of plugins, they can readily connect to
  over 4,000 apps" (intro).
- **Channels.** "Message or call your dot in ChatGPT on web, mobile, and
  desktop" (feature page). "You can also message your dot in Slack and
  Teams, with texting coming soon" (intro). Texting is "a limited beta" for
  US Pro users, and "your dot cannot initiate calls to you at launch"
  (help). "You can also set up a separate Slack account for your dot,
  giving it its own identity" (FAQ).
- **Identity.** "You can give your dot a name during setup. Its default
  handle is @yourname-dot … You can choose from the available characters or
  select a pet for your dot" (help).
- **How many.** "Today, you can start with your primary dot … Over time, we
  envision teams of dots working together on your behalf." "In the future,
  you'll be able to add more dots" (intro; help).
- **Specialist dots** (enterprise pilot, not general availability). "Your
  company sets up each dot with its own identity, credentials, and access to
  the systems it needs … Our engineering teams will work directly with
  organizations to define each dot's responsibilities, the tools it can use,
  and how people review and approve its work" (intro).
- **Memory.** "Your dot receives memories from ChatGPT and can create its
  own memories, including from connected apps." "You currently cannot view,
  delete or directly modify individual dot memories" (help; FAQ).
- **Scheduling.** "Ask your dot to set a reminder or run a recurring check."
  The profile shows "In progress, Scheduled, and Completed" (help).
- **Proactive research.** Background tasks that "use read-only tools to
  gather information … We enforce these limits in code: the research tasks
  cannot directly send messages to other people, change content in
  connected apps, or control a browser or desktop" (safety blog).
- **Control.**
  - "Custom Rules let you allow specific actions, require approval, or block
    them." The choices are "Take action without asking / Take action if
    pre-approved / Ask before taking action / Hand off to you" (intro; help).
  - "Some actions require your confirmation each time, including permanently
    deleting data, installing or running software from an unrecognized
    source, or granting new security-sensitive access … changing a password
    or transferring money … must hand those sensitive steps back to you"
    (safety blog).
  - "Dots can help you write Custom Rules, but they need your approval to
    change them" (safety blog).
- **Auto-review.** "Before dots take actions such as sending emails or
  changing files, a separate safety system called Auto-review checks the
  planned steps against your instructions, Custom Rules, and safety
  requirements … We keep the controls that enforce Auto-review outside the
  environments dots can change" (safety blog).
- **Delegation does not widen authority.** "That authorization stays tied to
  your instructions for the task; continuing later or delegating work does
  not expand it" (safety blog).
- **Secure sign-in.** "The service supplies the password for sign-in without
  passing it to the model" (safety blog).
- **Oversight.** "Activity View in the desktop app … shows ongoing and
  delegated tasks and their status … ask a dot to stop" (safety blog). Pause
  and Reset are in the profile's ••• menu; "Reset deletes your dot,
  including its conversations, saved memories, and scheduled tasks" (help).
- **Price.**
  - "Your first dot is included in your Pro or Business Premium plan at no
    extra cost … Your plan also includes an allowance for deeper work, with
    extended limits for the first month after launch."
  - Later, "scale the output of each dot by either increasing its speed or
    the total amount of work it can take on per month."
  - "Conversations with your dot don't count toward your ChatGPT usage
    limits" (intro).

**Unclear or unverified:**

- What the "allowance for deeper work" is in numbers.
- How "learn from feedback over time" works.
- Whether any user can run more than one dot today. The text says no.
- How specialist dots are priced or provisioned beyond a pilot.
- How Auto-review is built. The system card was not read.
- The Slack and Teams screenshots on the feature page show a dot ("Alfred")
  posting unprompted in a channel. They are marketing mock-ups, not
  documented behaviour.
- Every capability claim above is OpenAI's own description. None was
  exercised.

## 2. Dots parity

How to read the "Our status" column:

- **have**: shipped;
- **partial**: some of it exists, or it exists behind an off-by-default flag;
- **missing**: not built;
- **ahead**: we do more than dots;
- **different**: not comparable.

All paths are in this repo unless prefixed `teamwork/`.

| Dots capability (source) | Our status | Evidence |
|---|---|---|
| Always-on; "keeps making progress between conversations" (feature page, help) | partial | The task runner polls the Kanban every 5 min for cards assigned to `prax` (`prax/services/task_runner_service.py:130-177,388-405`). It is off by default (`prax/settings.py:1389`) but on in the local launch (`Makefile:741`). The scheduler runs cron prompts (`prax/services/scheduler_service.py:280-334`). Work starts only from an assignment or a cron, never from noticing something: [IDEAS_BACKLOG #23](../IDEAS_BACKLOG.md) P1–P3 not started. |
| Own cloud computer and browser; open it to inspect (intro) | have (shared) | prax-sandbox provides Chromium, a noVNC desktop and code-server; TeamWork has browser and desktop panels. There is one container per user, not per agent (`docker-compose.yml:86`). |
| Optionally connect the user's own computer (intro, help) | missing | — |
| 4,000+ apps via plugins (intro) | partial | Plugin system, `prax-plugins`, and an inbound MCP server (`prax/mcp/`). The outbound MCP client is backlog #27, not started. |
| Web, mobile, Slack, Teams, voice; texting in beta (intro, help) | partial; ahead on SMS | TeamWork web (mobile-friendly), Discord, Twilio SMS and inbound voice (`prax/services/sms_service.py`, `prax/services/voice_service.py`). No Slack or Teams. |
| Name, avatar, character or pet, handle (help) | partial (dormant) | TeamWork's `Agent` row has `persona` JSON, `profile_image(_type)` and `soul_prompt`/`skills_prompt` (`teamwork/src/teamwork/models/agent.py:30-40`); `ProfileModal` renders a persona (`teamwork/frontend/src/components/profiles/ProfileModal.tsx:309-395`). Nothing creates a persona today (§3.1). |
| Memory from ChatGPT plus its own (help, FAQ) | have per user; missing per agent | Qdrant and Neo4j keyed by `user_id` (`prax/services/memory/vector_store.py:93`, `prax/services/memory/graph_store.py:120`). No per-agent memory. |
| "Give it a responsibility to own"; brings work back for review (feature page) | partial | A Kanban card assigned to `prax` becomes a task-runner turn, a comment, and a move to done (`task_runner_service.py:321-340`). The only assignee that runs is Prax. |
| Proactive research: background, read-only, limits enforced in code (safety blog, FAQ) | missing | No ambient loop (#23). §5.2 adopts the read-only-in-code pattern for teammates. |
| Scheduled tasks; In progress / Scheduled / Completed (help) | have | Per-user `schedules.yaml`, reminders, missed-fire catch-up (`scheduler_service.py:39-84,396-489`); Library Kanban. |
| Activity View: follow, redirect, stop (intro, safety) | have; stop is behind a flag | TeamWork live output and execution graph. Stopping a running turn needs `TURN_STOP_ENABLED`, default off (`prax/settings.py:1127`). |
| Pause; Reset deletes context and schedules (help) | partial | `task_runner_pause/resume` (`prax/agent/task_runner_tools.py:18-60`); TeamWork project pause (`teamwork/src/teamwork/routers/projects.py:290-313`). No per-agent pause or reset. |
| Custom Rules: without asking / if pre-approved / ask / hand off (help, safety) | partial | Risk tiers and the HIGH gate (`prax/agent/governed_tool.py:556-611`); time-boxed `ApprovalGrant`s (`teamwork/src/teamwork/models/approval.py:227-257`); "the user's own message names the action and target" plays the role of pre-approved (`prax/agent/hard_floors.py:133-160`). No rule editor. |
| Mandatory hand-offs and per-call confirmation that rules cannot override (safety, FAQ) | have (flag) | Hard floors for credentials, code that runs with Prax's authority, and money (`hard_floors.py:42-64`) are behind `HARD_FLOORS_ENABLED`, default off (`settings.py:1149`). Public exposure is always on (`hard_floors.py:72-78`). Floors can be added, never removed (`hard_floors.py:98-104`). |
| Auto-review: a separate system checks actions against instructions, outside the dot's reach (intro, safety) | partial | Governance is an in-process wrapper (`governed_tool.py:339-778`). The trifecta guard is off by default (`settings.py:878`). Approvals live in TeamWork (`prax/services/approval_service.py:43-98`). Nothing checks an action against the *instruction*. "Governance runs in-process" is a known gap ([Muse note](../research/meta-muse-secure-vm.md)). |
| Safety monitoring can pause work (safety) | partial | Loop detection, turn budgets, and the Health Monitor, which only writes the activity log (`prax/agent/health_monitor.py:410-414`). |
| Secure sign-in: the password never enters the model (safety, FAQ) | partial (flag) | The `browser_request_login` VNC path. `BROWSER_SECRETS_OUT_OF_CONTEXT` defaults off (`settings.py:339-340`). |
| Delegating or continuing later does not widen authority (safety) | partial | Governance state is fresh per turn (`governed_tool.py:76-167`). Spokes record but enforce only with `SPOKE_GOVERNANCE_ENABLED`, default off (`settings.py:1113`). There is no per-agent scope to keep from widening: this design adds it. |
| One primary dot now; "teams of dots" are a vision (intro, help) | ahead in the substrate; missing in the runtime | TeamWork has per-agent credentials, capabilities, Ed25519 signing, a hash-chained event log, approvals, channel membership and agent↔agent DMs (`teamwork/src/teamwork/agent_auth.py`, `agent_signing.py`, `services/event_log.py`, `services/membership.py`). Nothing runs a second agent. |
| Specialist dots: own identity, credentials, defined tools and review (intro; enterprise pilot) | partial | TeamWork has the identity half. The job-scoped tool profile in Prax is missing: the only per-identity allowlist is MCP's (`prax/mcp/server.py:134-153`). |
| Plan allowance; later "scale … the total amount of work it can take on per month" (intro) | missing | Only a per-turn USD/seconds budget, off by default (`settings.py:1140-1141`, enforced at `governed_tool.py:872-921`). No daily, per-user or per-agent cap. |
| GPT-6 Astra; encryption and training controls | different | Model-agnostic tiers and failover; self-hosted, so data stays on the owner's machine. |

**Verdict: partial parity.**

- **Ahead:** the multi-agent identity substrate, self-hosting, and SMS and
  voice.
- **Behind:**
  - always-on initiative;
  - a job-scoped permission profile inside the agent runtime;
  - spend allowances;
  - connecting the user's own computer;
  - Slack and Teams.

Dots ships **one** dot per user. Teams of dots are a stated vision, and
specialist dots with their own identity are an enterprise pilot. So a
virtual company is not a catch-up: it is a step past what dots offers an
individual today, on a substrate TeamWork already has. The gap to close is in
Prax's runtime.

## 3. What exists today

### 3.1 The original coworker onboarding: present, dormant, backend deleted

| Piece | State | Evidence |
|---|---|---|
| `/new` wizard, `TeamTypeStep` | wired | `teamwork/frontend/src/App.tsx:115`; only the "Just Prax" card works (`TeamTypeStep.tsx:38-53` → `POST /api/projects/blank`) |
| Startup and Personal Coaching cards | dormant (flag) | `const TEAM_TYPES_ENABLED = false` (`TeamTypeStep.tsx:21`), set by commit `a210c23` on 2026-07-24 |
| Description, questions, team preview (name, personality, avatar per member), config steps | dormant and unreachable | `OnboardingWizard.tsx:23,536-599`; `TeamPreview.tsx:336-370` |
| Persona-generation backend (`onboarding.py`, `personality_generator.py`, soul and skills templates, image generator) | **deleted** 2026-03-26 (`0804020`); recoverable with `git show 0804020^:<path>` | The hooks still call `/onboarding/*` (`frontend/src/hooks/useApi.ts:571-668`); no router is mounted (`teamwork/src/teamwork/main.py:173-229`) |
| Agent persona columns | live | `name`, `role`, `specialization`, `team`, `soul_prompt`, `skills_prompt`, `profile_image(_type)`, `persona` JSON (`models/agent.py:30-40`); shape in `frontend/src/types/index.ts:67-97` |
| Persona display and soul/skills editor | live | `ProfileModal.tsx:75-98,309-395`; `PUT /api/agents/{id}/prompts` (`routers/agents.py:523`) |
| Setting a persona from Prax | missing | External `POST /projects/{p}/agents` takes only name, role, soul, skills and `avatar_url`, and never stores `avatar_url` (`routers/external.py:52-58,430-464`). There is no update endpoint, and the create always inserts. |

The comment that disabled the cards (`TeamTypeStep.tsx:9-20`) is this
design's brief: the cards "become live again once **Prax** can compose and
drive a team over /api/external. TeamWork has no LLM and should not grow one;
it should render a team the agent builds." The term "backstory" never existed
in the repo. The persona shape has traits, communication style, strengths,
hobbies, pet and work style.

### 3.2 TeamWork identity and governance (the "Buzz adoption", July 2026)

- **One credential is one identity.**
  - `X-API-Key` maps to an `AgentClient` with `agent_id`, `project_id`,
    `spaces`, `allow` and `gated` (`agent_auth.py:80-114`).
  - Tokens are hashed and compared in constant time.
  - The identity comes from the token. A body `agent_id` can narrow it but
    never widen it (`external.py:250-263`, `agent_auth.py:150-159`).
  - The legacy `EXTERNAL_API_KEY` is unbound and can act as anyone
    (`agent_auth.py:237-241`).
- **Capabilities.**
  - The set is `project.read|write`, `agent.write`, `message.post|delete|bulk`,
    `presence`, `task.write` and `activity.write` (`agent_auth.py:60-74`),
    plus `approval.decide`. The `*` wildcard never grants that last one.
  - Capabilities are granted only by editing `~/.teamwork/agent-clients.json`.
    There is no minting API.
- **Signing.** Ed25519 request signing with nonces and a 300 s skew window
  (`external.py:149-192`, `agent_signing.py`).
- **Approvals.**
  - Each approval is bound to `sha256(capability, project, payload)`, is
    single-use, and expires after 1 h (`models/approval.py:140-222`).
  - A person decides through `/api/approvals/{id}/decide` (`routers/approvals.py:45-75,108`).
  - The UI polls every 3 s; nothing pushes (`components/common/ApprovalPrompt.tsx:23,44`).
- **Event log.** Hash-chained and append-only (`services/event_log.py:26-96`).
  It records messages, channels, DMs, membership and approvals. It does **not**
  record task, agent or activity writes.
- **Membership and DMs.** Channel membership checks are off by default
  (`services/membership.py:95-111`). Agent↔agent DMs exist (`membership.py:119-148`).
- **Foreign agents.** `agent_adapter.py` is a library that nothing runs.

**Gaps that matter here:**

- `AgentClient.scoped_to` (project scope) is never enforced outside tests.
- `project.read` is never checked.
- Task and agent-status writes skip the identity check (`external.py:467-496,762-857`).
- There are no rate limits on the external API.

### 3.3 Channels, DMs and the webhook

- **Seeding.**
  - An external project is seeded with `general`, `engineering`, `research`,
    `discord` and `sms` (`external.py:329-335`).
  - Prax adds `browser` and `content` at startup
    (`prax/services/teamwork_channels.py:42-50`,
    `prax/services/teamwork_hooks.py:131-149`).
  - **`#random` exists only** in the lazy default for projects that have no
    public channels (`teamwork/src/teamwork/routers/channels.py:113-116`), so
    Prax workspaces never get it.
- **DMs.** The sidebar lists **every agent in the project** as a DM
  (`teamwork/frontend/src/components/chat/ChannelSidebar.tsx:286-301`). That
  section has no "+" button; the Channels section does (`:242`).
- **Webhook.**
  - TeamWork sends one only for human messages in external projects
    (`teamwork/src/teamwork/routers/messages.py:704-722`).
  - Messages posted by agents trigger nothing, so agents cannot wake each
    other through chat.
- **Unprompted posting.** Agents can post without a prompt through
  `POST /external/projects/{p}/messages` (`external.py:499-588`).

### 3.4 Prax's side

- **Registration** (`app.py:174-228`).
  - Prax creates or reconnects to "{AGENT_NAME}'s Workspace" and registers
    itself as `orchestrator`.
  - It then registers **five system roles**: Planner, Researcher, Executor,
    Auditor and Health Monitor (`teamwork_channels.py:66-72`,
    `teamwork_hooks.py:157-174`).
  - **Why they exist:** `TeamWorkClient` drops status, live-output and
    activity calls for unregistered names, so registering a role switches
    those sinks on (`teamwork_channels.py:13-28`). Spoke roles stay
    unregistered on purpose.
- **What each role does:**
  - Planner posts the plan to #general (`prax/agent/workspace_tools.py:1079-1082`).
  - Researcher posts to #research (`prax/agent/research_agent.py:426-435`).
  - Executor posts `delegate_task` output to #engineering (`prax/agent/subagent.py:246-250`).
  - Auditor posts claim-audit flags to #general (`prax/agent/orchestrator.py:2391-2393`).
  - Health Monitor writes the activity log every 10 turns (`health_monitor.py:410-414`).
  - Governance flips Executor and Auditor to "working" on every hub call
    (`governed_tool.py:646-650`).
- **A DM with any of them reaches Prax, and Prax answers as "Prax".**
  - Any channel id Prax does not know is treated as a DM
    (`prax/blueprints/teamwork_routes.py:2133-2135`).
  - The reply is sent with a hard-coded `agent_name="Prax"` (`:2350-2354`).
  - So the user's DM list shows six "people", and five of them are a different
    label on a Prax conversation.
- **Every TeamWork message is one user.** It resolves to `TEAMWORK_USER_PHONE`
  or `teamwork:default` (`teamwork_routes.py:199-216`).
- **Unprompted posts today.** Scheduler deliveries and parked-approval
  notices go to #general (`scheduler_service.py:258-262`), and Discord and SMS
  are mirrored. No role posts on a timer.
- **The task runner.**
  - It runs one interval job per user, with `max_instances=1`
    (`task_runner_service.py:388-405`).
  - It picks the leftmost non-done Kanban card whose `assignees` contain
    `"prax"` (`:42,130-177`). Assignees are free-form strings
    (`prax/services/library_tasks.py:46`).
  - For each pickup it builds a new `ConversationAgent(tier="medium")` and
    calls `ConversationService.reply` (`:271-273`) with no conversation key,
    so the turn lands in the user's default history.
  - It parks approvals (`:266-279`).
- **Delegation.**
  - There are 15 spokes behind `run_spoke` (`prax/agent/spokes/_runner.py:77-363`).
  - `delegate_task` picks a fixed tool set by category (`subagent.py:264-286`).
  - `delegate_parallel` runs a thread pool with a copied context per worker
    (`subagent.py:379-525`).
  - **No sub-agent can be given a tool subset.** The only filter is the eval
    deny-list ContextVar, applied at every build site
    (`prax/agent/tool_registry.py:25-38`; `_runner.py:213`, `subagent.py:128`,
    `research_agent.py:190`).
- **Governance order** (`governed_tool.py:339-778`):
  1. cancel
  2. turn budget
  3. hard floors
  4. epistemic gate
  5. earned trust
  6. lethal trifecta
  7. tool-call budget
  8. loop detection
  9. HIGH gate with smart auto-approve
  10. semantic entropy

  Spokes record always and enforce only with `SPOKE_GOVERNANCE_ENABLED`.
  Hard floors run before that switch.
- **MCP per-caller identity.**
  - Each token maps to a `user_id` and an `allow` set (`prax/mcp/clients.py`).
  - Tools are filtered when the list is built *and* checked again on every
    call. HIGH is never exposed (`prax/mcp/server.py:134-153,176-180`).
  - This is the pattern the teammate profile copies.
- **Cost.**
  - `TURN_BUDGET_USD`/`TURN_BUDGET_SECONDS` default to 0, meaning off
    (`settings.py:1140-1141`).
  - Spend is priced from the execution graph's token usage, and is a lower
    bound when a model has no rate (`prax/agent/trace.py:158-184`).
  - Over budget, the next calls are refused, and the turn ends after 3 grace
    calls (`governed_tool.py:872-921`).
  - **No daily, per-user or per-agent cap exists.**
- **Models.** `LOW` gpt-5.4-nano, `MEDIUM` gpt-5.4-mini, `HIGH` gpt-5.5
  (`settings.py:145-179`). The orchestrator refuses to default to low because
  nano skips tools (`orchestrator.py:240-250`).

### 3.5 Defects found while inventorying (outside this design's scope, but they bite it)

1. **Parked approvals cannot resume a schedule.**
   `prax/services/parked_approvals.py:149-150` imports
   `scheduler_service._on_schedule_fire`. No such function exists: the real
   one is `_on_fire` (`scheduler_service.py:280`). The `ImportError` is
   caught and logged (`:156-157`), so an approved schedule never re-runs.
   Teammates depend on parked approvals.
2. **In unattended turns, the hard-floor chat fallback trusts card text.**
   - With out-of-band approvals off, a floor is lifted when "the user's own
     message" names the action and target (`governed_tool.py:974`).
   - In a task-runner turn that message is the synthetic prompt, which embeds
     the card's title and description (`orchestrator.py:1131`;
     `task_runner_service.py:207-224`).
   - A card that says "log in to example.com" therefore satisfies the floor,
     whoever wrote the card.
3. **The orchestrator rebuilds its tool list from the global registry every
   turn** (`orchestrator.py:1162`, and on plugin reload `:323`). A teammate
   whose tools were filtered when its agent was built would be silently
   re-widened on its first turn. Scope has to live in the registry and the
   governance wrapper.
4. **The sandbox mounts the user's entire workspace, `.services/` included**
   (`docker-compose.yml:86`). Code a teammate runs in the sandbox can write
   anything stored there. Profiles and the spend ledger must live outside it.
5. **Stale code paths.** The coding-agent channels (#claude-code, #codex,
   #opencode) are still created lazily (`teamwork_hooks.py:193-257`), although
   those CLIs were removed on 2026-07-20.

---

## 4. Two modes

| | **Solo** (default, today) | **Company** (opt-in) |
|---|---|---|
| Switch | nothing to do | `COMPANY_MODE_ENABLED=true` in Prax, plus a person hires a first teammate |
| Who works | Prax and its internal spokes | Prax plus hired teammates, each working alone within a scope |
| Sidebar DMs | Prax only (after Phase 0) | Prax plus teammates, with a "+" to hire |
| Channels | as today | plus each teammate's home channel; `#random` only if turned on |
| Background work | task runner (`prax` cards) and scheduler | plus teammate pickups inside run windows |
| Cost | as today | per-teammate and company daily caps, enforced in code |

Solo mode is left exactly as it is, with one exception: the DM clean-up
(§9.1). The problem applies in solo mode too, because the five role DMs
mislead there as well (§3.4). The clean-up is a display change: no data is
deleted. Whether it ships default-on as a fix or behind a flag is open
question 2.

---

## 5. The teammate model

### 5.1 Persona: who they are (confers no power)

- name, avatar (character or pet, as dots does), and a one-sentence **job**;
- **background**, **skills** and **personality**, stored in TeamWork's
  existing `persona` JSON (traits, communication style, strengths, quirks,
  work style) and rendered by `ProfileModal`;
- a short persona preamble prepended to the teammate's system prompt.

**The persona is description, never permission.** A teammate described as
"a senior sysadmin with root" has exactly the tools its profile lists and no
others. Profile validation never reads persona text.

### 5.2 Capability profile: what they may do (enforced in code)

```yaml
# stored in company.db beside identity.db, never in the workspace (§3.5 item 4)
teammate: ada
job: "Research the Q4 market scan and keep its notebook current"
template: researcher            # starting point; edits are diffs against it
spokes: [knowledge]             # delegate_* tools allowed
tools: [fetch_url_content, library_note_create, library_note_update,
        library_task_comment, progress_read, progress_append]
risk_ceiling: MEDIUM            # anything above it is refused outright
spaces: [q4-market-scan]        # the only Library spaces it may pick up from or write to
home_channel: q4-research
budget: {daily_usd: 1.00, tier: medium, max_tool_calls: 25}
window: {days: Mon-Fri, hours: "13:00-17:00", tz: America/New_York}
created_by: person              # or "prax:approval:<id>"
```

**Where it is enforced.** Never in the prompt. Three places, all in Prax:

1. **At build time.** A `principal_tool_allowlist` ContextVar is applied at
   every tool-list build site, beside `apply_eval_denylist`: the registry
   (`tool_registry.py:57-72`), the spoke runner (`_runner.py:213`), workers
   (`subagent.py:128`) and research (`research_agent.py:190`). Because it sits
   inside `get_registered_tools()`, the per-turn rebuild in §3.5 item 3 cannot
   widen it. It is an **allowlist**: a tool added to Prax later is out until a
   person adds it to a profile.
2. **At call time.** `wrap_with_governance` refuses a tool outside the bound
   principal's profile, at both the hub and the spoke layer. The check sits
   beside the hard floors, before earned trust, auto-approve and the spoke
   enforcement switch. This is the defence-in-depth second check MCP already
   makes (`mcp/server.py:179`).
3. **On risk.** The ceiling uses the tool's *effective* risk: the wrapper's
   `_risk_level` from `@risk_tool`, falling back to the static map. MCP's
   filter reads only the static map (`mcp/server.py:151,179`), so a tool that
   is HIGH only through `@risk_tool` (`request_extended_budget`,
   `browser_fill_login`) reads as MEDIUM there. The profile must not copy
   that gap.

The principal travels in `UserContextSnapshot` (`prax/agent/user_context.py:72-105`),
as a new `principal` field. That snapshot is already captured when a tool is
wrapped and restored on every call, and it crosses into `delegate_parallel`
threads through the copied context. If the captured principal and the live
one disagree, the call is refused.

**Spaces** need argument-level checks: a Library tool's `slug` argument must
be in `spaces`. Phase 1 restricts pickups to the listed spaces and refuses
writes elsewhere. Read-only access to other spaces is open question 10.

**Idle mode (later, from dots).** Between cards, a teammate may do
"proactive research" only through a read-only profile that is enforced in
code: no sinks, no writes, notes to itself only. This is dots' pattern and
the outer loop of backlog #23. Not in Phase 1.

### 5.3 Identity and audit trail

- **In TeamWork,** a teammate is an `Agent` row with a new `kind` column set
  to `teammate` (`system` for internal roles, §9.1), plus its persona.
  - Phase 1 posts the way the role agents post today: Prax's credential names
    the teammate's `agent_id`. Attribution is therefore Prax's assertion.
  - Phase 2 gives each teammate its own **bound credential** (`agent_id` set,
    `project_id` set, `allow = {message.post, presence, task.write, activity.write}`).
    TeamWork's event log then attributes each post by token.
  - Honest limit: Prax holds every teammate's token, so a compromised Prax
    can still speak as any of them. This is the limit already written into
    `teamwork/docs/security/agent-identity.md`. Real separation needs key
    custody outside Prax (Phase 4).
- **In Prax,** every audit entry gets `principal=teammate:<slug>` next to
  `component`, written to the same git-backed `trace.log`. Teammate turns use
  their own conversation key, `ConversationService.scoped_conversation_key("teammate", slug)`,
  so their history is separate from Prax's and from the user's.
- **Per-component learning state** (earned trust, the tier bandit,
  metacognitive profiles; §3.4) must not be updated by teammate turns. A
  teammate's mistakes must not lower Prax's trust, and a teammate's successes
  must not earn it HIGH→MEDIUM downgrades. Teammate turns are excluded from
  that learning.

### 5.4 Backlog and schedule

- **Backlog.** Library Kanban cards whose `assignees` contain the teammate's
  slug, in the teammate's spaces. Today's runner logic is reused with the
  marker generalised from `"prax"`. Progress goes to card comments as
  `actor=<slug>`, which the Library already supports.
- **Pickup rule** (security, §6). A teammate picks up a card only when **a
  person or Prax** assigned it. A card a teammate created or reassigned is
  never auto-picked by anyone else. Work flows down, from wider scope to
  narrower, never up.
- **Run window.** Pickups start only inside the window. At the end of the
  window, the in-flight turn is asked to wrap up. A turn still running 10
  minutes later is stopped through the turn registry. Outside the window the
  teammate shows as offline. "Only when I ask" is a valid window: DMs and
  explicit hand-offs only, no polling.

### 5.5 Who creates and manages teammates

| Action | Person (TeamWork session) | Prax as CTO |
|---|---|---|
| Hire | yes ("+" under Direct Messages, §9.2) | **proposes** with `teammate_propose_hire`. The person sees the full profile, budget and window in TeamWork's approval dialog and decides. Hiring without asking from person-approved templates within the company budget is open question 3. |
| Assign work | yes (Kanban) | yes, by creating or assigning cards in the teammate's spaces (narrowing only) |
| Narrow scope, lower budget, shorten window, pause | yes | yes. Narrowing is always safe. |
| Widen scope, raise ceiling or budget | yes, and each widening is a hard floor: a person's decision on that exact diff | **never**. It can only propose. |
| Resume after the kill switch | yes | **never** |
| Fire (archive) | yes | proposes |

**What Prax may never grant, even with a template:**

- a tool outside Prax's own effective tool set, so no scope wider than its own;
- any HIGH-risk or hard-floor tool: credentials and logins, plugin
  install/activate/write, `self_improve_deploy`, `gpu_power_on`, public
  exposure;
- the `teammate_*` management tools themselves, so teammates cannot hire;
- `approval.decide` in TeamWork;
- a daily budget above what remains of the company cap, or a window outside
  company hours;
- a profile that holds all three trifecta legs (untrusted input, private
  data, an external sink), computed with `trifecta.legs_for` over every tool
  in the profile.

A **person** may build a profile that includes HIGH tools, but every HIGH
call still goes to that person in TeamWork. Teammate turns get no smart
auto-approve, no earned trust and no chat fallback.

The Prax management tools live in the `tasks` spoke, not the orchestrator,
to keep the hub under its tool budget: `team_status`, `teammate_assign`,
`teammate_pause` and `teammate_propose_hire`.

---

## 6. Security

- **Least privilege per job.** Each profile is an explicit allowlist (§5.2).
  Templates start narrow: a researcher gets web read plus its own spaces. Read
  access to the whole workspace, memory or traces is an explicit grant,
  because auto-injected context (memory, `agent_plan`, workspace context) is
  also a read path. Teammate turns get only what the profile names.
- **Credentials are never shared.**
  - Teammates never get the credential tools; those are hard floors.
  - Phase 2 gives each teammate its own TeamWork credential.
  - No teammate ever sees Prax's TeamWork key, the inbound `PRAX_API_KEY`, or
    any provider key. With the secrets proxy, Prax has no provider key to
    leak.
  - A per-teammate proxy token that the proxy meters is Phase 4.
- **No teammate can widen its own scope.**
  - Profiles live in `company.db` beside `identity.db`, outside the sandbox
    mount (§3.5 item 4).
  - No tool writes profiles. The only writers are the hire/edit route, which
    needs a person's TeamWork session for any widening, and the narrowing
    tools.
  - A teammate's Kanban edits cannot cause wider-scope work (§5.4 pickup
    rule).
  - Teammate-authored text that reaches Prax (card comments, hand-off notes)
    is **untrusted input**, tainted by `loop_middleware`'s provenance, never an
    instruction.
- **Approvals go to the person.**
  - Company mode refuses to start a teammate unless
    `OUT_OF_BAND_APPROVALS_ENABLED` and `PARKED_APPROVALS_ENABLED` are on, so
    unattended asks park in TeamWork and resume on the decision.
  - For teammate principals, the chat fallback (§3.5 item 2) is disabled.
  - Prax's credential does not hold `approval.decide` (`*` never grants it),
    so Prax cannot approve its teammates' requests.
  - Hire, widen and decide need a person-authenticated TeamWork session
    (`_require_person`, `teamwork/src/teamwork/routers/approvals.py:45-75`).
    If TeamWork has no person authentication configured, that is the weak
    link (open question 12).
- **Kill switch.** One person-only action, "Stop all teammates", in TeamWork
  and as `scripts/company.py stop-all`. It:
  1. sets `company.paused` in `company.db`;
  2. removes the teammates' scheduler jobs;
  3. cancels in-flight teammate turns through the turn registry (the wrapper
     raises `TurnCancelled` at the next tool call, `governed_tool.py:403-406`);
  4. marks the teammates offline in TeamWork.

  `COMPANY_MODE_ENABLED=false` is the cold switch. Per-teammate pause is the
  small one.
- **Lethal trifecta per teammate.**
  - Each teammate turn has its own `TurnGovernanceState`, so legs accumulate
    per teammate.
  - For teammate turns the guard is **forced on**, regardless of
    `LETHAL_TRIFECTA_GUARD`. So are hard floors (regardless of
    `HARD_FLOORS_ENABLED`) and spoke enforcement (regardless of
    `SPOKE_GOVERNANCE_ENABLED`). For an unattended agent, a refused turn is
    the safe failure.
  - Hire-time validation rejects all-three-legs profiles (§5.5).
  - Known limit: the leg model does not count a URL fetch as a sink
    (`prax/agent/trifecta.py:70-107`). A teammate with web fetch and private
    reads could leak through a query string. Phase 1 templates therefore pair
    web read only with the teammate's own spaces, whose content came from the
    web anyway.
- **Prompt injection between agents.**
  - Messages from other agents are **untrusted input**. TeamWork already sends
    no webhook for agent-posted messages (`messages.py:704-722`), so a
    teammate cannot trigger Prax or another teammate through chat. That stays
    the default.
  - Anything that reads channel history, including #random generation (§8),
    treats agent-authored lines as quoted data.
  - A teammate's DM is answered by that teammate's turn, under that
    teammate's profile, never Prax's (§9.1).
- **Memory.** In Phase 1, teammate turns do not write long-term memory:
  memory consolidation is skipped for teammate principals. Teammate output is
  web-derived more often than not, and "provenance follows content". Writing
  memory under teammate provenance is a later decision.
- **TeamWork hardening before Phase 2.** Enforce `scoped_to` and
  `project.read`, apply the identity check on task and status writes, and add
  a basic per-credential rate limit (§3.2).

## 7. Cost control

- **The enforced caps live in a ledger** (`company.db`, a table of spend by
  teammate and by day in the company timezone). They act at three points:
  1. **Before a turn starts:** if the teammate's remaining budget for the day
     or the company's is ≤ 0, the turn does not start. The teammate posts
     "out of budget for today" once.
  2. **During a turn:** the turn budget is `min(TURN_BUDGET_USD if set, the
     teammate's remaining budget, the company's remaining budget)`, through
     the existing `_over_budget` path, which refuses and then ends the turn
     after 3 grace calls (`governed_tool.py:872-921`).
  3. **After the turn:** the execution graph's `cost_so_far()` is added to the
     ledger.
- **Fail closed on unknown prices.**
  - `cost_so_far()` returns `complete=False` when a model has no rate
    (`trace.py:158-184`), and spend is then a lower bound.
  - A teammate may not be hired onto, or run with, a model that has no price
    entry.
  - Paid non-LLM tools, which the ledger does not see (image generation,
    `gpu_power_on`, phone calls), are excluded from teammate templates.
- **Overshoot is bounded, not zero.** The cap trips at the next tool call
  after it is crossed: at most one model call plus 3 grace calls. Estimates
  are from token counts.
- **Backstop.** Set a hard limit at the provider, or on the key, where the
  provider offers one; check the provider's docs before relying on it. A
  per-teammate limit in `prax-secrets-proxy`, outside Prax, is Phase 4.
- **Run windows, not 24/7.** Teammates run only inside their window. The
  company has a cap on total open hours.
- **Cheap defaults.**
  - Teammate loops default to `medium` (gpt-5.4-mini), not low. Nano skips
    tools (`orchestrator.py:240-250`), and a teammate that does nothing still
    costs money.
  - Status summaries and #random use `low`.
  - Per-turn caps are tighter for teammates (`max_tool_calls` 25 instead of 40).
- **The hard stop is the kill switch** (§6), and spend stops when the caps
  are hit.
- **"Test drive" preset**, for one afternoon:
  - one teammate, medium tier, `MEDIUM` ceiling;
  - a researcher template on one space;
  - a 4-hour window;
  - $1.50 for the teammate, $2.00 for the company that day;
  - at most 6 pickups;
  - #random off.

  These numbers are settings, not estimates. After the first run, read the
  ledger's actual cost per pickup and set real caps from it.

## 8. Liveliness

- **Status posts in each teammate's home channel**, generated from real
  events and never from claims:
  - "Picked up *X*" when a card is claimed;
  - progress at milestones the turn reports;
  - "Done: *X*" with the result link;
  - "Blocked on *X*: waiting for your approval" when an approval parks.

  They are templated, with an optional one-line `low`-tier summary of the
  real result.
- **A daily digest** in #general, opt-in, one per day, from the ledger and
  the cards: who did what, and spend so far.
- **#random, company mode only, off by default** (`WATERCOOLER_ENABLED=false`):
  - Turning it on creates `#random`.
  - A scheduler job posts at most `WATERCOOLER_MAX_POSTS_PER_DAY` (default 4),
    only inside run windows, each from one teammate.
  - Each post is a **tool-less**, low-tier text call. It has no tools at all,
    so an injected line in #random cannot become an action.
  - Its inputs are the teammate's persona plus the last few #random lines,
    quoted as untrusted data.
  - It has its own budget, `WATERCOOLER_DAILY_USD` (default $0.10), counted
    in the company total.
- **Honesty rule for chatter.** Prax's prime directive is that it never
  fabricates. Two shapes are possible:
  - **grounded banter:** "TIL from today's scan…", a link a teammate found,
    a reaction to a finished card, all drawn from that teammate's real work
    log;
  - **persona fiction:** hobbies, weekends.

  The recommendation is grounded banter only. Chatter must never claim
  progress that the ledger and cards do not show. Open question 8.

## 9. Sidebar, DMs and hiring

### 9.1 The DM clean-up (both modes)

| Change | Repo | Detail |
|---|---|---|
| `Agent.kind` column: `teammate` (the default for back-compat) or `system` | teamwork | Ad-hoc migration in `main.py:run_migrations` (`:56`); returned by the agents APIs |
| Register and update `kind` | teamwork | `ExternalAgentCreate` gains `kind`, `persona`, `profile_image_type` (`external.py:52-58`). New `PATCH /external/projects/{p}/agents/{a}` (`agent.write`), because Prax reconnects by name and never re-POSTs existing rows (`prax/services/teamwork_service.py:132-150`). |
| Mark the five system roles and the coding-agent identities `system` | prax | `register_role_agents` and `_ensure_agent_channel` (`teamwork_hooks.py:157-220`), plus an idempotent `ensure_agent_kind()` at startup. Pydantic ignores unknown fields, so an older TeamWork is unaffected. |
| DMs list only `kind != system` | teamwork | `ChannelSidebar.tsx:286-301` |
| System roles move to a "Behind the scenes" view: status dot, live output and work logs per role, plus a link to the trace panel | teamwork | Reuses the existing live-output and activity-log components; nothing new in Prax |
| A teammate's DM is answered by the teammate | both | The TeamWork webhook payload gains `channel_type` and `dm_agent_id` (`messages.py:115-171`). Prax routes a teammate's DM to that teammate's turn and replies as the teammate. Other DMs go to Prax, as today. |

**Nothing loses data.**

- Agent rows, any existing role DM channels and their messages, activity
  logs and live-output sinks all stay. The sidebar stops listing them.
- Status calls keep working, because the roles stay registered: hiding them
  is display only.
- Old role DMs were Prax conversations under a per-channel key
  (`teamwork_routes.py:2333-2335`). They remain reachable from "Behind the
  scenes".
- Planner's and Auditor's posts to #general are unchanged by this step.
  Whether they should move is open question 6.

### 9.2 "+" under Direct Messages: hire a teammate

- **Who can use it:** a signed-in person (`_require_person`). The "+" appears
  only when Prax reports company mode is available. Prax reaches the same
  review screen through `teammate_propose_hire`, as an approval card.
- **What it asks**, as one dialog with a review step:
  1. **Name and avatar.** Characters or pets, reusing the dormant
     `profile_image_type` choices.
  2. **Job.** One sentence on what they own.
  3. **Persona.** Background, skills, personality. A "Suggest" button asks
     Prax to draft one from the job; TeamWork stays LLM-free. The dormant
     `TeamPreview` editing UI is the starting point.
  4. **Access.**
     - Pick a template: Researcher, Writer, Analyst, Board keeper.
     - It renders as plain-language checkboxes with risk badges: green = runs
       alone; amber = a person approves each use; red = never for a teammate.
     - Then the Library spaces they may touch.
  5. **Budget.** Daily dollar cap, with the company remaining shown, and the
     model tier.
  6. **Schedule.** Days, hours and timezone, or "only when I message them".
  7. **Channels.** A home channel (create one or pick one), and whether they
     may post in #random.
  8. **Review and hire.**
- **What happens on hire:**
  - Prax writes the profile to `company.db`.
  - It registers the agent (`kind=teammate`, persona), creates the home
    channel, and in Phase 2 mints a bound TeamWork credential.
  - It schedules the window.
  - The teammate posts a one-line hello in its home channel.

### 9.3 Channel review

| Channel | What it actually carries today | Solo | Company |
|---|---|---|---|
| #general | User↔Prax; Planner's plan; Auditor's flags; scheduler deliveries (§3.4) | keep | keep. Optional daily digest. |
| #engineering | Described as "agent-to-agent work conversations" (`external.py:332`), but it is a log of `delegate_task` and sandbox spoke output (`subagent.py:249`; `_runner.py:519-521`). Nobody converses there. | keep. Suggest grouping it with the others below as a collapsed **Workbench** section. | same. Teammates get their own home channels instead of posting here. |
| #research | Researcher output (`research_agent.py:435`) | as above | as above |
| #browser, #content | Spoke output (`_runner.py:519-521`) | as above | as above |
| #discord, #sms | Mirrors of those channels | keep, grouped as **Mirrors** | keep |
| #random | Not present in Prax workspaces (§3.3) | absent | only with `WATERCOOLER_ENABLED` |
| #claude-code, #codex, #opencode | Created lazily by dead paths (§3.5 item 5) | remove the code paths | — |
| Teammate home channels | — | — | one per teammate, or shared by a team |

The Workbench and Mirrors grouping is a TeamWork display change, using a
channel `category` field. It is optional and open question 7.

## 10. What each piece builds on

| Piece | Reuses |
|---|---|
| Persona storage and display | TeamWork `Agent.persona`, `profile_image_type`, `soul_prompt`/`skills_prompt`, `ProfileModal`, the dormant `TeamPreview` edit UI |
| Teammate identity | TeamWork `AgentClient` bound credentials, capabilities, Ed25519 signing, event log; `may_act_as` |
| Job-scoped profile, build time | The `eval_tool_denylist` ContextVar pattern and its build sites (`tool_registry.py:25-38`) |
| Job-scoped profile, call time | `wrap_with_governance`, beside `hard_floors.is_floor`; MCP's two-check pattern (`mcp/server.py:134-153,176-180`) |
| Principal propagation | `UserContextSnapshot` capture and restore (`user_context.py:72-105`; `governed_tool.py:365-386`) |
| HIGH asks while unattended | `human_approval` and `parked_approvals` (`human_approval.py:128-169`; `parked_approvals.py:135-165`), once the §3.5 item 1 bug is fixed |
| Backlog | Library Kanban, free-form `assignees` (`library_tasks.py:46`); task-runner selection and reporting (`task_runner_service.py:130-200,321-352`) |
| Run windows | The global APScheduler and per-user jobs (`scheduler_service.py:29,649-658`; `task_runner_service.py:388-405`) |
| Separate history | `ConversationService.scoped_conversation_key` (`conversation_service.py:76-88`) |
| Spend | `ExecutionGraph.cost_so_far()` and `prax/eval/pricing.py`; the `_over_budget` refuse-then-end path |
| Kill switch | The turn registry and `TurnCancelled`; `task_runner_pause`; TeamWork project pause |
| Trifecta per teammate | `trifecta.legs_for`, `should_escalate_sink` (`trifecta.py:122-191`), per-turn state |
| Posting | `teamwork_hooks.post_to_channel`, `log_activity`, `push_live_output` |
| DMs | TeamWork `/api/channels/dm/{agent_id}` (`channels.py:219-256`); the webhook (`messages.py:115-171`) |

## 11. Phased plan

Flags default off. `make ci` is green at every phase. Each phase gets its
own branch off fresh `main`, accumulating on `wip` per house rules. All
testing happens in the **dev tree** (`/data/PRAX`, Prax `:5002`, TeamWork
`:8001`) before anything is deployed.

### Phase 0: clean-up and prerequisites (both modes, ~1 day)

- **prax:**
  - Fix the `parked_approvals` import (§3.5 item 1), with a test that resumes
    a parked schedule.
  - Send `kind=system` for the system roles; add `ensure_agent_kind()`.
  - Remove the dead coding-agent channel paths.
  - For `turn_source in {scheduler, task_runner}`, stop the hard-floor chat
    fallback from treating the synthetic prompt as the user's message. With
    approvals off this refuses (§3.5 item 2). Flag `UNATTENDED_FLOOR_STRICT`,
    default off; recommend flipping it.
- **teamwork:**
  - The `Agent.kind` migration and API.
  - The `PATCH` agent endpoint.
  - The DM filter (flag `HIDE_SYSTEM_AGENTS_FROM_DMS`; open question 2).
  - The "Behind the scenes" view.
- **Tests:**
  - prax: role registration sends `kind`; floor strictness in unattended
    turns; parked schedule resume.
  - teamwork: migration back-compat (missing `kind` reads as `teammate`);
    filter; `PATCH` needs `agent.write`.
- **How to test:** restart the dev stack. Direct Messages shows only Prax.
  The role statuses and logs appear under "Behind the scenes".

### Phase 1: one teammate, one afternoon (the valuable slice)

- **Flags (prax):**
  - `COMPANY_MODE_ENABLED=false`
  - `COMPANY_DAILY_BUDGET_USD=0`. Zero means teammates cannot start: a cap is
    required, so this fails closed.
  - `TEAMMATE_DEFAULT_DAILY_USD`
  - `TEAMMATE_DEFAULT_TIER=medium`
  - `TEAMMATE_MAX_TOOL_CALLS=25`
  - `COMPANY_DB_PATH` (default beside `identity.db`)
- **prax files:**
  - New `prax/services/company/` package:
    - `registry.py` (teammates, profiles, `company.db`);
    - `profiles.py` (templates; validation for subset of Prax's tools, risk
      ceiling, no floors, trifecta legs);
    - `ledger.py`;
    - `runner.py` (window-aware pickups, built on the task runner).
  - `prax/agent/user_context.py`: the `principal` field.
  - `prax/agent/tool_registry.py`: `apply_principal_allowlist`, called at the
    four build sites.
  - `prax/agent/governed_tool.py`, for teammate principals:
    - the call-time profile check;
    - forced floors, trifecta and spoke enforcement;
    - no auto-approve, no earned trust, no chat fallback;
    - ledger-aware budget.
  - `prax/services/task_runner_service.py`: generalise the pickup marker and
    pass a conversation key.
  - `prax/services/teamwork_service.py` and `teamwork_hooks.py`: register a
    teammate with a persona; post as the teammate.
  - `prax/blueprints/teamwork_routes.py`: DM routing by `dm_agent_id`; a
    person-only `POST /teamwork/company/stop`.
  - `scripts/company.py` (`hire --preset test-drive`, `list`, `status`,
    `pause`, `stop-all`). In Phase 1 the person hires from the terminal.
  - `.env-example`.
- **teamwork files:** webhook `channel_type`/`dm_agent_id` (`messages.py:115-171`).
  External register accepts `persona` and `kind` (done in Phase 0).
- **Tests (keyless):**
  - A tool outside the profile is absent from the built list, and refused at
    call time even if injected.
  - A spoke's inner tools are refused.
  - The per-turn rebuild does not widen.
  - The principal survives `delegate_parallel` threads.
  - A Prax-created profile with HIGH, floor or all-three-leg tools is
    rejected.
  - Effective risk (`@risk_tool`) is honoured.
  - The ledger refuses a turn at the cap, stops a turn mid-way, refuses
    unpriced models, and rolls over in the company timezone.
  - Pickups happen only inside the window, only in the teammate's spaces, and
    only on cards a person or Prax assigned. Teammate history uses its own
    key. Memory consolidation is skipped.
  - DM routing replies as the teammate.
  - The kill switch cancels an in-flight turn and blocks new ones.
  - `COMPANY_MODE_ENABLED=false` changes nothing.
- **How to test (an afternoon, under $2 by construction):**
  1. In the dev `.env`, set `COMPANY_MODE_ENABLED=true`,
     `COMPANY_DAILY_BUDGET_USD=2`, `OUT_OF_BAND_APPROVALS_ENABLED=true` and
     `PARKED_APPROVALS_ENABLED=true`. The local launch already sets
     `TASK_RUNNER_ENABLED`.
  2. `uv run python scripts/company.py hire --preset test-drive --name Ada --job "…" --space q4-scan`.
  3. Ada appears under Direct Messages, with a persona in her profile.
  4. Put three cards assigned to `ada` in that space. Watch:
     - a pickup comment on each card;
     - posts in her home channel;
     - result comments, and cards moved to done;
     - `scripts/company.py status` shows spend.
  5. DM Ada; she answers as Ada.
  6. Ask her to log in somewhere or to share a file publicly. She has no such
     tool, so she refuses. A HIGH ask lands in TeamWork's approval dialog,
     and Prax cannot approve it.
  7. Lower her cap to $0.05. The next pickup does not start, and she posts
     that she is out of budget.
  8. Run `stop-all` mid-card. The turn ends at its next tool call, and
     nothing else starts.

  **Pass** means every step behaved as described and the ledger total matches
  the provider's usage page within the overshoot bound.

### Phase 2: hiring from TeamWork, and Prax proposes

- **teamwork:**
  - The "+" and hire dialog (§9.2);
  - a proxy route to Prax's company API;
  - person-only access;
  - per-teammate credentials;
  - `scoped_to`, `project.read`, identity checks on task and status writes,
    and a rate limit.
- **prax:**
  - `teammate_propose_hire` and the `teammate_*` tools in the `tasks` spoke;
  - widening as a hard floor on the diff;
  - persona suggestion through a low-tier draft.
- **How to test:** hire from the "+". Ask Prax to "get someone on the
  competitor scan". Approve its proposal. Try to widen a teammate from chat:
  it is refused, and the proposal lands in approvals.

### Phase 3: liveliness

- Home-channel status posts are already in Phase 1. This phase adds:
  - the daily digest;
  - #random behind `WATERCOOLER_ENABLED`, with its own cap and a post limit;
  - grounded banter.
- **Tests:** rate limit; budget; the chatter call has no tools; #random
  input is quoted.
- **How to test:** enable it for an afternoon and read #random. Spend stays
  under `WATERCOOLER_DAILY_USD`.

### Phase 4: a team, with Prax as manager

- Several teammates, and Prax splitting a big task into cards for them.
- A Team panel: who is on what, today's spend, the next window.
- The idle-mode read-only proactive research (#23 P1 with a code-enforced
  read-only profile).
- A per-teammate token and spend cap in `prax-secrets-proxy`.
- Key custody outside Prax.
- Optionally, re-enabling the dormant Startup card as "start a company",
  with Prax composing the team.

## 12. Fit with earlier verdicts

- **[matrix.build](../research/matrix-autonomous-company.md)** called a
  multi-agent org layer a "deliberate non-goal". That verdict was about a
  *swarm* of peers coordinating in continuous loops. This design keeps Prax
  as the single verifying hub. Teammates are bounded single-agent loops with
  distinct jobs that do not talk to each other through chat.
- **[Scaling agent systems](../research/scaling-agent-systems.md)**: a
  verifying hub held error amplification to 4.4×, against 17.2× for
  independent peers. Coordination pays for long-running and waiting work,
  which is the case here, and costs on tasks Prax finishes in one turn. A teammate
  is for the former.
- **[Constraint factorization](../research/constraint-factorization-mas.md)**:
  personas must partition evaluative authority. Here a persona carries
  description only, and authority comes from the profile.

## 13. Open questions

1. Is **"teammate"** the word? (The alternatives were colleague and crew.)
2. **DM clean-up:** ship it default-on as a fix, since today's role DMs reach
   Prax under another name, or behind a flag per the convention? The
   recommendation is default-on.
3. **May Prax hire without asking**, from templates you approved and within
   the company budget, or does every hire need your click? The recommendation
   is a click for every hire until Phase 4.
4. **Test-drive money:** is $2/day company and $1.50/teammate right? And
   which provider-side cap will you set as the backstop?
5. **Teammate model tier:** medium (gpt-5.4-mini), or an OpenRouter model you
   prefer for cost?
6. **System roles:** should Planner's plans and Auditor's flags keep posting
   in #general, or move to "Behind the scenes" with the rest?
7. **Spoke-output channels** (#engineering, #research, #browser, #content):
   keep them, group them as a collapsed Workbench, or retire them in favour
   of the trace panel?
8. **#random:** grounded work banter only (recommended), or persona small
   talk as well?
9. **Start a company with a team** (the dormant onboarding, composed by Prax),
   or hire one at a time (recommended first)?
10. **Data access:** teammates share your workspace and memory, scoped by
    profile, or each gets its own workspace? Can they read other spaces?
11. **Run windows:** which timezone and default hours?
12. Does your **TeamWork require a person login** (`INTERNAL_API_KEY` or the
    auth proxy)? Hire, widen and approve depend on it.
13. **Company mode** as a toggle on your existing workspace, or a new project?

## Sources

- OpenAI, ChatGPT dots feature page, Introducing dots (2026-09-29), dots
  safety blog, and Help Center articles 20001530 and 20001529. Links and
  quotes are in §1.
- Code inventory: prax `8a763de` (main), TeamWork working tree `125d7d2`
  (v0.25.1). Read-only. No tests were run for this note.
