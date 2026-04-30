"""Tests for prax.services.library_service."""
from __future__ import annotations

from pathlib import Path

import pytest

from prax.services import library_service


@pytest.fixture
def user(tmp_path, monkeypatch):
    """Create an isolated user workspace and patch workspace_root to return it."""
    user_id = "test_user"
    ws = tmp_path / user_id
    ws.mkdir()
    monkeypatch.setattr(
        library_service, "workspace_root", lambda _uid: str(ws),
    )
    return user_id


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class TestProjects:
    def test_create_project(self, user):
        result = library_service.create_space(user, "Personal")
        assert result["status"] == "created"
        assert result["project"]["slug"] == "personal"
        assert result["project"]["name"] == "Personal"

    def test_project_slug_normalizes(self, user):
        result = library_service.create_space(user, "Q2 Marketing!!!")
        assert result["project"]["slug"] == "q2-marketing"

    def test_duplicate_project_rejected(self, user):
        library_service.create_space(user, "Business")
        result = library_service.create_space(user, "Business")
        assert "error" in result
        assert "already exists" in result["error"]

    def test_list_projects_empty(self, user):
        assert library_service.list_spaces(user) == []

    def test_list_projects_with_notebook_count(self, user):
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "school", "Quantum Computing")
        library_service.create_notebook(user, "school", "Data Structures")
        projects = library_service.list_spaces(user)
        assert len(projects) == 1
        assert projects[0]["notebook_count"] == 2

    def test_delete_empty_project(self, user):
        library_service.create_space(user, "Temp")
        result = library_service.delete_space(user, "temp")
        assert result["status"] == "deleted"

    def test_delete_nonempty_space_succeeds(self, user):
        """Deleting a space with notebooks works (removes everything)."""
        library_service.create_space(user, "Temp")
        library_service.create_notebook(user, "temp", "Something")
        result = library_service.delete_space(user, "temp")
        assert result["status"] == "deleted"
        assert library_service.get_space(user, "temp") is None

    def test_delete_space_with_archive(self, user):
        """archive_notes=True moves notes to archive before deleting."""
        library_service.create_space(user, "Archivable")
        library_service.create_notebook(user, "archivable", "Stuff")
        library_service.create_note(user, "Keep this", "content", "archivable", "stuff")
        result = library_service.delete_space(user, "archivable", archive_notes=True)
        assert result["status"] == "deleted"
        assert result["archived_notes"] == 1
        # The note should be in the archive now
        archive = library_service.list_archive(user)
        assert any(a["title"] == "Keep this" for a in archive)


# ---------------------------------------------------------------------------
# Notebooks
# ---------------------------------------------------------------------------

class TestNotebooks:
    def test_create_notebook_in_project(self, user):
        library_service.create_space(user, "Personal")
        result = library_service.create_notebook(user, "personal", "Health")
        assert result["status"] == "created"
        assert result["notebook"]["slug"] == "health"
        assert result["notebook"]["project"] == "personal"

    def test_notebook_in_missing_project_rejected(self, user):
        result = library_service.create_notebook(user, "ghost", "Health")
        assert "error" in result

    def test_list_notebooks_all_projects(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_space(user, "Business")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_notebook(user, "business", "Marketing")
        notebooks = library_service.list_notebooks(user)
        assert len(notebooks) == 2
        slugs = {nb["slug"] for nb in notebooks}
        assert {"health", "marketing"} == slugs

    def test_list_notebooks_filtered_by_project(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_space(user, "Business")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_notebook(user, "business", "Marketing")
        notebooks = library_service.list_notebooks(user, project="personal")
        assert len(notebooks) == 1
        assert notebooks[0]["slug"] == "health"

    def test_delete_empty_notebook(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Temp")
        result = library_service.delete_notebook(user, "personal", "temp")
        assert result["status"] == "deleted"

    def test_delete_nonempty_notebook_refused(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Temp")
        library_service.create_note(user, "Hi", "body", "personal", "temp")
        result = library_service.delete_notebook(user, "personal", "temp")
        assert "error" in result


# ---------------------------------------------------------------------------
# Notes — basic CRUD
# ---------------------------------------------------------------------------

class TestNoteCRUD:
    def _setup(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")

    def test_create_note_defaults_to_prax_author(self, user):
        self._setup(user)
        result = library_service.create_note(user, "Sleep tips", "drink water", "personal", "health")
        assert result["status"] == "created"
        assert result["note"]["author"] == "prax"
        assert result["note"]["prax_may_edit"] is True  # prax can always edit its own work

    def test_create_note_as_human(self, user):
        self._setup(user)
        result = library_service.create_note(
            user, "My thoughts", "blah", "personal", "health", author="human",
        )
        assert result["note"]["author"] == "human"
        assert result["note"]["prax_may_edit"] is False  # safe default

    def test_create_note_in_missing_notebook(self, user):
        result = library_service.create_note(user, "Hi", "body", "ghost", "ghost")
        assert "error" in result

    def test_get_note(self, user):
        self._setup(user)
        library_service.create_note(user, "Sleep tips", "drink water", "personal", "health")
        note = library_service.get_note(user, "personal", "health", "sleep-tips")
        assert note is not None
        assert note["meta"]["title"] == "Sleep tips"
        assert "drink water" in note["content"]

    def test_get_missing_note(self, user):
        self._setup(user)
        assert library_service.get_note(user, "personal", "health", "ghost") is None

    def test_list_notes_scoped(self, user):
        self._setup(user)
        library_service.create_notebook(user, "personal", "Hobbies")
        library_service.create_note(user, "A", "a", "personal", "health")
        library_service.create_note(user, "B", "b", "personal", "hobbies")
        all_notes = library_service.list_notes(user)
        assert len(all_notes) == 2
        only_health = library_service.list_notes(user, project="personal", notebook="health")
        assert len(only_health) == 1
        assert only_health[0]["title"] == "A"

    def test_duplicate_title_gets_suffix(self, user):
        self._setup(user)
        library_service.create_note(user, "Notes", "one", "personal", "health")
        r2 = library_service.create_note(user, "Notes", "two", "personal", "health")
        assert r2["status"] == "created"
        assert r2["note"]["slug"] != "notes"
        assert r2["note"]["slug"].startswith("notes-")

    def test_delete_note(self, user):
        self._setup(user)
        library_service.create_note(user, "Hi", "body", "personal", "health")
        result = library_service.delete_note(user, "personal", "health", "hi")
        assert result["status"] == "deleted"
        assert library_service.get_note(user, "personal", "health", "hi") is None

    def test_move_note(self, user):
        self._setup(user)
        library_service.create_notebook(user, "personal", "Hobbies")
        library_service.create_note(user, "Hi", "body", "personal", "health")
        result = library_service.move_note(
            user, "personal", "health", "hi", "personal", "hobbies",
        )
        assert result["status"] == "moved"
        assert library_service.get_note(user, "personal", "health", "hi") is None
        assert library_service.get_note(user, "personal", "hobbies", "hi") is not None

    def test_move_note_to_missing_notebook(self, user):
        self._setup(user)
        library_service.create_note(user, "Hi", "body", "personal", "health")
        result = library_service.move_note(
            user, "personal", "health", "hi", "personal", "ghost",
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Notes — permission gate for human-authored notes
# ---------------------------------------------------------------------------

class TestPraxMayEditPermission:
    def _human_note(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Journal")
        library_service.create_note(
            user, "My thoughts", "initial", "personal", "journal", author="human",
        )

    def test_prax_cannot_edit_human_note_by_default(self, user):
        self._human_note(user)
        result = library_service.update_note(
            user, "personal", "journal", "my-thoughts",
            content="prax rewrote this", editor="prax",
        )
        assert "error" in result
        assert "prax_may_edit" in result["error"]

    def test_prax_can_edit_human_note_after_toggle(self, user):
        self._human_note(user)
        library_service.set_prax_may_edit(user, "personal", "journal", "my-thoughts", True)
        result = library_service.update_note(
            user, "personal", "journal", "my-thoughts",
            content="prax refined this", editor="prax",
        )
        assert result["status"] == "updated"
        assert result["note"]["last_edited_by"] == "prax"
        note = library_service.get_note(user, "personal", "journal", "my-thoughts")
        assert "prax refined this" in note["content"]

    def test_human_can_always_edit_own_note(self, user):
        self._human_note(user)
        result = library_service.update_note(
            user, "personal", "journal", "my-thoughts",
            content="I rewrote this myself", editor="human",
        )
        assert result["status"] == "updated"

    def test_prax_can_edit_prax_authored_note(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(
            user, "Sleep tips", "body", "personal", "health", author="prax",
        )
        result = library_service.update_note(
            user, "personal", "health", "sleep-tips",
            content="updated", editor="prax",
        )
        assert result["status"] == "updated"

    def test_override_permission_bypasses_gate(self, user):
        """The UI's 'refine this note' button passes override_permission=True
        because the human explicitly initiated the edit."""
        self._human_note(user)
        result = library_service.update_note(
            user, "personal", "journal", "my-thoughts",
            content="user asked prax to refine", editor="prax",
            override_permission=True,
        )
        assert result["status"] == "updated"


# ---------------------------------------------------------------------------
# Library skeleton
# ---------------------------------------------------------------------------

class TestLibrarySkeleton:
    def test_ensure_library_creates_dirs(self, user):
        root = library_service.ensure_library(user)
        assert (root / "raw").is_dir()
        assert (root / "archive").is_dir()
        assert (root / "outputs").is_dir()
        assert (root / "spaces").is_dir()
        assert (root / "LIBRARY.md").is_file()

    def test_library_md_references_karpathy(self, user):
        library_service.ensure_library(user)
        lib_md = Path(library_service.workspace_root(user)) / "library" / "LIBRARY.md"
        text = lib_md.read_text()
        assert "Karpathy" in text  # attribution is part of the design


# ---------------------------------------------------------------------------
# Tree endpoint
# ---------------------------------------------------------------------------

class TestGetTree:
    def test_tree_shape(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_space(user, "Business")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(user, "Sleep tips", "body", "personal", "health")

        tree = library_service.get_tree(user)
        assert "spaces" in tree
        assert len(tree["spaces"]) == 2

        personal = next(p for p in tree["spaces"] if p["slug"] == "personal")
        assert len(personal["notebooks"]) == 1
        assert personal["notebooks"][0]["slug"] == "health"
        assert len(personal["notebooks"][0]["notes"]) == 1
        assert personal["notebooks"][0]["notes"][0]["slug"] == "sleep-tips"


# ---------------------------------------------------------------------------
# Wikilinks extraction and backlinks lookup
# ---------------------------------------------------------------------------

class TestWikilinks:
    def test_extract_bare_wikilinks(self):
        body = "See [[sleep-optimization]] and also [[cbt-i]]."
        links = library_service.extract_wikilinks(body)
        assert links == ["sleep-optimization", "cbt-i"]

    def test_extract_qualified_wikilinks(self):
        body = "Links: [[personal/health/sleep]] and [[business/q2/pipeline]]"
        links = library_service.extract_wikilinks(body)
        assert "personal/health/sleep" in links
        assert "business/q2/pipeline" in links

    def test_extract_aliased_wikilinks(self):
        body = "Refer to [[sleep-basics|my sleep note]] for details."
        links = library_service.extract_wikilinks(body)
        assert links == ["sleep-basics"]

    def test_extract_dedupes(self):
        body = "[[foo]] and [[foo]] and [[bar]]"
        links = library_service.extract_wikilinks(body)
        assert links == ["foo", "bar"]

    def test_create_note_stores_wikilinks(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(
            user, "Sleep tips",
            "See [[cbt-i]] and [[relaxation]]",
            "personal", "health",
        )
        note = library_service.get_note(user, "personal", "health", "sleep-tips")
        assert note["meta"]["wikilinks"] == ["cbt-i", "relaxation"]

    def test_update_note_refreshes_wikilinks(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(user, "Sleep tips", "See [[cbt-i]]", "personal", "health")
        library_service.update_note(
            user, "personal", "health", "sleep-tips",
            content="See [[relaxation]] and [[meditation]]", editor="prax",
        )
        note = library_service.get_note(user, "personal", "health", "sleep-tips")
        assert note["meta"]["wikilinks"] == ["relaxation", "meditation"]


class TestBacklinks:
    def test_backlinks_for_linked_note(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(user, "CBT-I", "protocol", "personal", "health")
        library_service.create_note(
            user, "Sleep tips", "See [[cbt-i]] for more.", "personal", "health",
        )
        backlinks = library_service.get_backlinks(user, "personal", "health", "cbt-i")
        assert len(backlinks) == 1
        assert backlinks[0]["slug"] == "sleep-tips"

    def test_qualified_backlink_across_projects(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_notebook(user, "school", "Psychology")
        library_service.create_note(user, "CBT-I", "protocol", "personal", "health")
        library_service.create_note(
            user, "Therapy notes",
            "See [[personal/health/cbt-i]].",
            "school", "psychology",
        )
        backlinks = library_service.get_backlinks(user, "personal", "health", "cbt-i")
        assert len(backlinks) == 1
        assert backlinks[0]["project"] == "school"

    def test_self_links_ignored(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(
            user, "Self ref", "See [[self-ref]] in itself", "personal", "health",
        )
        backlinks = library_service.get_backlinks(user, "personal", "health", "self-ref")
        assert backlinks == []


class TestDeadWikilinks:
    def test_detects_dead_link(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(
            user, "Sleep tips", "See [[missing-note]]", "personal", "health",
        )
        dead = library_service.find_dead_wikilinks(user)
        assert len(dead) == 1
        assert dead[0]["dead_target"] == "missing-note"

    def test_valid_links_not_flagged(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(user, "Target", "content", "personal", "health")
        library_service.create_note(
            user, "Source", "See [[target]]", "personal", "health",
        )
        dead = library_service.find_dead_wikilinks(user)
        assert dead == []


# ---------------------------------------------------------------------------
# INDEX.md regeneration
# ---------------------------------------------------------------------------

class TestIndex:
    def test_index_regenerates_on_note_create(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(user, "Sleep tips", "body", "personal", "health")
        index = library_service.read_index(user)
        assert "Personal" in index
        assert "Health" in index
        assert "Sleep tips" in index

    def test_index_shows_author_badges(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Journal")
        library_service.create_note(
            user, "Human note", "mine", "personal", "journal", author="human",
        )
        library_service.create_note(
            user, "Prax note", "agent's", "personal", "journal", author="prax",
        )
        index = library_service.read_index(user)
        assert "👤" in index
        assert "🤖" in index

    def test_index_updates_on_delete(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(user, "Sleep tips", "body", "personal", "health")
        library_service.delete_note(user, "personal", "health", "sleep-tips")
        index = library_service.read_index(user)
        assert "Sleep tips" not in index


# ---------------------------------------------------------------------------
# Schema I/O
# ---------------------------------------------------------------------------

class TestSchema:
    def test_read_returns_default(self, user):
        schema = library_service.read_schema(user)
        assert "Karpathy" in schema

    def test_write_and_read_roundtrip(self, user):
        library_service.write_schema(user, "# My custom schema\n\nJust a test.")
        schema = library_service.read_schema(user)
        assert schema == "# My custom schema\n\nJust a test."


# ---------------------------------------------------------------------------
# Raw captures
# ---------------------------------------------------------------------------

class TestRawCaptures:
    def test_capture_raw(self, user):
        result = library_service.raw_capture(
            user, "Interesting article", "Full text here", "https://example.com/foo",
        )
        assert result["status"] == "captured"
        assert result["raw"]["title"] == "Interesting article"
        assert result["raw"]["source_url"] == "https://example.com/foo"

    def test_list_raw(self, user):
        library_service.raw_capture(user, "First", "body 1")
        library_service.raw_capture(user, "Second", "body 2")
        raw = library_service.list_raw(user)
        assert len(raw) == 2

    def test_get_raw(self, user):
        r = library_service.raw_capture(user, "Test", "body text")
        slug = r["raw"]["slug"]
        fetched = library_service.get_raw(user, slug)
        assert fetched is not None
        assert "body text" in fetched["content"]

    def test_delete_raw(self, user):
        r = library_service.raw_capture(user, "Throwaway", "body")
        slug = r["raw"]["slug"]
        library_service.delete_raw(user, slug)
        assert library_service.get_raw(user, slug) is None

    def test_promote_raw_to_notebook(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        r = library_service.raw_capture(
            user, "Sleep article", "studies show...", "https://example.com/s",
        )
        raw_slug = r["raw"]["slug"]
        result = library_service.promote_raw(
            user, raw_slug, "personal", "health", new_title="Sleep evidence",
        )
        assert result["status"] == "promoted"
        # Original raw is gone
        assert library_service.get_raw(user, raw_slug) is None
        # New note exists with promoted_from reference
        note = library_service.get_note(user, "personal", "health", "sleep-evidence")
        assert note is not None
        assert note["meta"].get("promoted_from") == raw_slug
        assert note["meta"].get("source_url") == "https://example.com/s"

    def test_promote_raw_missing(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        result = library_service.promote_raw(
            user, "missing-slug", "personal", "health",
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

class TestOutputs:
    def test_write_output(self, user):
        result = library_service.write_output(
            user, "Daily briefing", "content here", kind="brief",
        )
        assert result["status"] == "written"
        assert result["output"]["kind"] == "brief"

    def test_list_outputs(self, user):
        library_service.write_output(user, "Brief 1", "a")
        library_service.write_output(user, "Brief 2", "b")
        outputs = library_service.list_outputs(user)
        assert len(outputs) == 2

    def test_get_output(self, user):
        r = library_service.write_output(user, "Test output", "report body")
        slug = r["output"]["slug"]
        fetched = library_service.get_output(user, slug)
        assert fetched is not None
        assert "report body" in fetched["content"]

    def test_delete_output(self, user):
        r = library_service.write_output(user, "Throwaway", "body")
        slug = r["output"]["slug"]
        result = library_service.delete_output(user, slug)
        assert result["status"] == "deleted"
        assert library_service.get_output(user, slug) is None

    def test_delete_output_missing(self, user):
        result = library_service.delete_output(user, "no-such-slug")
        assert "error" in result


# ---------------------------------------------------------------------------
# Refine (LLM-mocked)
# ---------------------------------------------------------------------------

class TestRefine:
    def test_refine_returns_before_and_after(self, user, monkeypatch):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Journal")
        library_service.create_note(
            user, "My thoughts", "raw draft", "personal", "journal", author="human",
        )

        # Mock build_llm to return a fake LLM with a predictable response
        class _FakeResult:
            content = "refined body"

        class _FakeLLM:
            def invoke(self, _prompt):
                return _FakeResult()

        def _fake_build_llm(**_kwargs):
            return _FakeLLM()

        import prax.agent.llm_factory as llm_factory
        monkeypatch.setattr(llm_factory, "build_llm", _fake_build_llm)

        result = library_service.refine_note(
            user, "personal", "journal", "my-thoughts",
            instructions="polish it",
        )
        assert result["status"] == "refined"
        assert result["before"] == "raw draft"
        assert result["after"] == "refined body"
        # Not yet applied — verify the note is unchanged on disk
        unchanged = library_service.get_note(user, "personal", "journal", "my-thoughts")
        assert unchanged["content"] == "raw draft"

    def test_apply_refine_bypasses_permission_gate(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Journal")
        library_service.create_note(
            user, "My thoughts", "original", "personal", "journal",
            author="human", prax_may_edit=False,
        )
        # Direct apply_refine bypasses prax_may_edit because the human
        # approved the change via the UI flow.
        result = library_service.apply_refine(
            user, "personal", "journal", "my-thoughts", "approved refinement",
        )
        assert result["status"] == "updated"
        note = library_service.get_note(user, "personal", "journal", "my-thoughts")
        assert "approved refinement" in note["content"]
        assert note["meta"]["last_edited_by"] == "prax"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_empty_library_skips_llm(self, user):
        report = library_service.run_health_check(user)
        assert report["static"]["note_count"] == 0
        assert report["llm"].get("skipped") is True

    def test_detects_dead_wikilinks(self, user, monkeypatch):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(
            user, "Sleep tips", "See [[missing]]", "personal", "health",
        )

        class _FakeLLM:
            def invoke(self, _p):
                class R:
                    content = '{"contradictions": [], "unsourced": [], "gaps": []}'
                return R()

        import prax.agent.llm_factory as llm_factory
        monkeypatch.setattr(llm_factory, "build_llm", lambda **_k: _FakeLLM())

        report = library_service.run_health_check(user)
        assert report["static"]["note_count"] == 1
        assert len(report["static"]["dead_wikilinks"]) == 1
        assert report["static"]["dead_wikilinks"][0]["dead_target"] == "missing"

    def test_detects_orphans(self, user, monkeypatch):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(
            user, "Lonely note", "no links anywhere", "personal", "health",
        )

        class _FakeLLM:
            def invoke(self, _p):
                class R:
                    content = '{"contradictions": [], "unsourced": [], "gaps": []}'
                return R()

        import prax.agent.llm_factory as llm_factory
        monkeypatch.setattr(llm_factory, "build_llm", lambda **_k: _FakeLLM())

        report = library_service.run_health_check(user)
        assert len(report["static"]["orphans"]) == 1
        assert report["static"]["orphans"][0]["slug"] == "lonely-note"

    def test_report_written_to_outputs(self, user, monkeypatch):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(user, "Note", "body", "personal", "health")

        class _FakeLLM:
            def invoke(self, _p):
                class R:
                    content = '{"contradictions": [], "unsourced": [], "gaps": []}'
                return R()

        import prax.agent.llm_factory as llm_factory
        monkeypatch.setattr(llm_factory, "build_llm", lambda **_k: _FakeLLM())

        library_service.run_health_check(user)
        outputs = library_service.list_outputs(user)
        assert len(outputs) == 1
        assert outputs[0]["kind"] == "health-check"
