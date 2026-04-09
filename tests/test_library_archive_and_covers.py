"""Tests for library archive + space cover images.

Covers:

- ``library_service.archive_capture`` / ``list_archive`` / ``get_archive``
  / ``delete_archive`` — the long-term document keeper.
- ``library_service.save_space_cover`` / ``get_space_cover_path`` /
  ``delete_space_cover`` — the cover image storage.
- ``library_service.generate_space_cover`` graceful error path when
  no API key is configured (we don't hit the real image API in tests).
- ``library_service.get_tree`` returning ``{"spaces": ...}`` after
  the rename.
- ``ensure_library`` now creates ``library/archive/``.
"""
from __future__ import annotations

import pytest

from prax.services import library_service, workspace_service

USER = "test_archive_user"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(
        library_service, "workspace_root", lambda uid: str(tmp_path / uid),
    )
    (tmp_path / USER).mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

class TestArchive:
    def test_ensure_library_creates_archive_dir(self, ws):
        root = library_service.ensure_library(USER)
        assert (root / "archive").is_dir()

    def test_capture_roundtrip(self, ws):
        result = library_service.archive_capture(
            USER,
            title="Buffer Overflow Paper",
            content="# Classic stack buffer overflow\n\nSome content here.",
            source_url="https://example.com/paper",
            source_filename="paper.pdf",
            tags=["security", "paper"],
        )
        assert result["status"] == "archived"
        slug = result["archive"]["slug"]
        assert "buffer-overflow-paper" in slug

        fetched = library_service.get_archive(USER, slug)
        assert fetched is not None
        assert "Classic stack buffer overflow" in fetched["content"]
        assert fetched["meta"]["title"] == "Buffer Overflow Paper"
        assert fetched["meta"]["source_url"] == "https://example.com/paper"
        assert fetched["meta"]["source_filename"] == "paper.pdf"
        assert fetched["meta"]["tags"] == ["security", "paper"]
        assert fetched["meta"]["kind"] == "archive"

    def test_list_archive_newest_first(self, ws):
        library_service.archive_capture(USER, title="Alpha", content="a")
        library_service.archive_capture(USER, title="Beta", content="b")
        library_service.archive_capture(USER, title="Gamma", content="c")

        items = library_service.list_archive(USER)
        assert len(items) == 3
        titles = [it["title"] for it in items]
        assert "Alpha" in titles
        assert "Beta" in titles
        assert "Gamma" in titles
        # Newest first — Gamma was captured last
        assert items[0]["title"] == "Gamma"

    def test_delete_archive(self, ws):
        result = library_service.archive_capture(USER, title="Temp", content="x")
        slug = result["archive"]["slug"]

        del_result = library_service.delete_archive(USER, slug)
        assert del_result["status"] == "deleted"
        assert library_service.get_archive(USER, slug) is None

    def test_delete_archive_not_found(self, ws):
        library_service.ensure_library(USER)
        result = library_service.delete_archive(USER, "nonexistent-slug")
        assert "error" in result


# ---------------------------------------------------------------------------
# Space cover images
# ---------------------------------------------------------------------------

# Tiny valid PNG (1x1 red pixel).  Plenty for testing the save/load
# path without needing Pillow or a real image file.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x8b\xef\xc7\x16\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestSpaceCover:
    def _setup(self, ws):
        library_service.create_space(USER, "My Space")

    def test_save_and_fetch_cover(self, ws):
        self._setup(ws)
        result = library_service.save_space_cover(USER, "my-space", _TINY_PNG, "png")
        assert result["status"] == "saved"
        assert result["filename"] == ".cover.png"

        path = library_service.get_space_cover_path(USER, "my-space")
        assert path is not None
        assert path.exists()
        assert path.read_bytes() == _TINY_PNG

    def test_cover_shows_in_get_space(self, ws):
        self._setup(ws)
        library_service.save_space_cover(USER, "my-space", _TINY_PNG, "png")
        meta = library_service.get_space(USER, "my-space")
        assert meta is not None
        assert meta.get("cover_image") == ".cover.png"

    def test_cover_shows_in_list_spaces(self, ws):
        self._setup(ws)
        library_service.save_space_cover(USER, "my-space", _TINY_PNG, "png")
        spaces = library_service.list_spaces(USER)
        s = next(s for s in spaces if s["slug"] == "my-space")
        assert s.get("cover_image") == ".cover.png"

    def test_save_replaces_existing_cover(self, ws):
        self._setup(ws)
        library_service.save_space_cover(USER, "my-space", _TINY_PNG, "png")
        # Upload a different extension — old .png should be removed
        library_service.save_space_cover(USER, "my-space", _TINY_PNG, "jpg")
        meta = library_service.get_space(USER, "my-space")
        assert meta["cover_image"] == ".cover.jpg"
        # Only the new one exists on disk
        from pathlib import Path
        space_dir = Path(str(ws / USER / "library" / "spaces" / "my-space"))
        assert (space_dir / ".cover.jpg").exists()
        assert not (space_dir / ".cover.png").exists()

    def test_unknown_extension_rejected(self, ws):
        self._setup(ws)
        result = library_service.save_space_cover(USER, "my-space", b"data", "bmp")
        assert "error" in result

    def test_delete_cover(self, ws):
        self._setup(ws)
        library_service.save_space_cover(USER, "my-space", _TINY_PNG, "png")
        result = library_service.delete_space_cover(USER, "my-space")
        assert result["status"] == "deleted"
        assert library_service.get_space_cover_path(USER, "my-space") is None

    def test_delete_cover_missing(self, ws):
        self._setup(ws)
        # No cover set
        result = library_service.delete_space_cover(USER, "my-space")
        assert "error" in result

    def test_get_cover_path_no_space(self, ws):
        assert library_service.get_space_cover_path(USER, "nope") is None

    def test_generate_cover_graceful_without_key(self, ws, monkeypatch):
        """When OPENAI_KEY is empty, generation returns an error dict
        instead of crashing."""
        self._setup(ws)
        from prax import settings as settings_module
        monkeypatch.setattr(settings_module.settings, "openai_key", None)
        result = library_service.generate_space_cover(USER, "my-space")
        assert "error" in result
        assert "OPENAI_KEY" in result["error"]

    def test_generate_cover_missing_space(self, ws, monkeypatch):
        from prax import settings as settings_module
        monkeypatch.setattr(settings_module.settings, "openai_key", "sk-fake")
        result = library_service.generate_space_cover(USER, "does-not-exist")
        assert "error" in result


# ---------------------------------------------------------------------------
# Tree shape — rename sanity check
# ---------------------------------------------------------------------------

class TestTreeShape:
    def test_tree_uses_spaces_key(self, ws):
        library_service.create_space(USER, "Space A")
        library_service.create_space(USER, "Space B")
        tree = library_service.get_tree(USER)
        assert "spaces" in tree
        assert "projects" not in tree
        assert len(tree["spaces"]) == 2
