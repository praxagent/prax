"""Prax must be able to read a file you put in a space.

A PDF uploaded through the Files tab was invisible. Asked to read it, Prax
searched notebooks, notes, the archive, outputs, attached repos and the inbox,
found nothing, and honestly reported that no PDF existed — while the file sat in
library/spaces/<slug>/files/, a directory no agent tool could look in.

The storage and the HTTP API both existed. Only the tool was missing, which is
the worst version of this: everything looks present, the agent is behaving
correctly, and the feature simply does not work.
"""
from __future__ import annotations

import pytest

from prax.agent import library_tools as lt


@pytest.fixture
def space(tmp_path, monkeypatch):
    from prax.services import library_service

    files = tmp_path / "spaces" / "work" / "files"
    files.mkdir(parents=True)
    monkeypatch.setattr(library_service, "_files_dir",
                        lambda uid, sp: files, raising=False)
    monkeypatch.setattr(lt, "_uid", lambda: "u1", raising=False)
    return files


def test_listing_finds_an_uploaded_file(space):
    (space / "notes.txt").write_text("hello from the files tab")
    out = lt.library_files_list.invoke({"space_slug": "work"})
    assert "notes.txt" in out


def test_an_empty_space_says_so_rather_than_erroring(space):
    out = lt.library_files_list.invoke({"space_slug": "work"})
    assert "No files" in out


def test_a_text_file_is_read_back(space):
    (space / "notes.md").write_text("# Topology\n\nchaos is not randomness")
    out = lt.library_file_read.invoke({"space_slug": "work", "filename": "notes.md"})
    assert "chaos is not randomness" in out


def test_a_missing_file_shows_what_is_actually_there(space):
    """'Not found' alone leaves the agent guessing at the filename."""
    (space / "real.txt").write_text("x")
    out = lt.library_file_read.invoke(
        {"space_slug": "work", "filename": "imagined.pdf"})
    assert "No file named" in out
    assert "real.txt" in out, "the listing should be included so it can retry"


def test_a_binary_is_named_not_returned_as_garbage(space):
    """An agent shown mojibake will try to interpret it."""
    (space / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    out = lt.library_file_read.invoke(
        {"space_slug": "work", "filename": "logo.png"})
    assert "cannot" in out and "text" in out


def test_a_corrupt_pdf_reports_rather_than_raises(space):
    (space / "broken.pdf").write_bytes(b"not really a pdf")
    out = lt.library_file_read.invoke(
        {"space_slug": "work", "filename": "broken.pdf"})
    assert "Could not open the PDF" in out or "No extractable text" in out


def test_the_tools_are_registered_or_they_may_as_well_not_exist():
    names = {getattr(t, "name", "") for t in lt.build_library_tools()}
    assert {"library_files_list", "library_file_read"} <= names
