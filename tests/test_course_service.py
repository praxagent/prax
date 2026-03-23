"""Tests for the course/tutor service."""
from __future__ import annotations

import os

import pytest

from prax.services import course_service
from prax.settings import settings


@pytest.fixture(autouse=True)
def _tmp_workspace(monkeypatch, tmp_path):
    """Point the workspace dir at a temp directory for each test."""
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    yield


USER = "test_user_123"


class TestCreateCourse:
    def test_basic(self):
        data = course_service.create_course(USER, "linear algebra")
        assert data["id"] == "linear_algebra"
        assert data["subject"] == "linear algebra"
        assert data["title"] == "linear algebra"
        assert data["status"] == "assessing"
        assert data["level"] is None
        assert data["plan"]["modules"] == []
        assert data["progress"]["pace"] == "normal"

    def test_custom_title(self):
        data = course_service.create_course(USER, "math", title="Linear Algebra for Engineers")
        assert data["id"] == "linear_algebra_for_engineers"
        assert data["title"] == "Linear Algebra for Engineers"
        assert data["subject"] == "math"

    def test_deduplicates_slug(self):
        d1 = course_service.create_course(USER, "Python")
        d2 = course_service.create_course(USER, "Python")
        assert d1["id"] == "python"
        assert d2["id"] == "python_2"

    def test_course_persists_and_has_materials_dir(self):
        course_service.create_course(USER, "rust")
        # Verify we can read it back (proves course.json exists on disk).
        data = course_service.get_course(USER, "rust")
        assert data["id"] == "rust"
        # Verify materials/ subdir was created by saving a material.
        course_service.save_material(USER, "rust", "test.md", "hello")
        assert course_service.read_material(USER, "rust", "test.md") == "hello"


class TestGetCourse:
    def test_found(self):
        course_service.create_course(USER, "go")
        data = course_service.get_course(USER, "go")
        assert data["subject"] == "go"

    def test_not_found(self):
        with pytest.raises(FileNotFoundError):
            course_service.get_course(USER, "nonexistent")


class TestListCourses:
    def test_empty(self):
        assert course_service.list_courses(USER) == []

    def test_multiple(self):
        course_service.create_course(USER, "Python")
        course_service.create_course(USER, "Rust")
        courses = course_service.list_courses(USER)
        assert len(courses) == 2
        ids = {c["id"] for c in courses}
        assert ids == {"python", "rust"}

    def test_summary_fields(self):
        course_service.create_course(USER, "Go")
        courses = course_service.list_courses(USER)
        c = courses[0]
        assert "id" in c
        assert "title" in c
        assert "status" in c
        assert "progress" in c


class TestUpdateCourse:
    def test_set_level(self):
        course_service.create_course(USER, "Python")
        data = course_service.update_course(USER, "python", {
            "level": "intermediate",
            "status": "active",
        })
        assert data["level"] == "intermediate"
        assert data["status"] == "active"

    def test_deep_merge_plan(self):
        course_service.create_course(USER, "Python")
        modules = [
            {"number": 1, "title": "Basics", "topics": ["vars", "types"], "status": "active"},
            {"number": 2, "title": "Functions", "topics": ["def", "lambda"], "status": "pending"},
        ]
        data = course_service.update_course(USER, "python", {
            "plan": {"modules": modules, "current_module": 1},
            "progress": {"total_modules": 2},
        })
        assert len(data["plan"]["modules"]) == 2
        assert data["plan"]["current_module"] == 1
        assert data["progress"]["total_modules"] == 2

    def test_adjust_pace(self):
        course_service.create_course(USER, "Python")
        data = course_service.update_course(USER, "python", {
            "progress": {"pace": "fast"},
        })
        assert data["progress"]["pace"] == "fast"

    def test_not_found(self):
        with pytest.raises(FileNotFoundError):
            course_service.update_course(USER, "nope", {"status": "active"})

    def test_updates_timestamp(self):
        course_service.create_course(USER, "Python")
        original = course_service.get_course(USER, "python")
        ts1 = original["updated_at"]
        course_service.update_course(USER, "python", {"level": "beginner"})
        updated = course_service.get_course(USER, "python")
        assert updated["updated_at"] >= ts1


class TestTutorNotes:
    def test_read_empty(self):
        course_service.create_course(USER, "Go")
        assert course_service.read_tutor_notes(USER, "go") == ""

    def test_write_and_read(self):
        course_service.create_course(USER, "Go")
        course_service.save_tutor_notes(USER, "go", "User is strong on concurrency.")
        assert "strong on concurrency" in course_service.read_tutor_notes(USER, "go")

    def test_overwrite(self):
        course_service.create_course(USER, "Go")
        course_service.save_tutor_notes(USER, "go", "First notes")
        course_service.save_tutor_notes(USER, "go", "Updated notes")
        notes = course_service.read_tutor_notes(USER, "go")
        assert "Updated notes" in notes
        assert "First notes" not in notes

    def test_not_found(self):
        with pytest.raises(FileNotFoundError):
            course_service.save_tutor_notes(USER, "nope", "test")


class TestMaterials:
    def test_save_and_read(self):
        course_service.create_course(USER, "Python")
        course_service.save_material(USER, "python", "quiz_1.md", "# Quiz 1\n\n1. What is a list?")
        content = course_service.read_material(USER, "python", "quiz_1.md")
        assert "What is a list?" in content

    def test_read_not_found(self):
        course_service.create_course(USER, "Python")
        with pytest.raises(FileNotFoundError):
            course_service.read_material(USER, "python", "nonexistent.md")

    def test_course_not_found(self):
        with pytest.raises(FileNotFoundError):
            course_service.save_material(USER, "nope", "test.md", "content")


class TestHugoSiteGeneration:
    """Test Hugo content generation (without requiring Hugo binary)."""

    def _setup_course_with_modules(self):
        """Create a course with modules and materials for Hugo tests."""
        course_service.create_course(USER, "Python", title="Learn Python")
        modules = [
            {"number": 1, "title": "Variables & Types", "topics": ["vars", "int", "str"], "status": "completed"},
            {"number": 2, "title": "Functions", "topics": ["def", "return", "lambda"], "status": "active"},
            {"number": 3, "title": "Classes", "topics": ["class", "inheritance"], "status": "pending"},
        ]
        course_service.update_course(USER, "learn_python", {
            "level": "beginner",
            "status": "active",
            "plan": {"modules": modules, "current_module": 2},
            "progress": {"modules_completed": 1, "total_modules": 3},
        })
        # Save a material file for module 1.
        course_service.save_material(
            USER, "learn_python", "module_1.md",
            "# Variables & Types\n\nPython uses dynamic typing...",
        )
        return "learn_python"

    def test_ensure_hugo_site_creates_skeleton(self):
        """_ensure_hugo_site creates config, layouts, and content dirs."""
        from prax.services.workspace_service import _ensure_workspace
        root = _ensure_workspace(USER)
        site = course_service._ensure_hugo_site(root, "https://example.ngrok.io")

        assert os.path.isfile(os.path.join(site, "hugo.toml"))
        assert os.path.isfile(os.path.join(site, "layouts", "_default", "single.html"))
        assert os.path.isfile(os.path.join(site, "layouts", "_default", "list.html"))
        assert os.path.isdir(os.path.join(site, "content"))

        # Config has the right base URL.
        with open(os.path.join(site, "hugo.toml")) as f:
            config = f.read()
        assert "https://example.ngrok.io/" in config

    def test_generate_hugo_content_index(self):
        """_generate_hugo_content creates an _index.md for the course."""
        course_id = self._setup_course_with_modules()
        from prax.services.workspace_service import _ensure_workspace
        root = _ensure_workspace(USER)
        course_service._ensure_hugo_site(root, "https://example.ngrok.io")
        course_service._generate_hugo_content(root, course_id)

        site = course_service._hugo_site_dir(root)
        index_path = os.path.join(site, "content", course_id, "_index.md")
        assert os.path.isfile(index_path)
        with open(index_path) as f:
            content = f.read()
        assert 'title: "Learn Python"' in content
        assert "**Subject:** Python" in content
        assert "**Level:** beginner" in content
        assert "1/3 modules" in content

    def test_generate_hugo_content_module_pages(self):
        """_generate_hugo_content creates per-module markdown files."""
        course_id = self._setup_course_with_modules()
        from prax.services.workspace_service import _ensure_workspace
        root = _ensure_workspace(USER)
        course_service._ensure_hugo_site(root, "https://example.ngrok.io")
        course_service._generate_hugo_content(root, course_id)

        site = course_service._hugo_site_dir(root)
        content_dir = os.path.join(site, "content", course_id)
        md_files = [f for f in os.listdir(content_dir) if f != "_index.md"]
        assert len(md_files) == 3

        # Module 1 should include the saved material content.
        mod1_files = [f for f in md_files if f.startswith("01-")]
        assert len(mod1_files) == 1
        with open(os.path.join(content_dir, mod1_files[0])) as f:
            mod1 = f.read()
        assert "Python uses dynamic typing" in mod1
        assert 'module_number: 1' in mod1

        # Module 2 should fall back to topics list.
        mod2_files = [f for f in md_files if f.startswith("02-")]
        assert len(mod2_files) == 1
        with open(os.path.join(content_dir, mod2_files[0])) as f:
            mod2 = f.read()
        assert "def" in mod2
        assert "lambda" in mod2

    def test_generate_hugo_content_progress_bar(self):
        """Progress bar percentage is calculated correctly."""
        course_id = self._setup_course_with_modules()
        from prax.services.workspace_service import _ensure_workspace
        root = _ensure_workspace(USER)
        course_service._ensure_hugo_site(root, "https://example.ngrok.io")
        course_service._generate_hugo_content(root, course_id)

        site = course_service._hugo_site_dir(root)
        with open(os.path.join(site, "content", course_id, "_index.md")) as f:
            content = f.read()
        # 1/3 = 33%
        assert "width:33%" in content

    def test_get_course_site_public_dir_none_before_build(self):
        """Returns None when no Hugo site has been built."""
        assert course_service.get_course_site_public_dir(USER) is None

    def test_get_course_site_public_dir_after_build(self):
        """Returns the public dir path when it exists."""
        self._setup_course_with_modules()
        from prax.services.workspace_service import _ensure_workspace
        root = _ensure_workspace(USER)
        # Manually create the public dir (simulating a Hugo build).
        public = os.path.join(course_service._hugo_site_dir(root), "public")
        os.makedirs(public, exist_ok=True)
        result = course_service.get_course_site_public_dir(USER)
        assert result is not None
        assert result.endswith("public")

    def test_build_regenerates_all_courses(self):
        """build_course_site regenerates content for ALL courses, not just the target."""
        self._setup_course_with_modules()
        # Create a second course.
        course_service.create_course(USER, "Rust", title="Learn Rust")
        course_service.update_course(USER, "learn_rust", {
            "status": "active",
            "plan": {"modules": [{"number": 1, "title": "Ownership", "topics": ["borrow"], "status": "active"}], "current_module": 1},
            "progress": {"total_modules": 1},
        })

        from prax.services.workspace_service import _ensure_workspace
        root = _ensure_workspace(USER)
        site = course_service._ensure_hugo_site(root, "https://example.ngrok.io")

        # Monkeypatch _run_hugo to avoid needing the binary.
        original_run = course_service._run_hugo
        course_service._run_hugo = lambda s: None  # no-op

        try:
            course_service.build_course_site(USER, "learn_python", "https://example.ngrok.io")
        finally:
            course_service._run_hugo = original_run

        # Both courses should have content generated.
        content_dir = os.path.join(site, "content")
        assert os.path.isdir(os.path.join(content_dir, "learn_python"))
        assert os.path.isdir(os.path.join(content_dir, "learn_rust"))

