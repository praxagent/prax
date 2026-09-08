"""The consolidation pointer must track exactly what the extractor was handed.

Two defects, both found in the 2026-09 review of the memory pipeline:

1. `_read_unconsolidated` counted RAW lines on the way in (`enumerate(f)`) but
   the pointer was advanced by `len(batch)` — the number of NON-BLANK lines
   sent.  Every blank separator inside a batch left the pointer one short, so
   the tail of each batch was consolidated again next run (the review measured
   13 re-sent lines per 50-line batch on a real trace).

2. trace.log is rotated at 512 KB (`workspace_service._rotate_trace`) and
   replaced by a small file with a header line.  A pointer that sat at, say,
   line 3000 of the old file then pointed past the end of the new one, and
   consolidation read nothing — forever.  The review rated this high: in the
   default configuration long-term memory quietly stopped being written.

Keyless: the extractor, embedder and stores are patched; only the pointer
arithmetic and the trace file are real.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from prax.services.memory import consolidation

EMPTY_EXTRACTION = {
    "entities": [], "relations": [], "facts": [], "temporal_events": [], "causal_links": [],
}


@pytest.fixture
def workspace(tmp_path):
    def _root(user_id: str) -> str:
        path = os.path.join(str(tmp_path), user_id)
        os.makedirs(path, exist_ok=True)
        return path

    with patch("prax.services.workspace_service.workspace_root", side_effect=_root):
        yield tmp_path


@contextmanager
def _pipeline(seen: list[str]):
    """Run consolidate_user keyless; every extractor input is appended to `seen`."""

    def _extract(text: str) -> dict:
        seen.append(text)
        return dict(EMPTY_EXTRACTION)

    with patch.object(consolidation, "_extract_entities_relations", side_effect=_extract), \
         patch.object(consolidation, "_build_daily_summary", return_value=""), \
         patch("prax.services.memory.embedder.embed_text", return_value=[0.1] * 8), \
         patch("prax.services.memory.embedder.sparse_encode", return_value={1: 0.5}), \
         patch("prax.services.memory.vector_store.upsert_memory", return_value="mem-1"), \
         patch("prax.services.memory.vector_store.decay_memories", return_value=0), \
         patch("prax.services.memory.graph_store.decay_graph", return_value=0):
        yield


def _state_path(workspace, uid: str):
    return workspace / uid / "memory" / "consolidation_state.json"


def _write_state(workspace, uid: str, state: dict) -> None:
    path = _state_path(workspace, uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def _lines_sent(seen: list[str]) -> list[str]:
    return [ln for text in seen for ln in text.split("\n")]


# --------------------------------------------------------------------------- #
# 1. Raw-vs-non-blank drift
# --------------------------------------------------------------------------- #

def test_pointer_advances_past_blank_separators_it_consumed(workspace):
    """The review's 13/50 case: 50 entries with 13 blank separators among them.

    Old arithmetic: pointer = 0 + len(batch) = 50, but 63 raw lines were read,
    so the next run started 13 lines early and re-sent entries it had already
    consolidated.  Now the pointer lands on raw line 63 and the second run has
    nothing to do.
    """
    uid = "drift"
    raw: list[str] = []
    for i in range(50):
        if i % 4 == 0:            # i = 0, 4, ..., 48 -> 13 separators
            raw.append("")
        raw.append(f"[USER] entry {i:02d}")
    assert raw.count("") == 13 and len(raw) == 63
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("\n".join(raw) + "\n")

    seen: list[str] = []
    with _pipeline(seen):
        consolidation.consolidate_user(uid)
        first_run = list(seen)
        consolidation.consolidate_user(uid)

    sent = _lines_sent(first_run)
    assert sent == [ln for ln in raw if ln], "first run must send every entry once"

    state = json.loads(_state_path(workspace, uid).read_text())
    assert state["last_consolidated_line"] == 63, (
        f"pointer must be one past the last RAW line sent, got {state['last_consolidated_line']}"
    )
    assert len(seen) == len(first_run), (
        f"second run re-sent {_lines_sent(seen[len(first_run):])!r}"
    )
    assert len(_lines_sent(seen)) == len(set(_lines_sent(seen))), "an entry was sent twice"


def test_legacy_state_without_fingerprint_resumes_from_its_pointer(workspace):
    """Equivalence: a pre-2026-09 state file (no trace_head) is not a rotation."""
    uid = "legacy"
    raw = [f"[USER] entry {i}" for i in range(6)]
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("\n".join(raw) + "\n")
    _write_state(workspace, uid, {"last_consolidated_line": 2, "last_daily_summary": "",
                                  "last_decay_run": ""})

    seen: list[str] = []
    with _pipeline(seen):
        consolidation.consolidate_user(uid)

    assert _lines_sent(seen) == raw[2:]
    state = json.loads(_state_path(workspace, uid).read_text())
    assert state["rotation_resets"] == 0
    assert state["last_consolidated_line"] == 6
    assert state["trace_head"] == "[USER] entry 0"


# --------------------------------------------------------------------------- #
# 2. Rotation
# --------------------------------------------------------------------------- #

def test_rotated_trace_shorter_than_pointer_resets_to_zero(workspace):
    """After a 512 KB rotation the new file is a header plus a few entries.

    Old code: pointer 3000 > every line index -> nothing sent, this run and
    every run after it.
    """
    uid = "rotated"
    new_file = [
        "=== Log rotated at 20260907-120000 — previous entries in archive/trace_logs/ ===",
        "[USER] first entry after rotation",
        "[ASSISTANT] second entry after rotation",
    ]
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("\n".join(new_file) + "\n")
    _write_state(workspace, uid, {
        "last_consolidated_line": 3000,
        "trace_head": "=== 2026-08-01T00:00:00Z ===",
        "last_daily_summary": "", "last_decay_run": "",
    })

    seen: list[str] = []
    with _pipeline(seen):
        consolidation.consolidate_user(uid)

    assert _lines_sent(seen) == new_file, "the rotated file must be consolidated from line 0"
    state = json.loads(_state_path(workspace, uid).read_text())
    assert state["last_consolidated_line"] == 3
    assert state["rotation_resets"] == 1
    assert state["last_rotation_reset_at"]
    assert state["trace_head"] == new_file[0]


def test_replaced_trace_already_longer_than_pointer_is_still_detected(workspace):
    """Rotation is detected by fingerprint too, not only by length.

    If consolidation was behind (pointer at 5 of a 500-line file) and the new
    file has already grown past line 5 by the next run, a length check alone
    would resume mid-file and skip the new file's first entries.
    """
    uid = "replaced"
    old_file = ["=== 2026-09-01T00:00:00Z ==="] + [f"[USER] old {i}" for i in range(4)]
    (workspace / uid).mkdir(exist_ok=True)
    trace = workspace / uid / "trace.log"
    trace.write_text("\n".join(old_file) + "\n")

    seen: list[str] = []
    with _pipeline(seen):
        consolidation.consolidate_user(uid)
    assert json.loads(_state_path(workspace, uid).read_text())["last_consolidated_line"] == 5

    new_file = ["=== Log rotated at 20260907-130000 — previous entries in archive/trace_logs/ ==="]
    new_file += [f"[USER] new {i}" for i in range(20)]
    trace.write_text("\n".join(new_file) + "\n")

    seen.clear()
    with _pipeline(seen):
        consolidation.consolidate_user(uid)

    assert _lines_sent(seen)[0] == new_file[0], "must restart at the new file's first line"
    assert _lines_sent(seen) == new_file
    state = json.loads(_state_path(workspace, uid).read_text())
    assert state["rotation_resets"] == 1
    assert state["last_consolidated_line"] == 21


def test_rotation_reset_is_persisted_even_when_nothing_to_consolidate(workspace):
    """A rotated file that is still empty must not leave the stale pointer behind."""
    uid = "rotated-empty"
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("\n\n")
    _write_state(workspace, uid, {"last_consolidated_line": 500, "trace_head": "x",
                                  "last_daily_summary": "", "last_decay_run": ""})

    seen: list[str] = []
    with _pipeline(seen):
        consolidation.consolidate_user(uid)

    assert seen == []
    state = json.loads(_state_path(workspace, uid).read_text())
    # Blank lines carry no content, so the pointer may sit past them (<= 2);
    # what matters is that it no longer points past the end of the file.
    assert 0 <= state["last_consolidated_line"] <= 2
    assert state["rotation_resets"] == 1


def test_missing_trace_file_is_a_noop(workspace):
    seen: list[str] = []
    with _pipeline(seen):
        result = consolidation.consolidate_user("nobody")
    assert seen == []
    assert result.bytes_seen == 0
    assert not _state_path(workspace, "nobody").exists()


# --------------------------------------------------------------------------- #
# 3. Throughput: one run must drain more than one batch, and say what is left
# --------------------------------------------------------------------------- #

def _uniform_trace(workspace, uid: str, n_lines: int, width: int) -> list[str]:
    lines = [f"[USER] {i:04d} " + "t" * (width - 12) for i in range(n_lines)]
    assert all(len(ln) == width for ln in lines)
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("\n".join(lines) + "\n")
    return lines


def test_one_run_drains_several_batches_and_reports_the_backlog(workspace, caplog):
    """400 lines x 100 chars = 40 KB, one consolidate_user call.

    One 4 000-char batch per run could never catch up with a trace that grows
    by several lines of up to 5 000 chars per turn (consolidation runs every 5
    turns), and the 512 KB rotation then discarded the backlog.  Old code:
    exactly one batch (39 lines) per call and no notion of what was left —
    the `> EXTRACTION_CHAR_BUDGET` assertion fails, and `pending_lines` does
    not exist on the result.
    """
    from prax.services.memory.consolidation import EXTRACTION_CHAR_BUDGET, MAX_BATCHES_PER_RUN

    uid = "backlog"
    width, n_lines = 100, 400
    lines = _uniform_trace(workspace, uid, n_lines, width)
    per_batch = (EXTRACTION_CHAR_BUDGET + 1) // (width + 1)   # 39: 39*100 + 38 joiners fit
    drained = per_batch * MAX_BATCHES_PER_RUN

    seen: list[str] = []
    with _pipeline(seen), caplog.at_level(logging.INFO, logger="prax.services.memory.consolidation"):
        result = consolidation.consolidate_user(uid)

    assert sum(len(b) for b in seen) > EXTRACTION_CHAR_BUDGET, "one run must drain more than one batch"
    assert len(seen) == MAX_BATCHES_PER_RUN == result.batches
    assert _lines_sent(seen) == lines[:drained]
    assert result.pending_lines == n_lines - drained
    assert result.pending_bytes == (n_lines - drained) * width
    assert result.bytes_seen == sum(len(b) for b in seen)

    state = json.loads(_state_path(workspace, uid).read_text())
    assert state["last_consolidated_line"] == drained
    assert state["trace_total_lines"] == n_lines
    assert state["trace_pending_lines"] == n_lines - drained
    assert state["trace_pending_bytes"] == (n_lines - drained) * width
    assert any("MAX_BATCHES_PER_RUN" in r.getMessage() and str(n_lines - drained) in r.getMessage()
               for r in caplog.records), "the lag must be visible in the log"

    # The next run finishes the job and reports an empty backlog.
    seen.clear()
    with _pipeline(seen):
        second = consolidation.consolidate_user(uid)
    assert _lines_sent(seen) == lines[drained:]
    assert second.pending_lines == 0 and second.pending_bytes == 0
    assert json.loads(_state_path(workspace, uid).read_text())["last_consolidated_line"] == n_lines


def test_single_batch_trace_behaves_exactly_as_one_batch_per_run(workspace):
    """Equivalence: a trace that fits one batch sees one extraction, one daily
    summary of the same text, and the pointer at the end — unchanged by the
    multi-batch loop."""
    uid = "small"
    lines = _uniform_trace(workspace, uid, 20, 60)     # 20*60 + 19 = 1 219 chars
    text = "\n".join(lines)

    extractor_in: list[str] = []
    summary_in: list[str] = []

    def _summary(t: str) -> str:
        summary_in.append(t)
        return ""

    with _pipeline(extractor_in), patch.object(consolidation, "_build_daily_summary",
                                               side_effect=_summary):
        result = consolidation.consolidate_user(uid)

    assert extractor_in == [text]
    assert summary_in == [text]
    assert result.batches == 1
    assert result.bytes_seen == len(text.encode()) and result.bytes_skipped == 0
    assert result.pending_lines == 0 and result.pending_bytes == 0
    state = json.loads(_state_path(workspace, uid).read_text())
    assert state["last_consolidated_line"] == 20
    assert state["trace_total_lines"] == 20 and state["trace_pending_lines"] == 0


def test_pointer_is_saved_after_every_batch(workspace):
    """A crash mid-run must not re-send batches that were already extracted."""
    uid = "crashy"
    lines = _uniform_trace(workspace, uid, 120, 100)   # 3 full batches + 3 lines
    sent: list[str] = []

    def _extract_then_die(text: str) -> dict:
        sent.append(text)
        if len(sent) == 2:
            raise RuntimeError("provider went away mid-run")
        return dict(EMPTY_EXTRACTION)

    with _pipeline(sent), patch.object(consolidation, "_extract_entities_relations",
                                       side_effect=_extract_then_die):
        with pytest.raises(RuntimeError):
            consolidation.consolidate_user(uid)
    state = json.loads(_state_path(workspace, uid).read_text())
    assert state["last_consolidated_line"] == 39, "batch 1 was committed before batch 2 ran"

    sent.clear()
    with _pipeline(sent):
        consolidation.consolidate_user(uid)
    assert _lines_sent(sent) == lines[39:], "the crashed batch is re-sent, the committed one is not"


# --------------------------------------------------------------------------- #
# 4. Rotation says what it dropped
# --------------------------------------------------------------------------- #

def _rotated_file(workspace, uid: str) -> list[str]:
    new_file = [
        "=== Log rotated at 20260908-090000 — previous entries in archive/trace_logs/ ===",
        "[USER] first entry after rotation",
    ]
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("\n".join(new_file) + "\n")
    return new_file


def test_rotation_reset_reports_the_backlog_it_abandons(workspace, caplog):
    """The reset used to log only itself.  The old file's unconsolidated tail
    goes to archive/trace_logs/, which consolidation never reads — so the
    warning must say how much long-term memory just lost.  Old code: the
    message carries neither the count nor the word "never"."""
    uid = "rotated-with-backlog"
    _rotated_file(workspace, uid)
    _write_state(workspace, uid, {
        "last_consolidated_line": 1000, "trace_head": "=== 2026-08-01T00:00:00Z ===",
        "trace_total_lines": 3000, "trace_pending_lines": 1900, "trace_pending_bytes": 190_000,
        "last_daily_summary": "", "last_decay_run": "",
    })

    seen: list[str] = []
    with _pipeline(seen), caplog.at_level(logging.WARNING, logger="prax.services.memory.consolidation"):
        consolidation.consolidate_user(uid)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("1900 content lines" in m and "never consolidated" in m and "3000 lines" in m
               for m in warnings), warnings
    state = json.loads(_state_path(workspace, uid).read_text())
    assert state["last_rotation_dropped_lines"] == 1900
    assert state["rotation_resets"] == 1
    assert state["last_consolidated_line"] == 2 and state["trace_pending_lines"] == 0


def test_rotation_of_a_legacy_state_says_the_loss_is_unknown(workspace, caplog):
    """A state file from before the backlog was tracked cannot size the drop;
    the warning says so instead of printing a confident zero."""
    uid = "rotated-legacy"
    _rotated_file(workspace, uid)
    _write_state(workspace, uid, {"last_consolidated_line": 1000, "trace_head": "x",
                                  "last_daily_summary": "", "last_decay_run": ""})

    seen: list[str] = []
    with _pipeline(seen), caplog.at_level(logging.WARNING, logger="prax.services.memory.consolidation"):
        consolidation.consolidate_user(uid)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("unknown" in m and "reset from line 1000" in m for m in warnings), warnings
    assert not any("0 content lines" in m for m in warnings)
