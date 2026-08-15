"""A reader must never observe a partially-written workspace file.

# INVARIANT: a concurrent reader of a workspace file observes either the
# complete previous content or the complete new content — never a splice, and
# never a truncated file if the process dies mid-write.

Grounded in #64, and in a correction to that report worth recording. The
original said workspace writes had "no threading.Lock ... verified by grep" —
but the grep looked at `workspace_tools.py`, while the lock lives in
`workspace_service.py`. Seventeen write operations DO take `get_lock(user_id)`,
so concurrent writers are serialised and cannot interleave their bytes.

What the lock does not do is make a write ATOMIC FOR READERS. `read_file`
deliberately takes no lock (nor does the user's file browser), and
`open(path, "w")` truncates the target before streaming into it — so there is a
window where the file on disk is real, readable, and wrong. Serialising writers
does not close that window; renaming over the target does.

The second hazard the lock cannot help with: a crash or exception mid-write
leaves the file truncated, because the truncation already happened.
"""

import os
import threading
import time

import pytest

from prax.services.workspace_service import atomic_write


class TestAtomicity:
    def test_reader_never_sees_a_splice(self, tmp_path):
        """The load-bearing test. Hammer a file with alternating long writes
        while reading continuously; every observed value must be one of the two
        complete contents."""
        target = tmp_path / "notes.md"
        a = "A" * 200_000
        b = "B" * 200_000
        target.write_text(a, encoding="utf-8")

        seen: list[str] = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    seen.append(target.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    seen.append("<MISSING>")

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        for i in range(40):
            atomic_write(str(target), b if i % 2 else a)
            time.sleep(0.001)
        stop.set()
        t.join(timeout=5)

        assert seen, "reader never ran"
        bad = [s for s in set(seen) if s not in (a, b)]
        assert not bad, (
            f"reader observed {len(bad)} value(s) that were neither the old nor "
            f"the new content (lengths: {sorted(len(x) for x in bad)[:5]})")

    def test_file_is_never_missing_during_replace(self, tmp_path):
        """os.replace is a rename, not delete-then-create, so the path always
        resolves. A writer that unlinked first would fail this."""
        target = tmp_path / "f.txt"
        target.write_text("original", encoding="utf-8")
        for _ in range(50):
            atomic_write(str(target), "x" * 5000)
            assert target.exists()


class TestCorrectness:
    def test_text_roundtrip(self, tmp_path):
        p = tmp_path / "a.md"
        atomic_write(str(p), "héllo wörld")
        assert p.read_text(encoding="utf-8") == "héllo wörld"

    def test_binary_roundtrip(self, tmp_path):
        p = tmp_path / "a.bin"
        payload = bytes(range(256)) * 10
        atomic_write(str(p), payload)
        assert p.read_bytes() == payload

    def test_creates_parent_directories(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "a.txt"
        atomic_write(str(p), "ok")
        assert p.read_text(encoding="utf-8") == "ok"

    def test_overwrites_existing(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("old", encoding="utf-8")
        atomic_write(str(p), "new")
        assert p.read_text(encoding="utf-8") == "new"


class TestNoDebris:
    def test_no_temp_files_left_on_success(self, tmp_path):
        p = tmp_path / "a.txt"
        for _ in range(10):
            atomic_write(str(p), "content")
        assert [f.name for f in tmp_path.iterdir()] == ["a.txt"]

    def test_no_temp_files_left_on_failure(self, tmp_path, monkeypatch):
        """A failed write must not litter the user's workspace with .tmp files
        — they would show up in their file browser and in git status."""
        p = tmp_path / "a.txt"
        p.write_text("original", encoding="utf-8")

        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            atomic_write(str(p), "new content")

        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "a.txt"]
        assert not leftovers, f"debris left behind: {leftovers}"

    def test_original_survives_a_failed_write(self, tmp_path, monkeypatch):
        """The other half of the same guarantee: a failure leaves the previous
        content intact, where truncate-then-write would have destroyed it."""
        p = tmp_path / "a.txt"
        p.write_text("original", encoding="utf-8")
        monkeypatch.setattr(os, "replace",
                            lambda *_a, **_k: (_ for _ in ()).throw(OSError("nope")))
        with pytest.raises(OSError):
            atomic_write(str(p), "new content")
        assert p.read_text(encoding="utf-8") == "original"


def test_temp_file_is_created_in_the_target_directory(tmp_path, monkeypatch):
    """os.replace is only atomic within a filesystem. A temp in /tmp can land on
    a different mount and silently degrade to a copy."""
    target = tmp_path / "sub" / "a.txt"
    target.parent.mkdir(parents=True)
    seen_dirs: list[str] = []
    import tempfile as _tempfile

    real_mkstemp = _tempfile.mkstemp

    def spy(*args, **kwargs):
        seen_dirs.append(kwargs.get("dir"))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(_tempfile, "mkstemp", spy)
    atomic_write(str(target), "ok")
    assert seen_dirs == [str(target.parent)]


def test_save_file_uses_atomic_write(tmp_path, monkeypatch):
    """Pin the wiring: the public entry point must go through it, or the
    guarantee above protects nothing anyone calls."""
    import prax.services.workspace_service as ws

    calls: list[str] = []
    monkeypatch.setattr(ws, "atomic_write",
                        lambda path, content: calls.append(path))
    monkeypatch.setattr(ws, "ensure_workspace", lambda uid: str(tmp_path))
    monkeypatch.setattr(ws, "git_commit", lambda *a, **k: None)
    (tmp_path / "active").mkdir(parents=True, exist_ok=True)

    ws.save_file("u1", "note.md", "content")
    assert calls and calls[0].endswith("note.md")
