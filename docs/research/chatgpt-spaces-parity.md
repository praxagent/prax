# ChatGPT Space vs. Prax + TeamWork Spaces — the parity gap

**Verdict: document + adopt the single-person half now; take the multi-person
half to TJ as a product decision.** On the axis "one person and their agent
working inside a space", Prax + TeamWork is at parity or ahead. The agent works
inside the space: it picks up Kanban cards assigned to it, keeps a per-space
progress log, can pin a model per space, and cannot touch your writing without
your consent. Outside coding agents can join a space over MCP, and the whole
thing is open source, self-hosted and model-agnostic. On the axis "several
people and their agents editing the same page", we are missing the core of what
OpenAI launched: sharing with view/edit permissions, real-time co-editing,
comments on a passage, and teams. We are also behind on the basics around it:
a real editor, search, trash and safe concurrent writes.

Eight changes, mostly S and M, close every gap that does not need
multiple human users. That is two to three weeks of work across the two repos.
"Zero" on the people axis needs a multi-user identity model and a
reversal of TeamWork's own backlog guardrail against multi-human co-editing.
That is a strategic call, not a parity chore.

Asked by TJ (2026-10-04): *"We beat them to this by many months, but they have
teams of dozens and billions of dollars, so let's examine the parity gap and see
if we can close it to zero."*

## Sources

ChatGPT Space was announced at OpenAI DevDay on 2026-09-29 and is rolling out
from 2026-10-01 to Pro, Business and Enterprise. Everything below is quoted
from OpenAI's own pages, read 2026-10-04:

| Tag | Page |
|---|---|
| **[Page]** | Product page, <https://chatgpt.com/features/space/> |
| **[Start]** | Help: [Getting started with Space in ChatGPT](https://help.openai.com/en/articles/20001549-getting-started-with-space-in-chatgpt) |
| **[Share]** | Help: [ChatGPT Space: sharing, data, and controls](https://help.openai.com/en/articles/20001544-chatgpt-space-sharing-data-and-controls) |
| **[Teams]** | Help: [Teams in ChatGPT](https://help.openai.com/en/articles/20001541-teams-in-chatgpt) |
| **[RN]** | [ChatGPT release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes), 2026-09-29 entries |
| **[Proj]** | Help: [Projects in ChatGPT](https://help.openai.com/en/articles/10169521-projects-in-chatgpt) (Space's sibling) |

chatgpt.com and help.openai.com returned HTTP 403 to direct fetches, so the text
was retrieved through the Jina Reader proxy (`r.jina.ai`). That gives the page
text and image alt-text. It does not give the launch video, and nobody here has
used the product. Press coverage (TechCrunch, VentureBeat, The Decoder) was used
only to date the launch and is not quoted for features.

## What ChatGPT Space is, in OpenAI's words

- **A home for pages and files.** "Space is a home for your pages, uploaded
  files, and other work in ChatGPT" [Start]. "Space replaces Library … Projects
  remain separate" [Start] [Share]. ChatGPT therefore has **two**
  containers: Projects (chats, files, instructions, project memory [Proj]) and
  Space (pages and files). Prax's spaces cover both.
- **Pages** are "a new type of document built for work you do with agents —
  write, visualize, research, and code" [Page]. "Pages can include charts,
  trackers, and interactive tools created by ChatGPT" [Start]. Pages nest:
  "Spaces can contain folders and pages. A page can also contain subpages"
  [Start].
- **Working with the AI:** "tag ChatGPT or your dot to make changes or take on
  next steps — all in the same page" [Page]. "Use comments to give feedback on a
  specific passage … Use the page's chat for broader questions" [Start]. "Ask
  ChatGPT to turn an existing conversation into a page or slides or start from a
  template" [Page]. Codex can be tagged too [Page].
- **Working with people:** "Edit pages with teammates in real time, leave
  feedback in comments" [Page]. Sharing is per page or per space, with view or
  edit access, and access is inherited from the parent [Start]. "Each
  collaborator works with their own ChatGPT. Sharing a page does not give other
  people access to your private chats or your ChatGPT Memory" [Start]. Teams
  share Spaces and run "team tasks for recurring work" on a team service account
  in the cloud [Teams].
- **Dots** (always-on agents launched the same day) "work natively across files
  in Space. Wherever you work with your dot — in ChatGPT, Slack, or Microsoft
  Teams — you can also ask it to keep the page up to date" [Page].
- **Not at launch (OpenAI's own caveats):** "Slides and Sheets are coming soon.
  Automatic page updates with Keep updated are not available at launch" [Start].
  "Mobile does not support editing pages" [Start]. "Enterprise sharing is an
  opt-in preview" [RN]. The product page's "Pages can stay updated from your
  connected tools" [Page] therefore describes a feature that the help center says
  is **not live**. That is marketing ahead of the product, and we should not count
  it as a gap.

## Feature-by-feature

Status: **have**, **partial**, **missing**, **ahead** (we do something ChatGPT
does not document). Effort is S (a day or less), M (two to five days) or L (two
weeks or more, or blocked on a design decision). Prax paths are relative to this
repo, and `teamwork/…` paths are the sibling repo.

### Structure and content

| ChatGPT capability (source) | Us | Evidence | Effort | Proposal |
|---|---|---|---|---|
| Named spaces, a personal space, folders and pages [Start] | **have** | Space → Notebook → Note: `prax/services/library_service.py:344` (`create_space`), `:1157` (`create_notebook`), `:1331` (`create_note`). Each space also carries kind, status, target date, pin, colour theme and an auto-generated cover (`:979`). Home grid: `teamwork/frontend/src/components/panels/HomeDashboard.tsx:148-157` | — | — |
| Subpages that inherit from their parent [Start] | **partial** | Depth is fixed at Space → Notebook → Note (`library_service.py:217-233`). Notes cannot nest | M | An optional `parent` on a note, stored as a folder beside it, plus a tree in the sidebar. Prax storage, TeamWork tree |
| A page is an editable document; edit directly [Start] | **partial** | Notes are markdown. The editor is a monospace `<textarea>` behind an Edit/Save toggle (`teamwork/…/SpacePage.tsx:419-426`, `LibraryPanel.tsx:1343-1347`). The only live preview is in the New Note modal (`LibraryPanel.tsx:1444-1479`) | M | A WYSIWYG markdown editor (TipTap or Milkdown) that still saves plain markdown, so the files on disk stay the source of truth. TeamWork only |
| Charts, trackers and interactive tools inside a page [Start] | **have, under-wired** | Mermaid (Prax validates the diagram before an agent write lands: `library_service.py:1354-1362`), KaTeX, GFM task lists. A sandboxed HTML artifact embeds in a note with a line `[artifact:<id>]` (`teamwork/…/common/MarkdownContent.tsx:218-232`, `ArtifactCard.tsx`). But artifacts are off by default (`ARTIFACTS_ENABLED`, `prax/settings.py:738`). The agent tool never records the space (`prax/agent/artifact_tools.py:53`, although `artifact_service.publish` takes `space`, `prax/services/artifact_service.py:73`). There is no UI to insert one | S | Pass the space through `artifact_publish`, list artifacts per space, add an "insert artifact" action to the editor, and consider defaulting the flag on after the public-exposure gate of #260 |
| Start a page from a conversation [Page] [Start] | **have** | Ask Prax from any channel. The knowledge spoke has `library_note_create` (`prax/agent/library_tools.py:127`, wired at `prax/agent/spokes/knowledge/agent.py:181-194`) | — | — |
| Start from a template [Page] [Start] | **missing** | No space or note templates. The nearest thing is the course generator `library_create_learning_space` (`library_tools.py:593`) | S | Seed templates (welcome, daily to-do, project dashboard, meeting notes, decision log) stored as markdown in Prax, with a picker on the space home in TeamWork |
| Files uploaded into a page [Start] | **have** (per space) | Files tab with drag-and-drop upload and inline preview (`SpacePage.tsx:964-1127`, `library_service.py:3204`). Since #188 the agent can read them (`library_tools.py:1167`, `:1195`) | — | — |
| Images view: "Your creations" and "Uploads" [Start] | **missing** | The Files tab is a list, not a gallery | S | Low value. A gallery filter on the Files tab |
| Delete moves to Trash [Start] | **missing** | Deleting a note is `path.unlink()` (`library_service.py:1634`). Deleting a space is `shutil.rmtree`, with an optional "archive the notes first" (`:1139-1141`). The Archive view cannot restore (`LibraryPanel.tsx:933-1001`) | S | Move to `library/.trash/` with a timestamp, add a restore route and a Trash view, and purge after N days |
| Find: Your items, Shared with you, Suggested, Recents, search [Start] | **partial** | "All Notes" sorted by `updated_at` (`LibraryPanel.tsx:230-258`). **No library search anywhere.** The command palette searches messages only (`teamwork/…/common/CommandPalette.tsx:85-105`). The agent can list and read notes but not search their text (`library_tools.py:1236-1283`; `knowledge_search` searches the graph, not the Library) | S | `library_service.search_notes(query, space=None)` (ripgrep or BM25 over the markdown) as a governed `library_search` tool plus a route, and a library section in the command palette |

### Working with the AI inside a space

| ChatGPT capability (source) | Us | Evidence | Effort | Proposal |
|---|---|---|---|---|
| Ask the AI to edit a page, all of it or one section [Start] | **partial** | "Ask Prax to refine" shows a before/after preview, then Apply (`LibraryPanel.tsx:1284-1297`, `:1580-1609` → `prax/blueprints/teamwork_routes.py:1694`, `:1772`; `library_service.py:2558`). It works on the whole note only. In TeamWork it appears only in LibraryPanel and only for human-authored notes | M | Comes with comments (below): the selection becomes the scope of the edit |
| The page's chat [Start] | **partial** | Each space has its own chat with its own history (`teamwork_routes.py:1034`, `:999`; history key from `conversation_service.py:89`). It injects the space name, up to 10 note titles per notebook and 15 tasks (`teamwork_routes.py:1056-1080`). But **the chat never knows which note is open**: "Discuss" only opens the chat (`SpacePage.tsx:207`, `:355-371`), and `contentContext` is hard-wired to `null` (`teamwork/frontend/src/pages/ProjectWorkspace.tsx:111`). Replies render as plain text, not markdown (`SpacePage.tsx:701`) | S | Send the open note (space/notebook/slug) with each chat message and inject it server-side. Render replies with `MarkdownContent` |
| Comments on a selected passage [Start] [Page] | **missing** | Comments exist only on Kanban tasks (`LibrarySpaceView.tsx:913-936`, `prax/services/library_tasks.py`) | M | Anchored comments (quote, offset, author, resolved) in a sidecar `.comments/<note>.yaml` owned by Prax, with a TeamWork margin UI. A comment that mentions `@prax` starts an agent turn scoped to that note and selection, and still respects `prax_may_edit` |
| Tag ChatGPT, Codex or a dot to "take on next steps" [Page] | **partial / ahead** | No @-tagging inside a note. But Kanban cards take assignees including `prax`, and the task runner picks up cards assigned to Prax (`prax/services/task_runner_service.py:1-20`; **off by default**, `TASK_RUNNER_ENABLED`, `settings.py:1389`). Outside coding agents of any vendor join a space through a per-space MCP key (`teamwork/src/teamwork/mcp_server.py:14-18`, tools at `:95-214`; Settings, `SpacePage.tsx:1634`) | M | `@prax` in comments (above). That covers "tag the AI on this passage". The board already covers "take on next steps" |
| The AI uses the space's context [Page] | **partial** | The agent can list and read notes, files and tasks, but only by name. Chat context is titles, not content. No search (see Find) | S | The `library_search` tool, and the per-space instructions below |
| Ask the agent from Slack or Teams to keep a page up to date [Page] | **have, different channels** | The same agent is reachable on Discord, SMS and TeamWork, and every channel reaches the knowledge spoke's library tools. No Slack or Microsoft Teams | — | Channels are a separate decision, not Space parity |
| Self-updating pages ("Keep updated") [Page]; not at launch [Start] | **partial — and ChatGPT has not shipped it** | The scheduler runs any prompt through the agent on a cron (`prax/services/scheduler_service.py:280`, `:674`). Artifacts are versioned and replaceable (`artifact_service.py:73`). There is no first-class "this note refreshes from that source every N hours" binding | M | A `refresh` block in a note's frontmatter (source, prompt, cron) registered with the scheduler, shown as "updated 2h ago · refresh now". Prax |
| Project instructions [Proj] (ChatGPT keeps these in Projects) | **missing** | The space description is injected into the chat, and `LIBRARY.md` is global (`library_service.py:79-162`). There is no per-space instruction field | S | An `instructions` field on `.space.yaml`, injected in space chat and in any turn whose `current_space_slug` is set. Prax, plus a textarea in Settings |
| Meeting notes saved to Space (Meetings plugin, beta) [RN] | **missing** | — | L | Don't chase it now. It is a capture channel, not part of Space |

### Working with people

| ChatGPT capability (source) | Us | Evidence | Effort | Proposal |
|---|---|---|---|---|
| Share a page or a space with view or edit access; review and revoke [Start] [Share] | **missing** | TeamWork has no user model (`teamwork/src/teamwork/models/` has no User). Auth is one shared key or cookie (`internal_auth.py:1-18`) or a proxy JWT. Prax maps **every** TeamWork request to one identity (`teamwork_routes.py:201-215`, 92 call sites). Note authorship is binary, `human` or `prax`. The only outward sharing is a public, read-only, per-decision link for files, notes and artifacts (`prax/services/share_registry.py:1-20`) | L | Human identities in TeamWork, a per-space ACL (owner, editor, viewer), and a per-request acting user passed to Prax so `author`/`last_edited_by` name a person. Both repos. **This is the decision point** (see the plan) |
| Real-time co-editing; teammate cursors in the screenshot [Page] [Start] | **missing** | A save PATCHes the whole body and **the last write wins**. `update_note` has no version check (`library_service.py:1560-1619`; `teamwork/src/teamwork/routers/library.py:126`). The `library:update` websocket event fires only for MCP writes. Prax's own agent writes announce nothing, and the event does not refresh an open note anyway (`teamwork/src/teamwork/routers/mcp.py:52-87`, `frontend/src/hooks/useWebSocket.ts:278-290`). A person editing a note while Prax edits it will overwrite Prax's change without seeing it. Presence exists only as the agent's typing indicator in channels | L (S precursor) | Precursor, worth doing now: optimistic concurrency, where a save carries the `updated_at` it was based on and gets a 409 on mismatch. This also stops the agent and the human silently overwriting each other today. Then Yjs with a markdown binding, persisted back to the `.md` file. Only after the identity work |
| Access inheritance, workspace admin sharing settings [Start] [Share] | **missing** | Depends on the row above | L | — |
| Teams and team tasks (recurring, in the cloud, on a team service account) [Teams] | **partial** | One person's recurring agent work exists: scheduler plus task runner. TeamWork's "teams" are teams of agents (`teamwork/src/teamwork/models/agent.py:33`), not groups of people | L | Comes with human identities |
| Each collaborator's own AI and Memory stay private [Start] [Share] | **n/a today** | One user per deployment. Long-term memory is per user, not per space (`prax/services/memory/` has no space key) | — | Becomes a real design rule if multi-user lands: memory must never write into a shared page without the author seeing it |

### History, platform and plans

| ChatGPT capability (source) | Us | Evidence | Effort | Proposal |
|---|---|---|---|---|
| Version history (OpenAI documents **none** for pages) | **partial — and overclaimed** | `teamwork/src/teamwork/mcp_server.py:20-24` says "Writes are git-backed … a bad write is recoverable by reverting a commit". **The code does not guarantee that.** `library_service.py` never calls `git_commit`. Library files are tracked, not ignored, so they are swept into whichever commit runs next (a plan step, a todo update: `prax/services/workspace_service.py:299`) under an unrelated message. Two human edits in a row with no other commit between them leave no record of the first. Nothing shows history in the UI | S + M | S: commit on every library write (`library: <who> updated <space>/<note>`). That makes the claim true. M: a History drawer per note (git log, diff, restore) |
| Web and desktop; mobile can read and share but **not edit** [Start] | **have** | TeamWork's Library is responsive, with a drill-in sidebar and the space chat as a bottom sheet (`LibraryPanel.tsx:433-435`, `:811-823`; `SpacePage.tsx:656-671`), guarded by `frontend/src/__tests__/mobile-layout.test.ts`. The textarea editor is not disabled on phones. **Not checked on a real device for this note** | — | — |
| Collaborative Slides and Sheets — "coming soon" [Page] [Start] | **neither side** | Prax exports .pptx, .xlsx and .pdf files (`prax/agent/office_tools.py:45`, `:153`, `:256`). They are not editable in place. TeamWork's Presentations and Quiz tabs (learning spaces) are **placeholders**: their "New" buttons have no handler (`SpacePage.tsx:557-596`) | S now, L later | Now: wire those tabs to the existing tools (office export, professor quizzes) or hide them, because a dead button reads as broken. Later: collaborative decks only if ChatGPT's version turns out to matter |
| Pro, Business and Enterprise plans only [Page] | **ahead** | Open source (Prax Apache-2.0, TeamWork AGPL-3.0), self-hosted, any model, no vendor training on your data | — | Say so (below) |

## Where we are ahead, and should say so

These are verified in code. Items marked *(flag)* are off by default, so
anything public must say "opt-in" or we should flip the default first.

1. **The agent works inside the space, not beside it.** Every space has a
   Kanban board with assignees (people and `prax`), due dates that become
   reminders over SMS, Discord or TeamWork, an activity log and comments
   (`library_tasks.py:1-60`, `LibrarySpaceView.tsx:712-1046`). The task runner
   picks up cards assigned to Prax and reports back on the card *(flag:
   `TASK_RUNNER_ENABLED`)*. ChatGPT's nearest equivalents are dots and team
   tasks, which are separate products and do not come from a board in the
   space.
2. **Consent and provenance on every note.** Each note records `author`
   (human or prax) and `last_edited_by`. A human's note is **read-only to the
   agent** until the person flips `prax_may_edit` (`library_service.py:1588-1594`).
   Flipping it queues a proactive offer to help (`:1673-1712`). OpenAI documents
   nothing comparable: in Space, whoever has edit access, and their AI, can
   change anything.
3. **Per-space model, chat and memory of progress.** A model pin that inherits
   honestly (`library_service.py:747-788`). A separate chat history per space.
   A bounded per-space progress log that survives the context window
   (`prax/services/progress_service.py`, tools at
   `prax/agent/workspace_tools.py:942-1016`). ChatGPT splits this across Projects
   and Space and documents no per-space model.
4. **Any agent can join a space, not just ours.** TeamWork issues per-space MCP
   keys, scoped so a key cannot widen to other spaces. Destructive actions are
   gated through a person, and every action lands in a hash-chained event log
   (`mcp_server.py:1-24`). ChatGPT lets you tag *its* agents (ChatGPT, Codex,
   dots).
5. **Git repositories attached to a space**, with write off until a person turns
   it on per repository and a deploy key per repository
   (`library_tools.py:1285-1447`) *(flag: `SPACE_REPOS_ENABLED`)*.
6. **Knowledge-base operations.** A wiki notebook with automatic wikilinks,
   backlinks, a link graph, a dead-link health check on a schedule, and an inbox
   → promote → archive → outputs flow (`library_service.py:1973-2650`;
   `LibraryPanel.tsx:1357-1380`, `:1769-2154`). Learning spaces add sequenced
   notebooks, generated lessons and flashcards (`library_service.py:620`,
   `:2985`).
7. **You own it.** Plain markdown files on your disk, open source, self-hosted,
   any model (OpenRouter, local, the keyless secrets proxy), every agent write
   through governed tools with risk tiers and audit. No plan gate, no data
   residency waitlist, no training question.
8. **Timing, stated honestly.** The Library landed in this repo's history on
   2026-04-08 (`e7b3c09`) and Spaces on 2026-04-09 (`1175c06`, "feat: add
   spaces"), about **5½ months before ChatGPT Space** (2026-09-29). The history
   was rewritten in July, so the true start may be earlier. We were early to
   *an agent-native space*, but we did not invent the category. ChatGPT Projects
   (December 2024), Notion AI and Microsoft Loop all predate it. "First" is a
   weak claim. "The agent does the work inside the space, under your rules, on
   your box" is a strong one.

**Do not say publicly** "git-backed history" for notes until the per-write
commit ships (see the history row), "real-time collaboration", or "share with
your team". None of them is true today.

## Close-the-gap plan (ranked by value per effort)

| # | Change | Repo / files | Effort | Closes |
|---|---|---|---|---|
| 1 | **Library search**: `search_notes(query, space=None)` in `library_service.py`, a governed `library_search` tool in `library_tools.py` (knowledge spoke), a `GET /teamwork/library/search` route in `teamwork_routes.py`; a TeamWork proxy (`routers/library.py`) plus a Library section in `CommandPalette.tsx` and a search box in `LibraryPanel.tsx` | prax + teamwork | S + S | Find; "the AI uses the space's context" |
| 2 | **Note-aware space chat**: the chat request carries the open note and Prax injects it (`teamwork_routes.py:1034`). "Discuss" passes it, `contentContext` gets wired (`ProjectWorkspace.tsx:111`), replies render as markdown (`SpacePage.tsx:701`) | teamwork (mostly) + prax | S | The page's chat |
| 3 | **Safe writes**: optimistic concurrency on `update_note` (409 on a stale `updated_at`), a git commit per library write, and a `library:update` websocket push for every write (not just MCP) that refreshes an open note | prax (`library_service.py`, `teamwork_routes.py`) + teamwork (`routers/library.py`, `useWebSocket.ts`) | S | Silent lost updates today; makes `mcp_server.py`'s git claim true; the precursor to co-editing |
| 4 | **Trash and history**: soft delete to `library/.trash/` with restore, and a per-note History drawer (log, diff, restore) | prax (`library_service.py`, routes) + teamwork (`LibraryPanel.tsx`, `SpacePage.tsx`) | S + M | Trash; recoverability we already claim |
| 5 | **Comments on notes, with `@prax`**: anchored comments stored beside the note, a margin UI, and `@prax` starting an agent turn scoped to the note and selection (respecting `prax_may_edit`). Selection-scoped refine falls out of this | prax (storage, route, scoped turn) + teamwork (UI) | M | Comments; tag the AI on a passage; section edits |
| 6 | **A real editor**: WYSIWYG markdown that saves plain markdown, a slash menu to insert mermaid, a table, a checklist or an artifact | teamwork | M | Page editing; inserting interactive content |
| 7 | **Templates and per-space instructions**: seed templates plus a picker; an `instructions` field on `.space.yaml` injected into space turns | prax + teamwork (picker, settings field) | S | Templates; ChatGPT Projects' instructions |
| 8 | **Artifacts as page blocks**: `space` passed through `artifact_publish`, a per-space artifact list, and the editor's insert action; decide whether `ARTIFACTS_ENABLED` defaults on | prax (`artifact_tools.py`, `artifact_service.py`) + teamwork | S | Charts and interactive tools in pages |
| 9 | Wire or hide the placeholder Presentations and Quiz tabs; fix the cover upload proxy, which drops the multipart body (`teamwork/src/teamwork/routers/library.py:554-564`; Prax reads `request.files["file"]`, `teamwork_routes.py:614`) | teamwork | S | Dead buttons that read as broken |
| 10 | **Keep updated**: a `refresh` binding in note frontmatter run by the scheduler | prax (+ a small UI badge) | M | Parity with a feature ChatGPT has not shipped; worth doing only after 1–8 |
| 11 | **Subpages** | prax (storage) + teamwork (tree) | M | Nested pages |
| 12 | **Decision for TJ: multiple people.** Human identities in TeamWork, a per-space ACL, an acting user passed to Prax; then teams | teamwork + prax (`_get_teamwork_user_id`'s 92 call sites) | L | Sharing, permissions, teams |
| 13 | Real-time co-editing (Yjs) and presence, only after 12 | teamwork + a prax storage adapter | L | Co-editing |

Items 1–9 are about two to three weeks of focused work and leave the person-plus-agent
experience at parity or ahead on every documented ChatGPT Space feature. Items
12–13 are what "zero" really costs.

**Why 12 is a decision and not a chore.** TeamWork's backlog says, in its
item #1 guardrail, "do **not** chase … **multi-human CRDT co-editing** — wrong
audience for an agent-teammate harness". That was reasoned against Microsoft
Loop, where humans co-author and Copilot assists ([TeamWork vs. Microsoft
Loop](teamwork-vs-microsoft-loop.md)). ChatGPT Space is different. It is pitched
as "People and AI working together" [Page], with agents as named coworkers,
which is TeamWork's own lane. The guardrail deserves a fresh decision rather than
silent inheritance. The cost is real: Prax's workspace, memory and identity
model are per user throughout. The honest options are (a) a shared space owned by
one Prax user that several humans can open, which is cheaper and keeps one
agent identity, or (b) true multi-tenant Prax, which is far larger. Recommend
(a) if TJ wants the people axis.

## Caveats

- **Marketing vs. shipped.** ChatGPT's claims come from its product and help
  pages, not from use. Where they disagree ("Pages can stay updated" vs. "not
  available at launch"), the help center is treated as the truth. A hands-on
  check once rollout reaches a Pro account would firm this up.
- **Polish and quality are not in this table.** A feature count cannot measure
  editor smoothness, model quality, reliability under many users, or design
  finish. OpenAI is very likely ahead on all of those, and the table does not
  pretend otherwise.
- **Our "ahead" list leans on opt-in features.** The task runner, artifacts and
  space repositories are off by default. An out-of-the-box install shows less
  than this note describes.
- **The TeamWork evidence** was gathered by reading code (one sub-agent sweep of
  the five named files and their routers, with the key claims re-checked by
  hand). Nothing was clicked through in a browser, and nothing was tested on a
  phone.
- **Bugs found on the way, not fixed here:** the cover-upload proxy (plan item
  9); the overclaimed git history in `mcp_server.py:20-24` (item 3); Presentations
  and Quiz placeholders (item 9); a `/tags` proxy and a `recharts` dependency with
  no frontend user. The TeamWork items should be mirrored into
  `teamwork/docs/BACKLOG.md` by whoever picks them up, because docs federate by
  ownership.

## Adopt tracker

| Item | Status |
|---|---|
| Library search (tool + route + UI) | 📋 queued, S |
| Note-aware space chat (open note in context; markdown replies) | 📋 queued, S |
| Safe library writes: optimistic concurrency, a commit per write, websocket refresh | 📋 queued, S — fixes silent lost updates today |
| Trash and per-note history | 📋 queued, S + M |
| Anchored comments on notes with `@prax` | 📋 queued, M |
| WYSIWYG markdown editor with insert menu (TeamWork) | 📋 queued, M |
| Templates and per-space instructions | 📋 queued, S |
| Artifacts as page blocks (space-scoped, insertable) | 📋 queued, S |
| Keep-updated refresh bindings | 💤 after the above; ChatGPT has not shipped it |
| Multiple people per space (identity, ACL, teams), then real-time co-editing | ⏸ TJ — product decision; reverses TeamWork backlog #1's guardrail |
