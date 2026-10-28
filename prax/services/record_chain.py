"""Tamper evidence for Prax's records: a hash-chained journal beside them.

``prax/services/records.py`` keeps the record of what Prax did out of the
reach of the agent's tools. Code running on the host as Prax's own user (a
plugin subprocess, a compromised Prax) can still write ``RECORDS_DIR``. This
module makes what such code does there evident.

Every write to a record goes through one of the helpers below. Each helper
does the write and appends one line to ``RECORDS_DIR/chain.jsonl``, under one
lock::

    {"seq", "ts", "op", "file", ...op fields, "prev", "hash"}

``file`` is relative to ``RECORDS_DIR``, and
``hash = sha256(prev + canonical JSON of the entry without "hash")``. The first
entry's ``prev`` is ``GENESIS``. The ops:

- ``append``: ``offset``, ``length``, and the ``sha256`` of the bytes appended
  (trace logs, execution graphs, feedback, trajectories);
- ``write``: the ``length`` and ``sha256`` of the whole new content (parked
  approvals, and a graphs file Prax rewrites to remove or move one trace);
- ``rotate``: ``to``, where the trace log was renamed into ``trace_logs/``;
- ``delete``: the ``length`` and ``sha256`` of what was deleted (graph
  retention, the only record Prax deletes);
- ``adopt``: the ``length`` and ``sha256`` of a file's content when the journal
  starts covering it. ``source`` says why: ``initial`` (already there when the
  journal began), ``legacy`` (moved in from the workspace by ``records.py``) or
  ``found`` (Prax found the file other than the journal left it, or holding
  content the journal never covered; ``verify`` reports every one).

Record formats do not change; the journal sits beside the records.

``verify()`` replays the journal against the files. It checks the chain's links,
every appended byte range, each file's length, whole-file writes, missing and
unknown files, and which ops each kind of record allows. It also checks
anchors: ``(seq, hash)`` pairs kept where Prax's user cannot rewrite them. Prax
writes ``RECORD-CHAIN-HEAD seq=<n> hash=<hex> records=<dir>`` to stderr at
startup, every 50 entries, and within about ten minutes of an unanchored entry.
When it runs as a systemd service whose stderr is not the journal (production
appends stderr to a file Prax's user owns), it sends the same line to the
system journal itself. A consistent rewrite of the whole journal and the files
passes the chain check alone, and fails against an anchor taken before it.

What it cannot do: prevent anything, or tell who wrote an entry. Anyone running
as Prax's user can append entries the chain accepts, so only what was anchored
before they started is fixed. And it cannot show that what Prax recorded was
true. See ``docs/security/trace-integrity.md``.

Verify against the system journal::

    journalctl -u prax | python -m prax.services.record_chain verify --anchors-from -
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

JOURNAL = "chain.jsonl"
GENESIS = "0" * 64
HEAD_TAG = "RECORD-CHAIN-HEAD"
EMIT_EVERY = 50          # entries between head lines
EMIT_INTERVAL = 600.0    # seconds: an entry waits at most about this long for an anchor
_CHUNK = 1 << 20
_JOURNALD_SOCKET = "/run/systemd/journal/socket"
_DAY_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\.jsonl$")
_HEAD_RE = re.compile(
    r"RECORD-CHAIN-HEAD seq=(\d+) hash=([0-9a-f]{64})(?: records=(\S.*?))?\s*$")

# Which ops each kind of record allows. A kind not listed here allows any op;
# a new record type should get a row.
_KINDS: list[tuple[str, re.Pattern[str], frozenset[str]]] = [
    ("trace log", re.compile(r"users/[^/]+/trace\.log"), frozenset({"append", "rotate", "adopt"})),
    ("rotated trace log", re.compile(r"users/[^/]+/trace_logs/[^/]+"), frozenset({"adopt"})),
    ("trajectory", re.compile(r"users/[^/]+/trajectories/[^/]+"), frozenset({"append", "adopt"})),
    ("graphs file", re.compile(r"graphs/graphs-\d{4}-\d{2}-\d{2}\.jsonl"),
     frozenset({"append", "write", "delete", "adopt"})),
    ("feedback", re.compile(r"feedback/[^/]+"), frozenset({"append", "adopt"})),
    ("parked approvals", re.compile(r"parked_approvals\.json"), frozenset({"write", "adopt"})),
    ("legacy moves", re.compile(r"\.legacy-moved\.json"), frozenset({"write", "adopt"})),
]


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def entry_hash(entry: dict) -> str:
    """``sha256(prev + canonical JSON of the entry without "hash")``."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    return _sha((str(entry.get("prev", "")) + _canonical(body)).encode("utf-8"))


def _hash_stream(f: IO[bytes], length: int) -> tuple[str, int]:
    """Hash up to *length* bytes from *f*'s position; returns (sha, bytes read)."""
    h, got = hashlib.sha256(), 0
    while got < length:
        chunk = f.read(min(_CHUNK, length - got))
        if not chunk:
            break
        h.update(chunk)
        got += len(chunk)
    return h.hexdigest(), got


def _hash_file(path: Path) -> tuple[str, int]:
    """(sha256, length) of a whole file, read in chunks."""
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        return _hash_stream(f, size)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# Writer state: one per records directory, under one process lock
# ---------------------------------------------------------------------------

@dataclass
class _View:
    """What the journal says a file should be now: its length, and its whole
    content's hash when the last op covered the whole file."""
    end: int
    sha: str | None = None


@dataclass
class _State:
    root: Path
    seq: int = 0
    hash: str = GENESIS
    pos: int = -1                # journal bytes read; -1 = not loaded yet
    need_newline: bool = False   # the journal ends without one
    known: dict[str, _View] = field(default_factory=dict)
    emitted_seq: int = -1
    emitted_at: float = 0.0
    alerts: list[dict] = field(default_factory=list)
    journal_errors: int = 0


_lock = threading.RLock()
_states: dict[str, _State] = {}
_anchor_thread: threading.Thread | None = None
_anchor_stop = threading.Event()


def _records_root() -> Path:
    from prax.services import records
    return records.records_root()


def _state(root: Path) -> _State:
    st = _states.get(str(root))
    if st is None:
        st = _states[str(root)] = _State(root=root)
    return st


def _locate(path: Path) -> tuple[_State | None, str | None]:
    """The chain state and journal name for *path*, or (None, None) when it
    is not under the records directory (a test pointing a writer elsewhere)."""
    try:
        root = _records_root()
    except Exception:
        logger.error("records: cannot find RECORDS_DIR; %s is written without a journal entry",
                     path, exc_info=True)
        return None, None
    try:
        rel = Path(os.path.abspath(path)).relative_to(root).as_posix()
    except ValueError:
        return None, None
    if rel == JOURNAL:
        return None, None
    return _state(root), rel


def _apply(known: dict[str, _View], e: dict) -> None:
    op, name = e.get("op"), e.get("file")
    if op == "append":
        known[name] = _View(int(e["offset"]) + int(e["length"]))
    elif op in ("write", "adopt"):
        known[name] = _View(int(e["length"]), e.get("sha256"))
    elif op == "rotate":
        view = known.pop(name, None)
        if view is not None:
            known[e["to"]] = view
    elif op == "delete":
        known.pop(name, None)


def _load_line(st: _State, raw: bytes) -> bool:
    raw = raw.strip()
    if not raw:
        return True
    try:
        e = json.loads(raw)
        seq, digest = int(e["seq"]), str(e["hash"])
    except Exception:
        return False
    st.seq, st.hash = seq, digest
    try:
        _apply(st.known, e)
    except Exception:
        pass
    return True


def _sync(st: _State, fh: IO[bytes]) -> None:
    """Bring the in-memory head and file views up to the journal's end (all
    of it on first use; only what another process appended after that)."""
    size = os.fstat(fh.fileno()).st_size
    if st.pos < 0:
        st.pos, st.seq, st.hash, st.known = 0, 0, GENESIS, {}
    if size < st.pos:
        # Only something outside Prax shrinks the journal. Keep the head held in
        # memory, so the next entry does not link to what is left in the file
        # and verify reports the cut.
        _alert(st, JOURNAL, f"the journal shrank from {st.pos} to {size} bytes; "
                            f"continuing the chain from seq {st.seq}")
        st.pos = size
        if size:
            fh.seek(size - 1)
            st.need_newline = fh.read(1) != b"\n"
        else:
            st.need_newline = False
        return
    if size == st.pos:
        return
    fh.seek(st.pos)
    data = fh.read(size - st.pos)
    st.pos = size
    lines = data.split(b"\n")
    tail = lines.pop()  # b"" when the data ends with a newline
    bad = sum(0 if _load_line(st, raw) else 1 for raw in lines)
    st.need_newline = bool(tail)
    if tail and not _load_line(st, tail):
        bad += 1
    if bad:
        logger.warning("records: %d line(s) of %s are not journal entries (a write cut short, "
                       "or an edit); verify reports them", bad, st.root / JOURNAL)


def _record_files(root: Path) -> Iterator[Path]:
    """Every record file under *root*: everything but the journal and
    temporary files."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            if name.endswith(".tmp") or (p.parent == root and name == JOURNAL):
                continue
            yield p


def _sweep(st: _State, fh: IO[bytes]) -> None:
    """A new journal anchors every record already there (written before it)."""
    for p in sorted(_record_files(st.root)):
        rel = p.relative_to(st.root).as_posix()
        if rel in st.known:
            continue
        try:
            digest, length = _hash_file(p)
        except OSError:
            continue
        _add(st, fh, {"op": "adopt", "file": rel, "source": "initial",
                      "length": length, "sha256": digest})


@contextmanager
def _journal(st: _State, *, sweep: bool = True) -> Iterator[IO[bytes] | None]:
    """The journal, locked against other processes and read up to its end;
    None if it cannot be opened (the record write goes ahead regardless)."""
    fh: IO[bytes] | None = None
    try:
        fh = open(st.root / JOURNAL, "a+b")
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _sync(st, fh)
        if sweep and st.seq == 0:
            _sweep(st, fh)
    except Exception:
        _journal_failed(st, "open")
        if fh is not None:
            fh.close()
        fh = None
    try:
        yield fh
    finally:
        if fh is not None:
            fh.close()  # releases the lock


def _add(st: _State, fh: IO[bytes], fields: dict) -> dict:
    e = {"seq": st.seq + 1, "ts": _now(), **fields, "prev": st.hash}
    e["hash"] = entry_hash(e)
    line = (b"\n" if st.need_newline else b"") + _canonical(e).encode("utf-8") + b"\n"
    fh.write(line)
    fh.flush()
    st.pos += len(line)
    st.need_newline = False
    st.seq, st.hash = e["seq"], e["hash"]
    _apply(st.known, e)
    _maybe_emit(st)
    return e


def _journal_add(st: _State, fh: IO[bytes] | None, fields: dict) -> None:
    if fh is None or st.pos < 0:  # no journal, or a write to it already failed here
        return
    try:
        _add(st, fh, fields)
    except Exception:
        _journal_failed(st, f"{fields.get('op')} {fields.get('file')}")


def _journal_failed(st: _State, what: str) -> None:
    st.journal_errors += 1
    st.pos = -1  # reread the journal next time
    logger.error(
        "records: could not write the record journal %s (%s). The record itself is "
        "written; this change is not in the chain, and verify will report it.",
        st.root / JOURNAL, what, exc_info=True)


def _alert(st: _State, name: str, detail: str) -> None:
    st.alerts.append({"file": name, "detail": detail, "ts": _now()})
    del st.alerts[:-50]
    logger.error("records: %s was changed outside Prax: %s", name, detail)


def _guard(st: _State, fh: IO[bytes] | None, rel: str, path: Path) -> None:
    """Before Prax changes *path*: if it is not what the journal left (or
    holds content the journal never covered), say so, in the log and in the
    chain, as an ``adopt`` with source ``found``."""
    if fh is None:
        return
    try:
        view = st.known.get(rel)
        if not path.is_file():
            if view is not None and view.end:
                _alert(st, rel, f"missing; the journal had {view.end} bytes")
                _journal_add(st, fh, {"op": "adopt", "file": rel, "source": "found",
                                      "expected": view.end, "length": 0, "sha256": _sha(b"")})
            return
        size = path.stat().st_size
        if view is None:
            if size:
                digest, length = _hash_file(path)
                _alert(st, rel, f"{length} bytes the journal never covered")
                _journal_add(st, fh, {"op": "adopt", "file": rel, "source": "found",
                                      "length": length, "sha256": digest})
            return
        digest = None
        if size == view.end and view.sha is not None:
            digest, _ = _hash_file(path)
            if digest == view.sha:
                return
        elif size == view.end:
            return
        if digest is None:
            digest, size = _hash_file(path)
        _alert(st, rel, f"{size} bytes where the journal had {view.end}"
                        + ("" if size != view.end else ", content changed"))
        _journal_add(st, fh, {"op": "adopt", "file": rel, "source": "found",
                              "expected": view.end, "length": size, "sha256": digest})
    except Exception:
        logger.error("records: could not check %s against the journal", path, exc_info=True)


# ---------------------------------------------------------------------------
# The writers' helpers
# ---------------------------------------------------------------------------

def _append_bytes(path: Path, data: bytes) -> int:
    with open(path, "ab") as f:
        offset = os.fstat(f.fileno()).st_size
        f.write(data)
    return offset


def _replace_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def append(path: str | os.PathLike, data: bytes) -> None:
    """Append *data* to the record at *path*, and journal it. Raises what the
    write raises; a journal failure is logged, never raised."""
    path = Path(path)
    with _lock:
        st, rel = _locate(path)
        if st is None:
            _append_bytes(path, data)
            return
        with _journal(st) as fh:
            _guard(st, fh, rel, path)
            offset = _append_bytes(path, data)
            _journal_add(st, fh, {"op": "append", "file": rel, "offset": offset,
                                  "length": len(data), "sha256": _sha(data)})


def write(path: str | os.PathLike, data: bytes, *, note: str = "") -> None:
    """Replace the record at *path* with *data* (atomically), and journal it."""
    path = Path(path)
    with _lock:
        st, rel = _locate(path)
        if st is None:
            _replace_bytes(path, data)
            return
        with _journal(st) as fh:
            _guard(st, fh, rel, path)
            digest = _sha(data)
            view = st.known.get(rel)
            if fh is not None and view is not None and view.sha == digest \
                    and view.end == len(data) and path.is_file():
                return  # already this content (the guard just checked): no entry
                        # for the parked-approvals poller's every-30-seconds save
            _replace_bytes(path, data)
            fields = {"op": "write", "file": rel, "length": len(data), "sha256": digest}
            if note:
                fields["note"] = note[:200]
            _journal_add(st, fh, fields)


def rewrite(path: str | os.PathLike, transform: Callable[[bytes], bytes | None], *,
            note: str = "") -> bool:
    """Read, transform and write back the record at *path* under the lock, so
    no append lands in between. *transform* returns the new content, or None
    to leave the file alone. Returns whether it was written."""
    path = Path(path)
    with _lock:
        if not path.is_file():
            return False
        old = path.read_bytes()
        new = transform(old)
        if new is None or new == old:
            return False
        write(path, new, note=note)
        return True


def rotate(src: str | os.PathLike, dst: str | os.PathLike, *, header: bytes = b"",
           min_size: int = 0) -> Path | None:
    """Rename the record *src* to *dst* when it is at least *min_size* bytes,
    then start a new *src* with *header*. Never overwrites: a *dst* already
    there gets a numbered name. Returns where it went, or None."""
    src, dst = Path(src), Path(dst)
    with _lock:
        if not src.is_file() or src.stat().st_size < min_size:
            return None
        n = 1
        target = dst
        while target.exists():
            target = dst.with_name(f"{dst.stem}-{n}{dst.suffix}")
            n += 1
        st, rel = _locate(src)
        rel_to = _locate(target)[1] if st is not None else None
        if st is None or rel_to is None:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
            if header:
                _append_bytes(src, header)
            return target
        with _journal(st) as fh:
            _guard(st, fh, rel, src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
            _journal_add(st, fh, {"op": "rotate", "file": rel, "to": rel_to})
            if header:
                offset = _append_bytes(src, header)
                _journal_add(st, fh, {"op": "append", "file": rel, "offset": offset,
                                      "length": len(header), "sha256": _sha(header)})
        return target


def delete(path: str | os.PathLike, *, reason: str) -> bool:
    """Delete the record at *path*, journaling what it held. Only graph
    retention deletes records; verify reports a delete of anything else."""
    path = Path(path)
    with _lock:
        if not path.is_file():
            return False
        st, rel = _locate(path)
        if st is None:
            path.unlink(missing_ok=True)
            return True
        with _journal(st) as fh:
            _guard(st, fh, rel, path)
            digest, length = _hash_file(path)
            path.unlink(missing_ok=True)
            _journal_add(st, fh, {"op": "delete", "file": rel, "length": length,
                                  "sha256": digest, "reason": reason[:200]})
        return True


def adopt(path: str | os.PathLike, *, source: str = "legacy") -> None:
    """Start covering a record moved into the records directory (``records._adopt``):
    journal its content as it is now. A file the journal already covers is left alone."""
    path = Path(path)
    with _lock:
        st, rel = _locate(path)
        if st is None:
            return
        files = [p for p in sorted(path.rglob("*")) if p.is_file()] if path.is_dir() else [path]
        with _journal(st) as fh:
            for p in files:
                rel_p = Path(os.path.abspath(p)).relative_to(st.root).as_posix()
                if fh is None or rel_p in st.known or not p.is_file():
                    continue
                try:
                    digest, length = _hash_file(p)
                except OSError:
                    logger.error("records: could not read %s to journal it", p, exc_info=True)
                    continue
                _journal_add(st, fh, {"op": "adopt", "file": rel_p, "source": source,
                                      "length": length, "sha256": digest})


# ---------------------------------------------------------------------------
# The head, and anchoring it outside Prax's reach
# ---------------------------------------------------------------------------

def _maybe_emit(st: _State) -> None:
    due = st.seq - max(st.emitted_seq, 0) >= EMIT_EVERY or (
        st.emitted_at and time.monotonic() - st.emitted_at >= EMIT_INTERVAL)
    if due:
        _emit(st)


def head_line(seq: int, digest: str, root: Path | str) -> str:
    return f"{HEAD_TAG} seq={seq} hash={digest} records={root}"


def _journald_wanted() -> bool:
    """True when Prax runs as a systemd service and its stderr does not
    already go to the journal. Production appends stderr to a file Prax's own
    user can rewrite, which anchors nothing; the system journal is root's."""
    if not os.environ.get("INVOCATION_ID") or not os.path.exists(_JOURNALD_SOCKET):
        return False
    stream = os.environ.get("JOURNAL_STREAM", "")
    if stream:
        try:
            st = os.fstat(sys.stderr.fileno())
            if stream == f"{st.st_dev}:{st.st_ino}":
                return False
        except Exception:
            pass
    return True


def _journald_send(message: str) -> None:
    """One entry in the system journal, over journald's native socket. The
    journal records the sender's unit itself, so it shows under
    ``journalctl -u prax``."""
    payload = f"MESSAGE={message}\nSYSLOG_IDENTIFIER=prax-record-chain\nPRIORITY=5\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.sendto(payload.encode("utf-8"), _JOURNALD_SOCKET)


def _emit(st: _State) -> None:
    line = head_line(st.seq, st.hash, st.root)
    st.emitted_seq, st.emitted_at = st.seq, time.monotonic()
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    if _journald_wanted():
        try:
            _journald_send(line)
        except Exception:
            logger.warning("records: could not send the chain head to the system journal",
                           exc_info=True)


def _anchor_loop() -> None:
    # Wakes every minute, writes at most one head line per EMIT_INTERVAL: an
    # entry after a quiet spell is anchored within about a minute, and none
    # waits much more than EMIT_INTERVAL.
    while not _anchor_stop.wait(min(60.0, EMIT_INTERVAL)):
        with _lock:
            for st in list(_states.values()):
                if st.seq > st.emitted_seq and time.monotonic() - st.emitted_at >= EMIT_INTERVAL:
                    _emit(st)


def emit_head(root: str | os.PathLike | None = None) -> dict:
    """Write the head line now (starting a journal, if there is none, from
    the records already there). Returns the head."""
    with _lock:
        st = _state(Path(root).resolve() if root else _records_root())
        with _journal(st):
            pass
        _emit(st)
        return {"seq": st.seq, "hash": st.hash}


def start() -> dict:
    """At server startup: anchor the head, and from then on anchor any new
    entry within about EMIT_INTERVAL seconds even when fewer than EMIT_EVERY
    follow it."""
    global _anchor_thread
    head = emit_head()
    with _lock:
        if _anchor_thread is None or not _anchor_thread.is_alive():
            _anchor_stop.clear()
            _anchor_thread = threading.Thread(target=_anchor_loop, name="prax-record-chain",
                                              daemon=True)
            _anchor_thread.start()
    return head


def status(root: str | os.PathLike | None = None) -> dict:
    """The head, and the changes outside Prax the writer noticed since start
    (for prax_doctor). Reads the journal; writes nothing."""
    with _lock:
        st = _state(Path(root).resolve() if root else _records_root())
        if (st.root / JOURNAL).is_file():
            with _journal(st, sweep=False):
                pass
        return {"records": str(st.root), "head": {"seq": st.seq, "hash": st.hash},
                "alerts": list(st.alerts), "journal_errors": st.journal_errors}


def parse_head_lines(text: str) -> list[dict]:
    """Every ``RECORD-CHAIN-HEAD`` line in *text* (``journalctl -u prax``
    output, a log file), as ``{"seq", "hash", "records"}``."""
    out = []
    for line in text.splitlines():
        m = _HEAD_RE.search(line)
        if m:
            out.append({"seq": int(m.group(1)), "hash": m.group(2), "records": m.group(3)})
    return out


def _same_dir(a: str, b: Path) -> bool:
    return a == str(b) or Path(a).resolve() == b.resolve()


def parse_anchors(text: str, records: str | os.PathLike | None = None) -> list[tuple[int, str]]:
    """``(seq, hash)`` anchors from head lines in *text*; with *records*, only
    lines for that records directory (and lines that name none)."""
    target = Path(records) if records is not None else None
    return [(h["seq"], h["hash"]) for h in parse_head_lines(text)
            if target is None or h["records"] is None or _same_dir(h["records"], target)]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass
class _Track:
    since: int
    base_len: int = 0            # the first base_len bytes hash to base_sha
    base_sha: str | None = None
    base_seq: int = 0
    segments: list[tuple[int, int, str, int]] = field(default_factory=list)
    end: int = 0
    last_seq: int = 0


def _kind(rel: str) -> tuple[str, frozenset[str]] | None:
    for name, pattern, ops in _KINDS:
        if pattern.fullmatch(rel):
            return name, ops
    return None


def _valid_name(name: object) -> bool:
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name:
        return False
    parts = name.split("/")
    return ".." not in parts and "" not in parts and name != JOURNAL


def verify(root: str | os.PathLike | None = None,
           anchors: Iterable[tuple[int, str]] | None = None) -> dict:
    """Check the records in *root* (default ``RECORDS_DIR``) against the journal,
    and the journal against *anchors*. Returns ``{"ok", "problems", "head",
    "files", ...}``; each problem is ``{"kind", "seq", "file", "detail"}``."""
    root = Path(root).resolve() if root else _records_root()
    anchors = list(anchors or [])
    problems: list[dict] = []

    def problem(kind: str, detail: str, seq: int | None = None, name: str | None = None) -> None:
        problems.append({"kind": kind, "seq": seq, "file": name, "detail": detail})

    jpath = root / JOURNAL
    raw = jpath.read_bytes() if jpath.is_file() else b""
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()

    prev, want, entries = GENESIS, 1, 0
    head = {"seq": 0, "hash": GENESIS}
    hashes: dict[int, str] = {}
    files: dict[str, _Track] = {}
    adopted: list[dict] = []
    deleted: list[dict] = []
    leading = True  # still in the run of "initial" adopts a new journal starts with

    for n, line in enumerate(lines, 1):
        try:
            e = json.loads(line)
            seq, digest = int(e["seq"]), str(e["hash"])
        except Exception:
            problem("bad line", f"journal line {n} is not an entry (cut short by a crash, "
                                f"or edited)")
            continue
        entries += 1
        if seq != want:
            problem("sequence", f"expected seq {want}, found seq {seq}: entries are missing, "
                                f"repeated or out of order", seq)
        if e.get("prev") != prev:
            problem("link", f"seq {seq} does not follow the entry before it "
                            f"(its prev is not that entry's hash)", seq)
        if entry_hash(e) != digest:
            problem("altered", f"seq {seq} was changed after it was written", seq, e.get("file"))
        hashes.setdefault(seq, digest)
        prev, want = digest, seq + 1
        head = {"seq": seq, "hash": digest}
        is_initial = e.get("op") == "adopt" and e.get("source") == "initial"
        try:
            _replay(e, seq, files, adopted, deleted, leading, problem)
        except (KeyError, TypeError, ValueError):
            problem("malformed", f"seq {seq} lacks the fields its op needs", seq, e.get("file"))
        leading = leading and is_initial

    for seq, digest in anchors:
        if seq == 0:
            if digest != GENESIS:
                problem("anchor", "an anchor for seq 0 names a hash other than the genesis value")
            continue
        have = hashes.get(seq)
        if have is None:
            problem("anchor", f"an anchor names seq {seq}, but the journal ends at seq "
                              f"{head['seq']}: it was cut short or replaced", seq)
        elif have != digest:
            problem("anchor", f"seq {seq} does not match the head anchored when it was written: "
                              f"the journal was rewritten", seq)

    for name, t in sorted(files.items()):
        _check_file(root, name, t, problem)

    for p in sorted(_record_files(root)):
        name = p.relative_to(root).as_posix()
        if name not in files:
            problem("unjournaled", f"{name} is not in the journal: written outside Prax, or "
                                   f"its journal entries are gone", None, name)

    return {"ok": not problems, "problems": problems, "head": head, "files": len(files),
            "entries": entries, "anchors": len(anchors), "adopted": adopted, "deleted": deleted,
            "records": str(root)}


def _replay(e: dict, seq: int, files: dict[str, _Track], adopted: list[dict],
            deleted: list[dict], leading: bool, problem: Callable[..., None]) -> None:
    op, name = e.get("op"), e.get("file")
    if not _valid_name(name):
        problem("malformed", f"seq {seq} names a file outside the records directory: {name!r}", seq)
        return
    kind = _kind(name)
    if op not in ("append", "write", "rotate", "delete", "adopt"):
        problem("op", f"seq {seq} has an unknown op {op!r}", seq, name)
        return
    if kind is not None and op not in kind[1]:
        problem("op", f"seq {seq}: Prax never does `{op}` to a {kind[0]}", seq, name)

    if op == "append":
        offset, length = int(e["offset"]), int(e["length"])
        t = files.setdefault(name, _Track(since=seq))
        if offset > t.end:
            problem("gap", f"bytes {t.end}..{offset} were not written by Prax "
                           f"(found when seq {seq} appended after them)", seq, name)
        elif offset < t.end:
            problem("shrunk", f"seq {seq} appended at byte {offset}, but Prax had written "
                              f"{t.end}: the file was truncated or replaced before it", seq, name)
        t.segments.append((offset, length, str(e["sha256"]), seq))
        t.end, t.last_seq = offset + length, seq
    elif op == "write":
        length = int(e["length"])
        files[name] = _Track(since=files[name].since if name in files else seq, base_len=length,
                             base_sha=str(e["sha256"]), base_seq=seq, end=length, last_seq=seq)
    elif op == "adopt":
        source, length = e.get("source"), int(e["length"])
        if source == "found":
            expected = e.get("expected")
            detail = (f"when Prax next wrote it, it held {length} bytes the journal never covered"
                      if expected is None else
                      f"when Prax next wrote it, it was {length} bytes where the journal had "
                      f"{expected}" + (", with other content" if expected == length else ""))
            problem("changed outside Prax", f"{detail} (seq {seq})", seq, name)
        elif source == "initial":
            if not leading:
                problem("adopt", f"seq {seq} claims {name} was there when the journal began, "
                                 f"but the journal had already begun", seq, name)
        elif source != "legacy":
            problem("adopt", f"seq {seq} adopts with an unknown source {source!r}", seq, name)
        if name in files and source != "found":
            problem("adopt", f"seq {seq} adopts {name} again (covered since seq "
                             f"{files[name].since}): what it held before is no longer checked",
                    seq, name)
        files[name] = _Track(since=seq, base_len=length, base_sha=str(e["sha256"]), base_seq=seq,
                             end=length, last_seq=seq)
        adopted.append({"seq": seq, "ts": e.get("ts", ""), "file": name, "source": source})
    elif op == "rotate":
        to = e["to"]
        if not _valid_name(to):
            problem("malformed", f"seq {seq} renames {name} outside the records directory", seq,
                    name)
            return
        to_kind = _kind(to)
        if kind is not None and (to_kind is None or to_kind[0] != "rotated trace log"
                                 or to.split("/")[1] != name.split("/")[1]):
            problem("op", f"seq {seq} renames {name} to {to}, which is not its own trace_logs/",
                    seq, name)
        t = files.pop(name, None)
        if t is None:
            problem("rotate", f"seq {seq} renames {name}, which the journal does not cover", seq,
                    name)
        if to in files:
            problem("rotate", f"seq {seq} renames {name} over {to}, which the journal covers", seq,
                    to)
        if t is not None:
            t.last_seq = seq
            files[to] = t
    elif op == "delete":
        t = files.pop(name, None)
        length = int(e["length"])
        if t is not None and length != t.end:
            problem("delete", f"seq {seq} deleted {name} at {length} bytes; Prax had written "
                              f"{t.end}", seq, name)
        day = _DAY_RE.search(name) if kind is not None and kind[0] == "graphs file" else None
        if day is not None:
            if day.group(1) >= str(e.get("ts", ""))[:10]:
                problem("delete", f"seq {seq} deleted {name} on its own day; retention only "
                                  f"deletes older files", seq, name)
        deleted.append({"seq": seq, "file": name, "reason": e.get("reason", "")})


def _check_file(root: Path, name: str, t: _Track, problem: Callable[..., None]) -> None:
    p = root / name
    if not p.is_file():
        problem("missing", f"{name} is missing (Prax last wrote it at seq {t.last_seq})",
                t.last_seq, name)
        return
    if not t.segments and t.base_sha is not None:
        # A whole-file record (a write or an adopt, nothing appended since).
        digest, size = _hash_file(p)
        if digest != t.base_sha or size != t.base_len:
            problem("edited", f"not what seq {t.base_seq} recorded ({size} bytes now, "
                              f"{t.base_len} then)", t.base_seq, name)
        return
    with open(p, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if t.base_len and t.base_sha is not None and size >= t.base_len:
            digest, _ = _hash_stream(f, t.base_len)
            if digest != t.base_sha:
                problem("edited", f"the first {t.base_len} bytes are not what seq {t.base_seq} "
                                  f"recorded", t.base_seq, name)
        for offset, length, sha, seq in t.segments:
            if offset + length > size:
                continue  # reported as a truncation below
            f.seek(offset)
            digest, _ = _hash_stream(f, length)
            if digest != sha:
                problem("edited", f"bytes {offset}..{offset + length} are not what seq {seq} "
                                  f"appended", seq, name)
    if size > t.end:
        problem("appended outside Prax", f"{size} bytes, but Prax wrote {t.end}: "
                                         f"{size - t.end} were added outside Prax", None, name)
    elif size < t.end:
        problem("truncated", f"{size} bytes, but Prax wrote {t.end}", None, name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _journal_head(root: Path) -> dict:
    st = _State(root=root)
    jpath = root / JOURNAL
    if jpath.is_file():
        with open(jpath, "rb") as fh:
            _sync(st, fh)
    return {"seq": st.seq, "hash": st.hash}


def _print_report(result: dict, notes: list[str]) -> None:
    head = result["head"]
    print(f"records: {result['records']}")
    print(f"journal: {result['entries']} entries, head seq={head['seq']} hash={head['hash']}")
    print(f"files covered: {result['files']}; anchors checked: {result['anchors']}")
    by_source: dict[str, int] = {}
    for a in result["adopted"]:
        by_source[a["source"]] = by_source.get(a["source"], 0) + 1
    if by_source:
        print("adopted: " + ", ".join(f"{n} {s}" for s, n in sorted(by_source.items())))
    # Moved in from the workspace: the sandbox can write those old places, so
    # a legacy adopt long after the move deserves a look.
    legacy = [a for a in result["adopted"] if a["source"] == "legacy"]
    for a in legacy[:20]:
        print(f"  legacy: seq {a['seq']} {a['ts']} {a['file']}")
    if len(legacy) > 20:
        print(f"  ... and {len(legacy) - 20} more")
    if result["deleted"]:
        print(f"deleted by retention: {len(result['deleted'])} file(s)")
    for note in notes:
        print(note)
    if result["ok"]:
        print("OK: every record is as the journal says Prax left it.")
        return
    print(f"PROBLEMS ({len(result['problems'])}):")
    for p in result["problems"]:
        where = " ".join(x for x in (f"seq {p['seq']}" if p["seq"] is not None else "",
                                     p["file"] or "") if x)
        print(f"  [{p['kind']}] {where + ': ' if where else ''}{p['detail']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m prax.services.record_chain",
        description="Check Prax's records against their hash-chained journal.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check the records, the journal and any anchors")
    v.add_argument("--records", help="the records directory (default: RECORDS_DIR)")
    v.add_argument("--anchors-from", metavar="FILE|-",
                   help="text with RECORD-CHAIN-HEAD lines, such as `journalctl -u prax` "
                        "output; - reads stdin")
    v.add_argument("--anchor-records", metavar="DIR",
                   help="use head lines written for this directory (when checking a copy)")
    v.add_argument("--json", action="store_true", help="print the result as JSON")
    h = sub.add_parser("head", help="print the journal's head as a head line")
    h.add_argument("--records", help="the records directory (default: RECORDS_DIR)")
    args = ap.parse_args(argv)

    root = Path(args.records).resolve() if args.records else _records_root()
    if args.cmd == "head":
        head = _journal_head(root)
        print(head_line(head["seq"], head["hash"], root))
        return 0

    anchors: list[tuple[int, str]] = []
    notes: list[str] = []
    extra: list[dict] = []
    if args.anchors_from:
        if args.anchors_from == "-":
            text = sys.stdin.read()
        else:
            text = Path(args.anchors_from).read_text(encoding="utf-8", errors="replace")
        lines = parse_head_lines(text)
        target = Path(args.anchor_records) if args.anchor_records else root
        anchors = parse_anchors(text, target)
        others = sorted({h["records"] for h in lines if h["records"]
                         and not _same_dir(h["records"], target)})
        if others:
            notes.append(f"ignored head lines for other records directories: {', '.join(others)}")
        if not anchors:
            extra.append({"kind": "anchor", "seq": None, "file": None,
                          "detail": f"no head lines for {target} in the anchor input"
                                    + (" (use --anchor-records to check a copy)" if others else "")})

    result = verify(root, anchors)
    if extra:
        result["problems"].extend(extra)
        result["ok"] = False
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        _print_report(result, notes)
    return 0 if result["ok"] else 1


def _reset_for_tests() -> None:
    with _lock:
        _states.clear()


if __name__ == "__main__":
    sys.exit(main())
