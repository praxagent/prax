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
| Record journal (`chain.jsonl`) | One hash-chained line per write to any record above ([Tamper evidence](#tamper-evidence)) | `RECORDS_DIR/` | No |
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
5. **Tamper evidence.** Every write to a record is journaled in a hash chain.
   Under systemd, the chain's head is anchored in the system journal. A change
   made outside Prax can then be detected afterwards. It is detection, not
   prevention: see [Tamper evidence](#tamper-evidence).

## What it does not cover

- **Code running on the host as Prax's user**: a plugin subprocess (no
  filesystem restriction, a known gap in [plugin-trust.md](plugin-trust.md)),
  a self-improvement change, or a compromised Prax.
  - All of it can write `RECORDS_DIR`.
  - Under rootless Docker, container data (Tempo, the wire record) belongs to
    the same host user.
  - Prax's own records are hash-chained ([Tamper evidence](#tamper-evidence)),
    which makes such changes evident, not impossible.
  - Only an off-box copy survives it: back up `RECORDS_DIR`, and copy the wire
    record's head hash off the machine (it anchors that chain).
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

## Tamper evidence

Placement keeps the records away from the agent's tools. Code running on the
host as Prax's own user (a plugin subprocess, a compromised Prax) can still
write them. Against that, Prax keeps a hash-chained journal of its own writes
(`prax/services/record_chain.py`, after the Agent Flight Recorder idea the
paper cites).

### The journal

`RECORDS_DIR/chain.jsonl` gets one line per write to a record, made under the
same lock as the write itself. The records keep their formats; the journal
sits beside them.

```
{"seq", "ts", "op", "file", ...op fields, "prev", "hash"}
hash = sha256(prev + canonical JSON of the entry without "hash")
```

| Op | Fields | Used for |
|---|---|---|
| `append` | `offset`, `length`, `sha256` of the bytes appended | trace log, graphs, feedback, trajectories |
| `write` | `length`, `sha256` of the whole new content | parked approvals; a graphs file Prax rewrites to remove or move one trace (the entry's `note` names it) |
| `rotate` | `to` | the trace log renamed into `trace_logs/` |
| `delete` | `length`, `sha256` of what was deleted, `reason` | graph retention, the only record Prax deletes |
| `adopt` | `length`, `sha256`, `source` | a file the journal starts covering: `initial` (there when the journal began), `legacy` (moved in from the workspace), `found` (see below) |

When the journal is first created, it adopts every record already in
`RECORDS_DIR`, as it finds them.

The head (the last `seq` and `hash`) is only an anchor if it is kept where
Prax's user cannot rewrite it. Prax publishes it as one line,
`RECORD-CHAIN-HEAD seq=<n> hash=<hex> records=<dir>`: at startup, after every
50 entries, and within about ten minutes of any entry not yet anchored.

- It always goes to stderr.
- Under systemd, it also goes straight to the **system journal** over
  journald's socket, unless stderr already goes to the journal.
  - A unit that sends stderr to a file (`StandardError=append:…`, as
    `deploy/systemd/prax.service` does) leaves the line in a file that Prax's
    user can replace whenever the file sits in a directory it can write. That
    anchors nothing, which is why Prax does not rely on stderr there.
  - The system journal records the sending unit itself, so these lines show
    under `journalctl -u prax`. Its files belong to root.
- In Docker or a development run there is no system journal to send to.
  Stderr goes to the container log or the terminal. Under rootless Docker the
  container log belongs to Prax's own host user, so it is not an anchor.
  There, copy the head lines off the box.

### What it detects

`verify` replays the journal against the files. It reports:

- **edits**: bytes Prax appended or wrote that now hash differently;
- **truncation**: a record shorter than what Prax wrote;
- **appends from outside Prax**: a record longer than what Prax wrote;
- **deletion**: a record the journal covers that is missing;
- **planted files**: a file in `RECORDS_DIR` the journal never mentions;
- **journal edits**: a line edited, deleted or moved breaks a `prev` link or
  the sequence;
- **ops Prax never performs**: for example, a whole-file `write` to a trace
  log, a `delete` of anything but an older graphs file, or a rotation
  anywhere but the user's own `trace_logs/`;
- **a rewrite of the whole journal**: rebuilt so that every link holds again.
  This passes the chain check alone. It fails against any head line taken
  before the rewrite, unless the rewrite left every entry up to that line as
  it was.

Prax's writers notice too. Before changing a record, they check it against the
journal. If it is not as the journal left it, Prax:

- logs an error;
- shows the change in `prax_doctor`;
- writes it into the chain as an `adopt` with source `found`, which `verify`
  always reports. A later whole-file write therefore cannot hide it.

Deleting `chain.jsonl` under a running Prax does not reset the chain either:
the next entry links to the head Prax holds in memory, so the cut shows. After
a restart, though, Prax starts a new journal over whatever it finds, and only
the anchors show what came before.

### What it cannot do

- **Prevent anything.** A deleted record is still gone; restore it from a
  backup.
- **Tell Prax from code running as Prax's user.** That code can append
  entries the chain accepts: add a trace line, rewrite parked approvals or a
  graphs file, delete an old graphs file. Only what was anchored before it
  started is fixed. Entries since the last head line are not.
- **Stop a compromised Prax from omitting or fabricating events** before they
  are written. The paper makes the same point about hash chains.
- **Survive someone who can also rewrite the anchor**: root, anyone who can
  edit the system journal's files, or whoever controls the off-box copy.
- **Vouch for content from before the journal.** Records adopted as
  `initial` or `legacy` are anchored as they were found, not as Prax first
  wrote them.

Two more limits:

- Prax's user can add its own `RECORD-CHAIN-HEAD` lines to the system journal,
  but it cannot remove or edit the real ones. `verify` requires every head
  line to match, so a forged one raises an alarm; it cannot hide one.
- A crash between a record write and its journal line leaves bytes the journal
  does not cover. `verify` reports them like any other unexplained change.

### Verifying

Run it from the Prax checkout, as a user who can read `RECORDS_DIR` (and,
for `journalctl`, the system journal):

```bash
journalctl -u prax -o cat \
  | uv run python -m prax.services.record_chain verify --records /path/to/records --anchors-from -
```

- `--records` defaults to `RECORDS_DIR` from Prax's settings.
- `--anchors-from FILE` reads the head lines from a saved copy instead of
  stdin.
- `--anchor-records DIR` checks a copy, such as a restored backup, against the
  head lines Prax wrote for the original directory.
- It prints a short report, with one line per problem (kind, `seq`, file),
  and exits non-zero if there are any.
- If anchor input was given but none of it is for this directory, that counts
  as a problem: a check that silently used no anchors would prove less than
  it claims.
- `python -m prax.services.record_chain head` prints the current head as a
  head line, and `prax_doctor` shows it.

The journal is append-only and never rotated. It grows by roughly 300 bytes
per record write, and `verify` reads all of it and every record.

## Operator checklist

- [ ] `RECORDS_DIR` is outside `WORKSPACE_DIR` (the default is), and is in
      your backups as a whole. That includes `chain.jsonl`: records restored
      without their journal cannot be verified.
- [ ] Nothing mounts `RECORDS_DIR` into the sandbox.
- [ ] The system journal keeps the head lines:
  - it is persistent (`/var/log/journal` exists, or `Storage=persistent`);
  - its retention (`SystemMaxUse`, `MaxRetentionSec`) covers the period you
    may need to audit. A noisy process pushes old entries out sooner.
  - Or copy the head lines off the box with each backup:
    `journalctl -u prax -o cat | grep RECORD-CHAIN-HEAD`, or the container's
    log under Docker.
  - Without systemd, an off-box copy is the only anchor, and it proves only
    what was written before the copy was taken.
- [ ] `python -m prax.services.record_chain verify --anchors-from -` runs
      against the journal from time to time, and after any incident.
- [ ] The secrets proxy runs with `PROXY_WIRE_RECORD=/wire/wire.jsonl`; its
      head hash is copied off the box; `scripts/check_wire_record.py` runs
      against Prax's graphs from time to time.
