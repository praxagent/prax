"""The wire-record check: every tool call on the model path is accounted for in Prax's traces."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from datetime import UTC, datetime
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_wire_record", Path(__file__).resolve().parents[1] / "scripts" / "check_wire_record.py")
cwr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cwr)


def _write_wire(path: Path, entries: list[dict]) -> None:
    prev = cwr.GENESIS
    lines = []
    for body in entries:
        digest = hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":")))
                                .encode()).hexdigest()
        lines.append(json.dumps({**body, "prev": prev, "hash": digest}))
        prev = digest
    path.write_text("\n".join(lines) + "\n")


def _wire_entry(ts, *names, caller="prax-prod"):
    return {"ts": ts, "caller": caller, "host": "openrouter.ai", "model": "m",
            "tool_calls": [{"name": n, "args_sha256": "x"} for n in names]}


def _write_graphs(d: Path, spans: list[tuple[float, str]]) -> None:
    d.mkdir(parents=True)
    nodes = [{"name": n, "spoke_or_category": "tool",
              "started_at": datetime.fromtimestamp(t, UTC).isoformat()} for t, n in spans]
    (d / "graphs-2026-10-01.jsonl").write_text(json.dumps({"trace_id": "t1", "nodes": nodes}) + "\n")


def test_everything_accounted_for(tmp_path, capsys):
    now = time.time()
    _write_wire(tmp_path / "wire.jsonl", [_wire_entry(now - 100, "delegate_browser"),
                                          _wire_entry(now - 90, "browser_click", "browser_fill")])
    _write_graphs(tmp_path / "graphs", [(now - 99, "delegate_browser"), (now - 89, "browser_click"),
                                        (now - 88, "browser_fill")])
    assert cwr.main([str(tmp_path / "wire.jsonl"), "--graphs", str(tmp_path / "graphs")]) == 0
    assert "0 unaccounted" in capsys.readouterr().out


def test_a_call_missing_from_the_traces_is_reported(tmp_path, capsys):
    now = time.time()
    _write_wire(tmp_path / "wire.jsonl", [_wire_entry(now - 100, "browser_click", "workspace_send_file")])
    _write_graphs(tmp_path / "graphs", [(now - 99, "browser_click")])
    assert cwr.main([str(tmp_path / "wire.jsonl"), "--graphs", str(tmp_path / "graphs")]) == 1
    out = capsys.readouterr().out
    assert "1 unaccounted" in out and "UNACCOUNTED" in out and "workspace_send_file" in out


def test_one_span_cannot_account_for_two_calls(tmp_path):
    now = time.time()
    _write_wire(tmp_path / "wire.jsonl", [_wire_entry(now - 100, "browser_click"),
                                          _wire_entry(now - 95, "browser_click")])
    _write_graphs(tmp_path / "graphs", [(now - 99, "browser_click")])
    assert cwr.main([str(tmp_path / "wire.jsonl"), "--graphs", str(tmp_path / "graphs")]) == 1


def test_other_callers_are_ignored(tmp_path):
    now = time.time()
    _write_wire(tmp_path / "wire.jsonl", [_wire_entry(now - 100, "browser_click", caller="prax-dev")])
    _write_graphs(tmp_path / "graphs", [])
    assert cwr.main([str(tmp_path / "wire.jsonl"), "--graphs", str(tmp_path / "graphs"),
                     "--caller", "prax-prod"]) == 0


def test_a_tampered_record_fails_before_anything_else(tmp_path, capsys):
    now = time.time()
    _write_wire(tmp_path / "wire.jsonl", [_wire_entry(now - 100, "a"), _wire_entry(now - 90, "b")])
    lines = (tmp_path / "wire.jsonl").read_text().splitlines()
    (tmp_path / "wire.jsonl").write_text(lines[1] + "\n")  # first line deleted
    _write_graphs(tmp_path / "graphs", [])
    assert cwr.main([str(tmp_path / "wire.jsonl"), "--graphs", str(tmp_path / "graphs")]) == 1
    assert "BROKEN CHAIN" in capsys.readouterr().out


# --- with argument hashes in the traces ---------------------------------------

def _wire_call(ts, name, h, caller="prax-prod"):
    return {"ts": ts, "caller": caller, "host": "openrouter.ai", "model": "m",
            "tool_calls": [{"name": name, "args_sha256": h}]}


def _write_hashed_graphs(d: Path, spans: list[tuple]) -> None:
    """Spans as (time, name, requested hash, ran hash[, args_changed])."""
    d.mkdir(parents=True)
    nodes = []
    for t, n, req, ran, *changed in spans:
        node = {"name": n, "spoke_or_category": "tool",
                "started_at": datetime.fromtimestamp(t, UTC).isoformat(),
                "requested_args_sha256": req, "args_sha256": ran}
        if changed and changed[0]:
            node["args_changed"] = True
        nodes.append(node)
    (d / "graphs-2026-10-03.jsonl").write_text(json.dumps({"trace_id": "t1", "nodes": nodes}) + "\n")


def test_matching_hashes_are_accounted_for(tmp_path, capsys):
    now = time.time()
    _write_wire(tmp_path / "wire.jsonl", [_wire_call(now - 100, "browser_fill", "aa")])
    _write_hashed_graphs(tmp_path / "graphs", [(now - 99, "browser_fill", "aa", "aa")])
    assert cwr.main([str(tmp_path / "wire.jsonl"), "--graphs", str(tmp_path / "graphs")]) == 0
    assert "0 unaccounted, 0 changed before running" in capsys.readouterr().out


def test_the_trace_misreporting_the_model_is_args_differ(tmp_path, capsys):
    now = time.time()
    _write_wire(tmp_path / "wire.jsonl", [_wire_call(now - 100, "browser_fill", "aa")])
    _write_hashed_graphs(tmp_path / "graphs", [(now - 99, "browser_fill", "bb", "bb")])
    assert cwr.main([str(tmp_path / "wire.jsonl"), "--graphs", str(tmp_path / "graphs")]) == 1
    assert "ARGS DIFFER" in capsys.readouterr().out


def test_arguments_changed_before_running_are_reported(tmp_path, capsys):
    now = time.time()
    _write_wire(tmp_path / "wire.jsonl", [_wire_call(now - 100, "browser_fill", "aa")])
    _write_hashed_graphs(tmp_path / "graphs", [(now - 99, "browser_fill", "aa", "cc", True)])
    assert cwr.main([str(tmp_path / "wire.jsonl"), "--graphs", str(tmp_path / "graphs")]) == 1
    out = capsys.readouterr().out
    assert "CHANGED BEFORE RUNNING" in out and "0 unaccounted" in out


def test_a_post_validation_hash_alone_is_not_a_change(tmp_path, capsys):
    """args_sha256 hashes what the tool received AFTER validation — defaults
    filled in, values coerced — so it differs from the model's request on
    ordinary calls. Only Prax's own args_changed flag is a finding."""
    now = time.time()
    _write_wire(tmp_path / "wire.jsonl", [_wire_call(now - 100, "browser_fill", "aa")])
    _write_hashed_graphs(tmp_path / "graphs", [(now - 99, "browser_fill", "aa", "cc")])
    assert cwr.main([str(tmp_path / "wire.jsonl"), "--graphs", str(tmp_path / "graphs")]) == 0
    assert "0 unaccounted, 0 changed before running" in capsys.readouterr().out


def test_load_tool_spans_reads_the_flag_strictly(tmp_path):
    now = time.time()
    _write_hashed_graphs(tmp_path / "graphs", [(now - 99, "a", "aa", "aa", True),
                                               (now - 98, "b", "bb", "bb")])
    spans = cwr.load_tool_spans(str(tmp_path / "graphs"), now - 200)
    assert [(s[1], s[4]) for s in spans] == [("a", True), ("b", False)]
    assert [s[1] for s in cwr.changed_before_running(spans)] == ["a"]
