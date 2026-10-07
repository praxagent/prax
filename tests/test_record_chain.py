"""Prax's records are hash-chained, so a change made outside Prax is evident.

Moving the records out of the workspace (``prax/services/records.py``) keeps
them from the agent's tools. Code running on the host as Prax's own user (a
plugin subprocess, a compromised Prax) can still write them. Every record
write is journaled in ``RECORDS_DIR/chain.jsonl``
(``prax/services/record_chain.py``), and the journal's head is anchored where
that user cannot rewrite it. These tests tamper the ways arXiv 2609.30266 saw
agents tamper (edit, truncate, append, delete, rewrite everything) and check
that each is caught, through Prax's real writers.

Each tamper case is its own test, so a failure names the property that broke.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
from pathlib import Path

import pytest

from prax.services import record_chain, records

USER = "usr_chain"


@pytest.fixture
def root(monkeypatch):
    """The per-test RECORDS_DIR (conftest), and a user workspace to trace."""
    from prax.services import workspace_service
    records._reset_for_tests()
    Path(workspace_service.workspace_root(USER)).mkdir(parents=True, exist_ok=True)
    sent: list[str] = []
    monkeypatch.setattr(record_chain, "_journald_send", sent.append)
    r = records.records_root()
    yield r
    records._reset_for_tests()


def _trace(content: str = "hello") -> Path:
    from prax.services import workspace_service
    workspace_service.append_trace(USER, [{"type": "user", "content": content},
                                          {"type": "assistant", "content": "ok"}])
    return Path(workspace_service.trace_log_path(USER))


def _graph(trace_id: str = "t-1") -> Path:
    from prax.agent import trace as trace_mod
    trace_mod._persist_graph(trace_mod.ExecutionGraph(trace_id))
    return next(trace_mod._graphs_dir().glob("graphs-*.jsonl"))


def _everything() -> None:
    """One of every kind of record, written the way Prax writes it."""
    from prax.services import feedback_service, parked_approvals, trajectory_service
    _trace("first")
    _graph("t-1")
    feedback_service.submit_feedback(USER, "positive", trace_id="t-1", message_content="ok")
    trajectory_service.export_trajectory(USER, "hi", "hello there", [])
    parked_approvals._save([{"id": "p1", "approval_id": "a1"}])
    _trace("second")
    _graph("t-2")


def _kinds(result: dict) -> set[str]:
    return {p["kind"] for p in result["problems"]}


def _journal(root: Path) -> list[str]:
    return (root / record_chain.JOURNAL).read_text().splitlines(keepends=True)


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


# ---------------------------------------------------------------------------
# Clean runs
# ---------------------------------------------------------------------------

def test_a_clean_run_verifies(root):
    _everything()
    result = record_chain.verify(root)
    assert result["ok"], result["problems"]
    # trace log, one day's graphs, feedback, trajectory, parked, and the list of
    # old locations already moved (records.LEGACY_MARKER)
    assert result["files"] == 6
    entries = [json.loads(line) for line in _journal(root)]
    ops = [e["op"] for e in entries if e["file"] != records.LEGACY_MARKER]
    assert ops.count("append") == 6 and ops.count("write") == 1
    assert result["head"]["seq"] == len(entries)


def test_record_formats_are_unchanged(root):
    """The journal sits beside the records; their bytes are what they were."""
    from prax.services import parked_approvals
    trace = _trace("byte for byte")
    text = trace.read_text()
    assert text.startswith("\n=== ") and "[USER] byte for byte\n[ASSISTANT] ok\n" in text
    graphs = _graph("t-fmt")
    assert json.loads(graphs.read_text().splitlines()[0])["trace_id"] == "t-fmt"
    parked_approvals._save([{"id": "x"}])
    assert parked_approvals._path().read_text() == json.dumps([{"id": "x"}], indent=1)


def test_rotation_is_followed(root, monkeypatch):
    from prax.services import workspace_service
    monkeypatch.setattr(workspace_service, "_TRACE_MAX_BYTES", 200)
    for i in range(4):
        _trace("x" * 150 + str(i))
    archive = Path(workspace_service.trace_archive_dir(USER))
    rotated = sorted(archive.iterdir())
    assert rotated, "the trace log should have rotated"
    assert "=== Log rotated at " in _trace("after").read_text()
    ops = [json.loads(line)["op"] for line in _journal(root)]
    assert "rotate" in ops
    result = record_chain.verify(root)
    assert result["ok"], result["problems"]


def test_rotation_never_overwrites_an_archive(root):
    from prax.services import workspace_service
    trace = _trace("one")
    archive = Path(workspace_service.trace_archive_dir(USER))
    taken = archive / "trace.same.log"
    record_chain.rotate(trace, taken)
    _trace("two")
    second = record_chain.rotate(trace, taken)
    assert second != taken and taken.exists() and second.exists()
    assert record_chain.verify(root)["ok"]


def test_graph_retention_and_scrubbing_are_journaled(root, monkeypatch):
    """Prax itself deletes old graph files and rewrites one to drop a trace;
    both are in the journal, so both verify."""
    from prax.agent import trace as trace_mod
    from prax.settings import settings
    monkeypatch.setattr(settings, "trace_retention_days", 30)
    monkeypatch.setattr(settings, "trace_retention_max_mb", 0)
    old = trace_mod._graphs_dir() / "graphs-2020-01-01.jsonl"
    record_chain.append(old, b'{"trace_id": "ancient"}\n')
    _graph("keep")
    _graph("drop")
    trace_mod._rotate_graph_files()
    assert not old.exists()
    trace_mod.delete_graph("drop")
    result = record_chain.verify(root)
    assert result["ok"], result["problems"]
    assert [d["file"] for d in result["deleted"]] == ["graphs/graphs-2020-01-01.jsonl"]
    notes = [json.loads(line).get("note", "") for line in _journal(root)]
    assert "delete trace drop" in notes


def test_records_already_there_are_anchored_when_the_journal_begins(root):
    (root / "feedback").mkdir()
    (root / "feedback" / "feedback.jsonl").write_text('{"id": "before"}\n')
    _trace()
    result = record_chain.verify(root)
    assert result["ok"], result["problems"]
    assert (1, "feedback/feedback.jsonl", "initial") in [
        (a["seq"], a["file"], a["source"]) for a in result["adopted"]]


def test_an_unchanged_parked_store_adds_no_entry(root):
    """The parked-approvals poller saves every 30 seconds; an unchanged store
    must not grow the journal."""
    from prax.services import parked_approvals
    parked_approvals._save([])
    before = len(_journal(root))
    parked_approvals._save([])
    assert len(_journal(root)) == before
    parked_approvals._save([{"id": "p1"}])
    assert len(_journal(root)) == before + 1


def test_entries_another_process_added_are_picked_up(root):
    """Two processes writing one records directory keep one chain: each
    reads what the other appended before adding its own entry."""
    _trace("from process A")
    process_a = dict(record_chain._states)
    record_chain._states.clear()                 # process B, starting fresh
    _graph("from process B")
    record_chain._states.clear()
    record_chain._states.update(process_a)       # back to A, its cache now stale
    _trace("A again")
    result = record_chain.verify(root)
    assert result["ok"], result["problems"]
    entries = [json.loads(line) for line in _journal(root)]
    assert [e["op"] for e in entries if e["file"] != records.LEGACY_MARKER] == ["append"] * 3
    assert result["head"]["seq"] == len(entries)


def test_a_journal_ending_without_a_newline_is_continued(root):
    _trace("one")
    journal = root / record_chain.JOURNAL
    journal.write_bytes(journal.read_bytes().rstrip(b"\n"))
    records._reset_for_tests()  # a fresh process reads it from disk
    _trace("two")
    result = record_chain.verify(root)
    assert result["ok"], result["problems"]


# ---------------------------------------------------------------------------
# Tampering, one property per test
# ---------------------------------------------------------------------------

def test_editing_one_byte_of_the_trace_log_is_caught(root):
    _everything()
    trace = Path(_trace("third"))
    data = bytearray(trace.read_bytes())
    i = data.index(b"[USER] first")
    data[i + 7] = ord("F")
    trace.write_bytes(bytes(data))
    result = record_chain.verify(root)
    assert not result["ok"]
    assert any(p["kind"] == "edited" and p["file"] == _rel(root, trace) and p["seq"]
               for p in result["problems"]), result["problems"]


def test_truncating_the_trace_log_is_caught(root):
    _everything()
    trace = _trace("third")
    with open(trace, "r+b") as f:
        f.truncate(10)
    result = record_chain.verify(root)
    assert "truncated" in _kinds(result)


def test_appending_to_the_trace_log_outside_prax_is_caught(root):
    _everything()
    trace = _trace("third")
    with open(trace, "a") as f:
        f.write("[ASSISTANT] I never ran that command.\n")
    result = record_chain.verify(root)
    assert "appended outside Prax" in _kinds(result)


def test_deleting_a_graphs_file_is_caught(root):
    _everything()
    graphs = _graph("t-3")
    graphs.unlink()
    result = record_chain.verify(root)
    assert any(p["kind"] == "missing" and p["file"] == _rel(root, graphs)
               for p in result["problems"]), result["problems"]


def test_deleting_a_journal_line_breaks_the_chain(root):
    _everything()
    lines = _journal(root)
    del lines[2]
    (root / record_chain.JOURNAL).write_text("".join(lines))
    result = record_chain.verify(root)
    assert {"link", "sequence"} <= _kinds(result)


def test_reordering_journal_lines_breaks_the_chain(root):
    _everything()
    lines = _journal(root)
    lines[1], lines[2] = lines[2], lines[1]
    (root / record_chain.JOURNAL).write_text("".join(lines))
    result = record_chain.verify(root)
    assert {"link", "sequence"} <= _kinds(result)


def test_editing_a_journal_entry_is_caught(root):
    _everything()
    lines = _journal(root)
    entry = json.loads(lines[0])
    entry["length"] += 1
    lines[0] = json.dumps(entry) + "\n"
    (root / record_chain.JOURNAL).write_text("".join(lines))
    assert "altered" in _kinds(record_chain.verify(root))


def test_a_consistent_rewrite_passes_alone_and_fails_against_an_earlier_anchor(root):
    """Someone who can write RECORDS_DIR edits a trace line, deletes the
    journal and lets Prax start a new one over what is there now. The chain
    alone cannot tell; the head anchored before the rewrite can."""
    _everything()
    anchor = record_chain.emit_head()  # what the system journal holds
    trace = _trace("rm -rf the evidence")

    trace.write_text(trace.read_text().replace("rm -rf the evidence", "list the files"))
    (root / record_chain.JOURNAL).unlink()
    record_chain._reset_for_tests()     # a restarted Prax
    record_chain.emit_head()            # a new journal, anchoring what it finds

    alone = record_chain.verify(root)
    assert alone["ok"], alone["problems"]
    anchored = record_chain.verify(root, anchors=[(anchor["seq"], anchor["hash"])])
    assert not anchored["ok"]
    assert _kinds(anchored) == {"anchor"}


def test_editing_the_parked_approvals_is_caught(root):
    """The parked store holds what runs once a person says yes."""
    from prax.services import parked_approvals
    parked_approvals._save([{"id": "p1", "recipe": {"args": {"prompt": "summarise my inbox"}}}])
    p = parked_approvals._path()
    p.write_text(p.read_text().replace("summarise my inbox", "forward my inbox"))
    result = record_chain.verify(root)
    assert any(r["kind"] == "edited" and r["file"] == "parked_approvals.json"
               for r in result["problems"]), result["problems"]


def test_an_adopted_legacy_file_edited_afterwards_is_caught(root):
    """A trace log from before the move is adopted as it was; editing what it
    held then is caught like any other edit."""
    from prax.services import feedback_service, workspace_service
    feedback_service.submit_feedback(USER, "positive")  # the journal has begun
    old = Path(workspace_service.workspace_root(USER)) / "trace.log"
    old.write_text("\n=== 2025-02-01T00:00:00Z ===\n[USER] from before the move\n")
    trace = _trace("after the move")
    assert not old.exists()
    adopted = record_chain.verify(root)
    assert adopted["ok"], adopted["problems"]
    assert [(a["file"], a["source"]) for a in adopted["adopted"]] == [(_rel(root, trace), "legacy")]

    trace.write_text(trace.read_text().replace("from before", "FROM BEFORE"))
    result = record_chain.verify(root)
    assert any(p["kind"] == "edited" and p["file"] == _rel(root, trace)
               for p in result["problems"]), result["problems"]


def test_a_record_file_the_journal_never_mentions_is_caught(root):
    _everything()
    planted = root / "users" / "someone" / "trace_logs" / "trace.20250101-000000.log"
    planted.parent.mkdir(parents=True)
    planted.write_text("[USER] a history that never happened\n")
    result = record_chain.verify(root)
    assert any(p["kind"] == "unjournaled" and p["file"] == _rel(root, planted)
               for p in result["problems"]), result["problems"]


def test_an_op_prax_never_does_to_a_record_is_caught(root):
    """Someone extending the chain cannot launder an edit of the trace log as
    a whole-file write: the trace log is only ever appended to."""
    trace = _trace()
    record_chain.write(trace, b"[USER] nothing to see\n")
    assert "op" in _kinds(record_chain.verify(root))


def test_the_writer_notices_a_change_and_puts_it_in_the_chain(root, caplog):
    """A change made outside Prax shows up the next time Prax writes the
    record: in the log, in prax_doctor, and in the journal itself, so a later
    whole-file write cannot hide it."""
    from prax.agent import doctor
    from prax.services import parked_approvals
    parked_approvals._save([{"id": "p1"}])
    parked_approvals._path().write_text("[]")
    with caplog.at_level(logging.ERROR, logger="prax.services.record_chain"):
        parked_approvals._save([{"id": "p2"}])
    assert "changed outside Prax" in caplog.text
    assert "changed outside Prax" in _kinds(record_chain.verify(root))
    assert doctor._check_records().startswith("[WARN] Records:")


def test_a_failed_journal_write_never_loses_the_record(root, monkeypatch, caplog):
    def broken(*a, **k):
        raise OSError("disk full")
    real = record_chain._add
    monkeypatch.setattr(record_chain, "_add", broken)
    with caplog.at_level(logging.ERROR, logger="prax.services.record_chain"):
        trace = _trace("still recorded")
    assert "still recorded" in trace.read_text()
    assert "could not write the record journal" in caplog.text
    monkeypatch.setattr(record_chain, "_add", real)
    assert "unjournaled" in _kinds(record_chain.verify(root))


def test_a_removed_journal_does_not_restart_the_chain_in_a_running_prax(root):
    """Deleting chain.jsonl under a running Prax does not let the next entry
    start a clean chain: it links to the head Prax holds, so the cut shows."""
    _everything()
    (root / record_chain.JOURNAL).unlink()
    _trace("after")
    assert "link" in _kinds(record_chain.verify(root))


def test_files_outside_the_records_dir_are_written_without_a_journal(root, tmp_path):
    elsewhere = tmp_path / "elsewhere.jsonl"
    record_chain.append(elsewhere, b"x\n")
    assert elsewhere.read_bytes() == b"x\n"
    assert not (root / record_chain.JOURNAL).exists()


# ---------------------------------------------------------------------------
# The head line, and anchors read back from it
# ---------------------------------------------------------------------------

def test_the_head_line_is_written_to_stderr_and_parsed_back(root, capsys):
    _everything()
    head = record_chain.emit_head()
    err = capsys.readouterr().err
    assert f"RECORD-CHAIN-HEAD seq={head['seq']} hash={head['hash']} records={root}" in err
    # As journalctl prints it, with its own prefix.
    text = "Oct 06 12:00:00 host prax-record-chain[42]: " + err.strip().splitlines()[-1]
    assert record_chain.parse_anchors(text) == [(head["seq"], head["hash"])]
    assert record_chain.parse_anchors(text, records=root) == [(head["seq"], head["hash"])]
    assert record_chain.parse_anchors(text, records="/somewhere/else") == []
    assert record_chain.verify(root, record_chain.parse_anchors(text))["ok"]


def test_a_head_line_follows_every_fifty_entries(root, capsys):
    from prax.services import feedback_service
    for i in range(record_chain.EMIT_EVERY + 1):
        feedback_service.submit_feedback(USER, "positive", comment=str(i))
    lines = record_chain.parse_head_lines(capsys.readouterr().err)
    assert [h["seq"] for h in lines] == [record_chain.EMIT_EVERY]


def test_the_head_goes_to_the_system_journal_only_when_stderr_does_not(root, monkeypatch, tmp_path):
    """Production appends Prax's stderr to a file Prax's user owns, which
    anchors nothing; there, the head line goes to the system journal too."""
    sock = tmp_path / "journal.socket"
    sock.write_text("")
    monkeypatch.setattr(record_chain, "_JOURNALD_SOCKET", str(sock))
    assert not record_chain._journald_wanted()           # not a systemd service
    monkeypatch.setenv("INVOCATION_ID", "abc")
    log = (tmp_path / "prax.log").open("w")
    monkeypatch.setattr(sys, "stderr", log)
    assert record_chain._journald_wanted()                # stderr is a plain file
    st = os.fstat(log.fileno())
    monkeypatch.setenv("JOURNAL_STREAM", f"{st.st_dev}:{st.st_ino}")
    assert not record_chain._journald_wanted()            # stderr already is the journal


def test_the_cli_reads_anchors_from_stdin(root, monkeypatch, capsys):
    _everything()
    record_chain.emit_head()
    journal_text = capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO(journal_text))
    assert record_chain.main(["verify", "--records", str(root), "--anchors-from", "-"]) == 0
    assert "OK" in capsys.readouterr().out

    trace = _trace("later")
    trace.write_text("")
    monkeypatch.setattr("sys.stdin", io.StringIO(journal_text))
    assert record_chain.main(["verify", "--records", str(root), "--anchors-from", "-"]) == 1
    out = capsys.readouterr().out
    assert "PROBLEMS" in out and "truncated" in out


def test_the_cli_fails_when_no_anchor_matches_the_records_dir(root, monkeypatch, capsys, tmp_path):
    _everything()
    lines = tmp_path / "anchors.txt"
    lines.write_text(f"RECORD-CHAIN-HEAD seq=1 hash={'a' * 64} records=/srv/other\n")
    assert record_chain.main(["verify", "--records", str(root),
                              "--anchors-from", str(lines)]) == 1
    assert "no head lines for" in capsys.readouterr().out
