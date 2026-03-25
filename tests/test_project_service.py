"""Tests for prax.services.project_service."""

import pytest

from prax.services import note_service, project_service


class _FakeLock:
    def __init__(self, *a):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture(autouse=True)
def _patch_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Patch project_service workspace helpers.
    monkeypatch.setattr(
        project_service, "ensure_workspace", lambda uid: str(workspace),
    )
    monkeypatch.setattr(project_service, "get_lock", _FakeLock)
    monkeypatch.setattr(project_service, "git_commit", lambda *a, **kw: None)

    # Patch note_service workspace helpers (used by generate_project_brief).
    monkeypatch.setattr(
        note_service, "ensure_workspace", lambda uid: str(workspace),
    )
    monkeypatch.setattr(note_service, "get_lock", _FakeLock)
    monkeypatch.setattr(note_service, "git_commit", lambda *a, **kw: None)

    return workspace


class TestCreateProject:
    def test_creates_project(self):
        data = project_service.create_project("u1", "Bayesian Reasoning Research")
        assert data["id"] == "bayesian-reasoning-research"
        assert data["title"] == "Bayesian Reasoning Research"
        assert data["status"] == "active"
        assert data["notes"] == []
        assert data["links"] == []
        assert data["sources"] == []
        assert "created_at" in data
        assert "updated_at" in data

    def test_creates_with_description(self):
        data = project_service.create_project(
            "u1", "My Project", description="Exploring stuff",
        )
        assert data["description"] == "Exploring stuff"

    def test_duplicate_slug_handling(self):
        d1 = project_service.create_project("u1", "Duplicate")
        d2 = project_service.create_project("u1", "Duplicate")
        assert d1["id"] == "duplicate"
        assert d2["id"] == "duplicate-2"

    def test_project_dir_created(self, _patch_workspace):
        workspace = _patch_workspace
        project_service.create_project("u1", "Test Dir")
        project_dir = workspace / "projects" / "test-dir"
        assert project_dir.is_dir()
        assert (project_dir / "project.yaml").is_file()


class TestGetProject:
    def test_reads_project(self):
        project_service.create_project("u1", "Readable")
        data = project_service.get_project("u1", "readable")
        assert data["title"] == "Readable"
        assert data["status"] == "active"

    def test_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            project_service.get_project("u1", "nonexistent")


class TestListProjects:
    def test_empty_list(self):
        assert project_service.list_projects("u1") == []

    def test_lists_all(self):
        project_service.create_project("u1", "Alpha")
        project_service.create_project("u1", "Beta")
        projects = project_service.list_projects("u1")
        assert len(projects) == 2
        titles = {p["title"] for p in projects}
        assert titles == {"Alpha", "Beta"}

    def test_summary_fields(self):
        project_service.create_project("u1", "Summary Test")
        projects = project_service.list_projects("u1")
        p = projects[0]
        assert "id" in p
        assert "title" in p
        assert "status" in p
        assert "notes_count" in p
        assert "links_count" in p
        assert "sources_count" in p


class TestUpdateProject:
    def test_deep_merge(self):
        project_service.create_project("u1", "Updatable")
        updated = project_service.update_project(
            "u1", "updatable", {"status": "completed", "description": "Done"},
        )
        assert updated["status"] == "completed"
        assert updated["description"] == "Done"
        assert updated["title"] == "Updatable"  # unchanged


class TestAddNote:
    def test_adds_note(self):
        project_service.create_project("u1", "Notes Project")
        data = project_service.add_note_to_project(
            "u1", "notes-project", "my-note-slug",
        )
        assert "my-note-slug" in data["notes"]

    def test_deduplicates_notes(self):
        project_service.create_project("u1", "Dedup Notes")
        project_service.add_note_to_project("u1", "dedup-notes", "note-a")
        data = project_service.add_note_to_project("u1", "dedup-notes", "note-a")
        assert data["notes"].count("note-a") == 1

    def test_multiple_notes(self):
        project_service.create_project("u1", "Multi Notes")
        project_service.add_note_to_project("u1", "multi-notes", "note-a")
        data = project_service.add_note_to_project("u1", "multi-notes", "note-b")
        assert data["notes"] == ["note-a", "note-b"]


class TestAddLink:
    def test_adds_link(self):
        project_service.create_project("u1", "Links Project")
        data = project_service.add_link_to_project(
            "u1", "links-project", "https://example.com", "Example",
        )
        assert len(data["links"]) == 1
        assert data["links"][0]["url"] == "https://example.com"
        assert data["links"][0]["title"] == "Example"
        assert "added_at" in data["links"][0]

    def test_adds_link_without_title(self):
        project_service.create_project("u1", "No Title Link")
        data = project_service.add_link_to_project(
            "u1", "no-title-link", "https://example.com",
        )
        assert data["links"][0]["title"] == ""

    def test_multiple_links(self):
        project_service.create_project("u1", "Multi Links")
        project_service.add_link_to_project("u1", "multi-links", "https://a.com")
        data = project_service.add_link_to_project("u1", "multi-links", "https://b.com")
        assert len(data["links"]) == 2


class TestAddSource:
    def test_saves_source_file(self, _patch_workspace):
        workspace = _patch_workspace
        project_service.create_project("u1", "Source Project")
        data = project_service.add_source_to_project(
            "u1", "source-project", "paper.md", "# Paper Content\nHello",
        )
        assert "paper.md" in data["sources"]
        filepath = workspace / "projects" / "source-project" / "paper.md"
        assert filepath.is_file()
        assert filepath.read_text() == "# Paper Content\nHello"

    def test_deduplicates_source_list(self):
        project_service.create_project("u1", "Dedup Source")
        project_service.add_source_to_project(
            "u1", "dedup-source", "file.txt", "v1",
        )
        data = project_service.add_source_to_project(
            "u1", "dedup-source", "file.txt", "v2",
        )
        assert data["sources"].count("file.txt") == 1


class TestGenerateBrief:
    def test_generates_brief_with_notes(self):
        # Create a note first.
        note_service.create_note("u1", "My Research Note", "This is the note content.")

        # Create project and link the note.
        project_service.create_project(
            "u1", "Brief Project", description="Testing briefs",
        )
        project_service.add_note_to_project("u1", "brief-project", "my-research-note")

        brief = project_service.generate_project_brief("u1", "brief-project")
        assert "# Brief Project" in brief
        assert "Testing briefs" in brief
        assert "My Research Note" in brief
        assert "This is the note content." in brief

    def test_generates_brief_with_links(self):
        project_service.create_project("u1", "Link Brief")
        project_service.add_link_to_project(
            "u1", "link-brief", "https://example.com", "Example Site",
        )
        brief = project_service.generate_project_brief("u1", "link-brief")
        assert "https://example.com" in brief
        assert "Example Site" in brief

    def test_generates_brief_with_sources(self):
        project_service.create_project("u1", "Source Brief")
        project_service.add_source_to_project(
            "u1", "source-brief", "data.txt", "Source file content here",
        )
        brief = project_service.generate_project_brief("u1", "source-brief")
        assert "data.txt" in brief
        assert "Source file content here" in brief

    def test_brief_handles_missing_note(self):
        project_service.create_project("u1", "Missing Note Brief")
        project_service.add_note_to_project(
            "u1", "missing-note-brief", "nonexistent-note",
        )
        brief = project_service.generate_project_brief("u1", "missing-note-brief")
        assert "not found" in brief.lower()

    def test_nonexistent_project_raises(self):
        with pytest.raises(FileNotFoundError):
            project_service.generate_project_brief("u1", "nonexistent")

    def test_empty_project_brief(self):
        project_service.create_project("u1", "Empty Brief")
        brief = project_service.generate_project_brief("u1", "empty-brief")
        assert "# Empty Brief" in brief
