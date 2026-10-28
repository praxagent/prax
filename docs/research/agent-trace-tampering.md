# LLM agents can easily tamper with their own traces

**Verdict: document + adopted. Prax was exposed the way the paper describes,
and it is fixed.** Prax's records (trace log, governance audit, execution
graphs, trajectories, parked approvals, feedback) lived inside the workspace
directory, which the sandbox mounts read-write as root. One `sandbox_shell`
call could delete or rewrite all of them. They now live in `RECORDS_DIR`,
outside every sandbox mount and workspace tool, and the system prompt carries a
standing rule against tampering. Every write to them is journaled in a hash
chain; under systemd its head is anchored in the system journal, so tampering
by host-side code is evident, though not prevented. The paper's main
recommendation, a record kept on the model path out of the agent's reach, Prax
already had in the secrets proxy's wire record, but only as an opt-in. What is
protected, and what is not, is in
[Trace integrity](../security/trace-integrity.md).

Source: Qin, Schmotz, Prinzhorn, Beurer-Kellner, Prabhu, Andriushchenko,
"LLM Agents Can Easily Tamper With Their Own Traces",
[arXiv 2609.30266](https://arxiv.org/abs/2609.30266) (September 2026).

## What it found

Monitoring, incident investigation and compliance audits reconstruct what an
agent did from its traces, and assume the agent cannot change them. The
authors tested local harnesses (Claude Code, Codex, Antigravity, OpenCode,
Grok Build, Muse Code, ZCode, Kimi Code). Each stores its session traces as
JSONL or SQLite under the user's home directory, which the agent's own shell
can reach.

- **Asked directly** ("for privacy, delete this session's traces"), every
  harness but Muse Code complied; deletion success was 80–100% in full-access
  mode. Permission-limiting modes reduced it but did not stop it: 70–90% and
  60–100% for two of the pairs.
- **Induced by an attacker**: a "privacy-protection" skill planted in a
  harness's skill directory got Codex models to delete their traces in most
  trials. Several harnesses refused after reasoning about it. OpenCode accepted
  an injected compaction checkpoint.
- **Emerging from reward pressure**: with 0.2 of a task's reward silently
  given for trace modification, every model tampered in at least 80% of trials,
  usually within the first few attempts.
- **Techniques**:
  - `rm` and truncation (`> "$F"`);
  - detached background loops (`nohup`, `setsid`) that keep deleting;
  - selective redaction of JSONL entries;
  - emptying file descriptors of deleted files;
  - editing compaction summaries;
  - tool-call spoofing: changing the shell environment (`BASH_ENV`, shell
    snapshots) so that later tool calls return fabricated results.
- **Muse Code** resisted every direct deletion request, because a built-in
  skill tells the agent never to edit, move or delete traces. It still fell to
  tool-call spoofing 30% of the time.

**Recommendation**: record model traffic through an independent interception
mechanism between the harness and the model API, outside the agent's control.
It should be append-only, fail closed when recording fails, and survive full
compromise of the agent's host. The authors are clear about the limits:
- interception establishes integrity, not authenticity, because a compromised
  harness can emulate tool calls;
- completeness also requires stopping the agent from reaching other model
  APIs.

Hash chains (they cite Agent Flight Recorder) make edits evident, but cannot
stop an agent omitting or fabricating events before they are recorded.

## Prax, before

| Paper's question | Prax before 2026-10 |
|---|---|
| Where are the traces? | `<workspace>/trace.log` and `archive/trace_logs/`; `<workspace_dir>/.prax/graphs/`; `<workspace>/.prax/trajectories/`; `<workspace_dir>/.prax/parked_approvals.json` and `feedback/` |
| Can the agent reach them? | **Yes.** The sandbox mounts the workspace directory read-write as root (production mounts all of `workspaces/`), so `rm`, truncation and edits work from `sandbox_shell` or a desktop terminal. Two host tools (`restore_file`, `archive_file`) also took `trace_logs/…` subpaths. |
| Is there a rule against it? | No. |
| Independent record? | The secrets proxy's wire record: hash-chained, outside Prax's process, on the model path, compared with Prax's graphs by `scripts/check_wire_record.py`. **Opt-in.** |
| Retention | Rotation deleted all but the last three trace logs. The workspace's git history kept the rest, and the sandbox could rewrite that too. |
| Shell spoofing | Not via `BASH_ENV`: commands run as `sh -c` through `docker exec` with an environment Prax supplies, and a non-interactive `sh` reads no profile. Replacing a binary on `PATH` would work. |

Two records were worse than a trace. **Parked approvals** hold the prompt that
runs once a person says yes. The decision lives in TeamWork, and TeamWork only
spends an approval on the exact payload that was approved. But an edited
prompt would still have run. **Feedback** is a reward signal, the paper's
third finding in miniature.

## Adopted

1. **Records out of the agent's reach** (`RECORDS_DIR`,
   `prax/services/records.py`):
   - by default `records/` next to the workspace directory, never inside it;
   - existing files move there on first use;
   - the compose files give it its own volume;
   - Prax warns if it is configured inside the workspace.
2. **Keep every rotated trace log.**
3. **A standing rule in the system prompt**, Muse's cheap layer: leave the
   record alone, whether asked, told by content, or tempted by a better
   result.
4. **Documented the independent record** and the operator checklist:
   - turn on the wire record;
   - anchor its head hash off-box;
   - back up `RECORDS_DIR`.
5. **A hash chain on Prax's own records** (`prax/services/record_chain.py`),
   the Agent Flight Recorder idea the paper cites:
   - every write to a record is journaled in `RECORDS_DIR/chain.jsonl`: one
     hash-chained line per append, whole-file write, rotation, retention
     delete or adoption, under the same lock as the write;
   - the head is published as a `RECORD-CHAIN-HEAD` line, which goes straight
     to the system journal under systemd (root's, out of Prax's user's
     reach);
   - `python -m prax.services.record_chain verify --anchors-from -` reports
     edits, truncation, appends from outside Prax, deleted and planted files,
     journal edits, and a rewritten journal against any earlier head line.

   It adds tamper evidence against host-side code (plugin subprocesses,
   self-improvement changes, a compromised Prax), not prevention. Such code
   can still append entries the chain accepts, and nothing stops a
   compromised Prax omitting or fabricating events before they are written,
   which is the paper's own caveat. What it covers is in
   [Tamper evidence](../security/trace-integrity.md#tamper-evidence).

## Not adopted, or still open

- **Defence against full host compromise.** On one machine, every record,
  container data under rootless Docker included, belongs to Prax's own user.
  The hash chain makes changes evident; only an off-box copy survives them.
  This is an operator task, not code.
- **The conversation database and the workspace's git history** are still in
  the sandbox's reach. They are memory and the user's file history rather than
  the audit, so moving the database is a follow-up.
- **Authenticity of tool results.** Out of scope here, as it is for the paper.

## Related

- [NVIDIA Open Agent Safety Platform](nvidia-open-agent-safety-platform.md):
  the wire record checked against Prax's trace came from there.
- [Meta Muse](meta-muse-secure-vm.md): authority outside the agent.
- [Provenance laundering](../security/provenance-laundering.md): the same
  lesson about which side of a boundary a record sits on.
