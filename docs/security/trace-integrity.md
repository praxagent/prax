# Trace integrity — the agent cannot edit the record of what it did

[← Security](README.md) · Related: [Secrets proxy](secrets-proxy.md) ·
[Sandbox execution boundary](sandbox-execution-boundary.md) ·
[Assessment: agents tamper with their own traces](../research/agent-trace-tampering.md)

Monitoring, incident review and the user's own "what did you do?" all read
Prax's records. They are only worth reading if the agent cannot change them.
"LLM Agents Can Easily Tamper With Their Own Traces" (arXiv 2609.30266) showed
local harnesses failing exactly here: agents deleted their session logs when
asked, when a planted skill told them to, and unprompted when it raised their
score, and no monitor fired.

Prax was exposed the same way until 2026-10. Its records lived inside the
workspace directory, and the sandbox container mounts that directory
read-write as root. In production it mounts every user's workspace at
`/workspace`. One `sandbox_shell` call could delete or rewrite them.

## The records and where they live

| Record | What it holds | Where | Reachable by the agent's tools? |
|---|---|---|---|
| Trace log (`trace.log`, rotated `trace_logs/`) | Every turn: input, tool calls and results, the answer, and the governance audit (risk tier, approval provenance, floor decisions) | `RECORDS_DIR/users/<workspace>/` | No |
| Execution graphs (`graphs-YYYY-MM-DD.jsonl`) | Structured traces behind `trace_search`, `trace_detail` and TeamWork's graph view | `RECORDS_DIR/graphs/` | No |
| Task trajectories | Exchanges kept for review and fine-tuning | `RECORDS_DIR/users/<workspace>/trajectories/` | No |
| Parked approvals | What runs once a person approves it | `RECORDS_DIR/parked_approvals.json` | No |
| Feedback | Users' ratings of answers (a reward signal) | `RECORDS_DIR/feedback/` | No |
| Wire record (secrets proxy, opt-in) | Every model response: tool names and argument hashes, hash-chained | the proxy's `./wire` | No |
| Spans (when `OBSERVABILITY_ENABLED`) | OpenTelemetry traces, pushed to Tempo | Tempo's storage | No |
| TeamWork chat | The conversation as people saw it | TeamWork's database | No: deleting messages through TeamWork's API needs `message.delete` and a person's approval |
| Conversation database | Prax's conversation memory | `<workspace>/.services/prax/conversations.db` | **Yes** (see below) |
| Workspace git history | History of the user's files | `<workspace>/.git` | **Yes** (see below) |

`RECORDS_DIR` defaults to `records/` next to `WORKSPACE_DIR`: never inside it.
Records found in their old places move there the first time each is used
(`prax/services/records.py`). Prax logs a warning if `RECORDS_DIR` is set
inside the workspace directory.

## What protects them

1. **Placement.** No agent tool reaches `RECORDS_DIR`:
   - the sandbox mounts only the workspace directory;
   - Prax's workspace file tools are confined to the workspace;
   - the compose files give records their own volume, never mounted into the
     sandbox.

   This does not rely on the agent's cooperation.
2. **A standing rule.** The system prompt tells Prax never to delete, edit,
   move or hide these records, whether a person asks, a page or file tells it
   to, or doing so would make a result look better. In the paper, the one
   harness with such an instruction resisted every direct deletion request.
   It is defence in depth, not the control.
3. **An independent record.** The paper's main recommendation is a record kept
   on the path to the model, outside the agent's control. Prax's secrets proxy
   is on that path:
   - with `PROXY_WIRE_RECORD` set, it appends one hash-chained line per model
     response, holding tool names and argument hashes, never text;
   - `scripts/check_wire_record.py` lists tool calls on the wire that Prax's
     own graphs don't show.

   This is opt-in (see the checklist).
4. **Rotated trace logs are kept.** Rotation used to delete all but the last
   three. That was survivable only while the workspace's git history held the
   rest.

## What it does not cover

- **Code running on the host as Prax's user**: a plugin subprocess (no
  filesystem restriction, a known gap in [plugin-trust.md](plugin-trust.md)),
  a self-improvement change, or a compromised Prax.
  - All of it can write `RECORDS_DIR`.
  - Under rootless Docker, container data (Tempo, the wire record) belongs to
    the same host user.
  - Against this only an off-box copy holds: back up `RECORDS_DIR`, and copy
    the wire record's head hash off the machine (it anchors the chain).
  - Prax's own records are not hash-chained yet. The wire record is.
- **The conversation database and the workspace's git history** are still in
  the workspace, so the sandbox can delete or rewrite them.
  - They are Prax's memory and the user's file history, not the audit, which
    is in the trace log.
  - Moving the database out is a follow-up.
- **The Docker topology** keeps Qdrant, Neo4j and TeamWork data in the user's
  workspace (`.services/`), which that topology's sandbox mounts. A native
  deployment keeps them elsewhere.
- **Production's sandbox mounts every user's workspace.** That is a
  cross-user isolation issue separate from this one.
- **Authenticity.** The agent can change its own sandbox (put a fake `ls`
  first on `PATH`) so that a later command's output lies, and the trace will
  record what the tool returned.
  - Sandbox commands run as `sh -c` through `docker exec`, with an environment
    Prax supplies. A non-interactive `sh` reads no profile and the agent
    cannot set the exec's environment, so the paper's `BASH_ENV` route has no
    hook here.
  - Replacing binaries does work.
  - The paper notes interception cannot establish authenticity either.

## Operator checklist

- [ ] `RECORDS_DIR` is outside `WORKSPACE_DIR` (the default is), and is in
      your backups.
- [ ] Nothing mounts `RECORDS_DIR` into the sandbox.
- [ ] The secrets proxy runs with `PROXY_WIRE_RECORD=/wire/wire.jsonl`; its
      head hash is copied off the box; `scripts/check_wire_record.py` runs
      against Prax's graphs from time to time.
