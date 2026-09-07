"""source_read / source_list / source_grep containment.

Review 2026-09-05: the project-root check was ``str(abs_path).startswith(
str(_PROJECT_ROOT))`` — a string prefix, so a sibling checkout whose name
merely starts with the root's name (``../prax-evals``, the held-out eval
data next to ``prax``) passed. And source_grep searched every directory
under the root, including ``workspaces/`` (users' private data), logs and
session state, whatever the model-controlled ``file_glob``. Scratch tree
here: ``<tmp>/prax`` as the root, ``<tmp>/prax-evals`` as the sibling.
"""
from __future__ import annotations

import pytest

from prax.agent import plugin_tools as pt


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "prax"
    (root / "prax").mkdir(parents=True)
    (root / "prax" / "ok.py").write_text("NEEDLE = 1\n", encoding="utf-8")
    (root / "README.md").write_text("inside the root\n", encoding="utf-8")
    for rel, text in (
        ("workspaces/usr_x/notes.md", "NEEDLE private user data\n"),
        ("app.log", "NEEDLE in a log\n"),
        ("checkpoint.md", "NEEDLE in the checkpoint\n"),
        (".local-run/prax.log", "NEEDLE local-run\n"),
        (".venv/lib/x.py", "NEEDLE = 'venv'\n"),
        ("node_modules/x.js", "NEEDLE = 'nm'\n"),
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    sibling = tmp_path / "prax-evals"
    sibling.mkdir()
    (sibling / "README.md").write_text("HELD-OUT ANSWERS\n", encoding="utf-8")
    monkeypatch.setattr(pt, "_PROJECT_ROOT", root.resolve())
    return root, sibling


@pytest.mark.parametrize("path", [
    "../prax-evals/README.md",   # sibling sharing the root's name prefix
    "../prax-evals",
    "/etc/hostname",             # absolute path is not joined, it replaces
    "prax/../../prax-evals/README.md",
])
def test_source_read_refuses_outside_root(tree, path):
    out = pt.source_read.invoke({"path": path})
    assert "Path traversal blocked" in out
    assert "HELD-OUT" not in out


@pytest.mark.parametrize("path", ["../prax-evals", "..", "/etc"])
def test_source_list_refuses_outside_root(tree, path):
    out = pt.source_list.invoke({"path": path})
    assert "Path traversal blocked" in out


def test_source_read_and_list_inside_root(tree):
    assert "inside the root" in pt.source_read.invoke({"path": "README.md"})
    assert "ok.py" in pt.source_list.invoke({"path": "prax"})
    assert "NEEDLE" in pt.source_read.invoke({"path": "prax/ok.py"})


def test_source_grep_never_sees_runtime_dirs_or_logs(tree):
    """Real grep against the scratch tree with the widest possible glob."""
    out = pt.source_grep.invoke({"pattern": "NEEDLE", "file_glob": "*"})
    assert "prax/ok.py" in out
    for forbidden in (
        "workspaces", "private user data", "app.log", "checkpoint.md",
        ".local-run", ".venv", "node_modules",
    ):
        assert forbidden not in out, forbidden


def test_source_grep_wires_exclusions_regardless_of_glob(monkeypatch):
    captured = {}

    class _Res:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(__import__("subprocess"), "run",
                        lambda cmd, **kw: captured.setdefault("cmd", cmd) and _Res())
    pt.source_grep.invoke({"pattern": "x", "file_glob": "*.log"})
    cmd = captured["cmd"]

    def _has(flag, value):
        # "*.log" is also the --include argument here — the exclusion must be
        # present in addition to it (grep lets --exclude win).
        return any(cmd[i - 1] == flag for i, v in enumerate(cmd) if i and v == value)

    assert _has("--include", "*.log")
    for d in ("workspaces", ".local-run", ".venv", "node_modules"):
        assert _has("--exclude-dir", d), d
    for g in ("*.log", "checkpoint.md"):
        assert _has("--exclude", g), g
