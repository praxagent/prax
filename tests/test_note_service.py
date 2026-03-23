"""Tests for prax.services.note_service."""
import pytest

from prax.services import note_service


@pytest.fixture(autouse=True)
def _patch_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        note_service, "_ensure_workspace", lambda uid: str(workspace)
    )
    monkeypatch.setattr(note_service, "_get_lock", _FakeLock)
    monkeypatch.setattr(note_service, "_git_commit", lambda *a, **kw: None)
    return workspace


class _FakeLock:
    def __init__(self, *a):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class TestCreateNote:
    def test_creates_note_file(self, tmp_path):
        meta = note_service.create_note("u1", "My First Note", "Hello world")
        assert meta["slug"] == "my-first-note"
        assert meta["title"] == "My First Note"
        assert meta["content"] == "Hello world"

    def test_deduplicates_slug(self):
        note_service.create_note("u1", "Duplicate", "first")
        meta2 = note_service.create_note("u1", "Duplicate", "second")
        assert meta2["slug"] == "duplicate-2"

    def test_tags_preserved(self):
        meta = note_service.create_note("u1", "Tagged", "content", ["math", "physics"])
        assert meta["tags"] == ["math", "physics"]


class TestUpdateNote:
    def test_updates_content(self):
        meta = note_service.create_note("u1", "Note", "old content")
        updated = note_service.update_note("u1", meta["slug"], content="new content")
        assert updated["content"] == "new content"
        assert updated["title"] == "Note"

    def test_updates_title(self):
        meta = note_service.create_note("u1", "Old Title", "content")
        updated = note_service.update_note("u1", meta["slug"], title="New Title")
        assert updated["title"] == "New Title"
        assert updated["content"] == "content"

    def test_updates_tags(self):
        meta = note_service.create_note("u1", "Note", "content", ["old"])
        updated = note_service.update_note("u1", meta["slug"], tags=["new", "tags"])
        assert updated["tags"] == ["new", "tags"]

    def test_preserves_created_at(self):
        meta = note_service.create_note("u1", "Note", "content")
        updated = note_service.update_note("u1", meta["slug"], content="changed")
        assert updated["created_at"] == meta["created_at"]

    def test_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            note_service.update_note("u1", "nonexistent", content="x")


class TestGetNote:
    def test_reads_note(self):
        note_service.create_note("u1", "Read Me", "the content", ["tag1"])
        note = note_service.get_note("u1", "read-me")
        assert note["title"] == "Read Me"
        assert note["content"] == "the content"
        assert note["tags"] == ["tag1"]

    def test_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            note_service.get_note("u1", "nope")


class TestListNotes:
    def test_empty_list(self):
        assert note_service.list_notes("u1") == []

    def test_lists_all(self):
        note_service.create_note("u1", "Alpha", "a")
        note_service.create_note("u1", "Beta", "b")
        notes = note_service.list_notes("u1")
        assert len(notes) == 2
        titles = {n["title"] for n in notes}
        assert titles == {"Alpha", "Beta"}

    def test_no_content_in_list(self):
        note_service.create_note("u1", "X", "secret content")
        notes = note_service.list_notes("u1")
        assert "content" not in notes[0]


class TestSearchNotes:
    def test_searches_content(self):
        note_service.create_note("u1", "Math", "eigenvalues and eigenvectors")
        note_service.create_note("u1", "Cooking", "how to make pasta")
        results = note_service.search_notes("u1", "eigen")
        assert len(results) == 1
        assert results[0]["slug"] == "math"

    def test_searches_title(self):
        note_service.create_note("u1", "Docker Networking", "content")
        results = note_service.search_notes("u1", "docker")
        assert len(results) == 1

    def test_searches_tags(self):
        note_service.create_note("u1", "Note", "content", ["linear-algebra"])
        results = note_service.search_notes("u1", "linear-algebra")
        assert len(results) == 1

    def test_no_results(self):
        note_service.create_note("u1", "Note", "content")
        results = note_service.search_notes("u1", "nonexistent")
        assert results == []


class TestHugoGeneration:
    def test_generates_content_files(self, _patch_workspace):
        workspace = _patch_workspace
        note_service.create_note("u1", "Test Note", "Hello **world**", ["demo"])

        # Manually create the Hugo site dir structure.
        site_dir = workspace / "courses" / "_site"
        site_dir.mkdir(parents=True, exist_ok=True)

        note_service._generate_hugo_notes(str(workspace))

        notes_dir = site_dir / "content" / "notes"
        assert (notes_dir / "_index.md").exists()
        assert (notes_dir / "test-note.md").exists()

        content = (notes_dir / "test-note.md").read_text()
        assert "Hello **world**" in content
        assert 'title: "Test Note"' in content

    def test_writes_layout_templates(self, _patch_workspace):
        workspace = _patch_workspace
        note_service.create_note("u1", "X", "Y")

        site_dir = workspace / "courses" / "_site"
        site_dir.mkdir(parents=True, exist_ok=True)

        note_service._generate_hugo_notes(str(workspace))

        layouts = site_dir / "layouts" / "notes"
        assert (layouts / "list.html").exists()
        assert (layouts / "single.html").exists()
        assert "search" in (layouts / "list.html").read_text().lower()
        assert "katex" in (layouts / "single.html").read_text().lower()
