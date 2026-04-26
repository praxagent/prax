"""Cross-service contract tests.

Verify that services respect each other's boundaries — they use only
public APIs and don't depend on internal path/layout assumptions.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Root of the package tree, resolved relative to this test file.
_PRAX_ROOT = Path(__file__).resolve().parent.parent / "prax"

_SERVICE_DIR = _PRAX_ROOT / "services"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_source(path: Path) -> ast.Module:
    """Parse a Python source file and return the AST."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _collect_imports_from(tree: ast.Module, module: str) -> list[str]:
    """Return all names imported via ``from <module> import ...`` in *tree*."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            for alias in node.names:
                names.append(alias.name)
    return names


def _collect_top_level_imports(tree: ast.Module) -> list[str]:
    """Return module names from top-level ``import X`` statements."""
    names: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
    return names


# ---------------------------------------------------------------------------
# 1. All services import only public workspace API
# ---------------------------------------------------------------------------


_CONSUMER_SERVICES = [
    "note_service.py",
    "project_service.py",
    "course_service.py",
    "sandbox_service.py",
]


class TestServicesImportOnlyPublicWorkspaceAPI:
    """Static check: ``from prax.services.workspace_service import X`` must
    only import names that do NOT start with an underscore."""

    @pytest.mark.parametrize("filename", _CONSUMER_SERVICES)
    def test_no_private_workspace_imports(self, filename: str):
        path = _SERVICE_DIR / filename
        tree = _parse_source(path)
        imported = _collect_imports_from(tree, "prax.services.workspace_service")
        private = [n for n in imported if n.startswith("_")]
        assert private == [], (
            f"{filename} imports private workspace_service names: {private}"
        )


# ---------------------------------------------------------------------------
# 2. note_service specifically uses workspace public API
# ---------------------------------------------------------------------------


class TestNoteServiceUsesWorkspacePublicAPI:
    """Focused AST check for note_service.py — workspace imports must be
    public names only (no underscore prefix)."""

    def test_workspace_imports_are_public(self):
        tree = _parse_source(_SERVICE_DIR / "note_service.py")
        imported = _collect_imports_from(tree, "prax.services.workspace_service")
        assert len(imported) > 0, "note_service should import from workspace_service"
        private = [n for n in imported if n.startswith("_")]
        assert private == [], (
            f"note_service.py imports private workspace_service names: {private}"
        )


# ---------------------------------------------------------------------------
# 3. Services use shared slugify (not their own re-based implementation)
# ---------------------------------------------------------------------------


_SLUGIFY_CONSUMERS = [
    "note_service.py",
    "project_service.py",
    "course_service.py",
]


class TestAllServicesUseSharedSlugify:
    """Verify services delegate to ``prax.utils.text.slugify`` instead of
    rolling their own with ``re.sub``."""

    @pytest.mark.parametrize("filename", _SLUGIFY_CONSUMERS)
    def test_imports_slugify_from_utils(self, filename: str):
        tree = _parse_source(_SERVICE_DIR / filename)
        imported = _collect_imports_from(tree, "prax.utils.text")
        assert "slugify" in imported, (
            f"{filename} does not import slugify from prax.utils.text"
        )

    @pytest.mark.parametrize("filename", _SLUGIFY_CONSUMERS)
    def test_no_top_level_re_import(self, filename: str):
        tree = _parse_source(_SERVICE_DIR / filename)
        top_imports = _collect_top_level_imports(tree)
        assert "re" not in top_imports, (
            f"{filename} has a top-level ``import re`` — slugify "
            "should be delegated to prax.utils.text"
        )


# ---------------------------------------------------------------------------
# 4. note_service.save_and_publish return-value contract
# ---------------------------------------------------------------------------


class _FakeLock:
    def __init__(self, *a):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class TestNoteServiceSaveAndPublishContract:
    """Verify the return dict from ``save_and_publish``."""

    def test_success_returns_slug_title_url(self, monkeypatch, tmp_path):
        from prax.services import note_service

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        monkeypatch.setattr(note_service, "ensure_workspace", lambda uid: str(workspace))
        monkeypatch.setattr(note_service, "get_lock", _FakeLock)
        monkeypatch.setattr(note_service, "git_commit", lambda *a, **kw: None)

        # Mock the Hugo build chain via publish_notes.
        monkeypatch.setattr(
            note_service,
            "publish_notes",
            lambda uid, base_url, slug=None: {"url": f"{base_url}/notes/{slug}/"},
        )

        # Mock ngrok URL.
        monkeypatch.setattr(
            "prax.utils.ngrok.get_ngrok_url",
            lambda: "https://example.ngrok.io",
        )

        result = note_service.save_and_publish("u1", "Contract Test", "body")
        assert "slug" in result
        assert "title" in result
        assert "url" in result
        assert result["title"] == "Contract Test"

    def test_url_is_teamwork_local_by_default(self, monkeypatch, tmp_path):
        """save_and_publish always returns a TeamWork-served URL — no ngrok required.

        The Hugo render goes through TeamWork (reachable via local network /
        Tailscale / SSH).  Public ngrok exposure is opt-in via ``public=True``.
        """
        from prax.services import note_service

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        monkeypatch.setattr(note_service, "ensure_workspace", lambda uid: str(workspace))
        monkeypatch.setattr(note_service, "get_lock", _FakeLock)
        monkeypatch.setattr(note_service, "git_commit", lambda *a, **kw: None)
        monkeypatch.setattr(
            note_service,
            "publish_notes",
            lambda uid, base_url, slug=None: {"url": f"{base_url}/notes/{slug}/"},
        )

        # ngrok intentionally unconfigured — should still produce a valid URL.
        monkeypatch.setattr(
            "prax.utils.ngrok.get_ngrok_url",
            lambda: None,
        )

        result = note_service.save_and_publish("u1", "No Ngrok", "body")
        assert "error" not in result
        assert "slug" in result
        assert "title" in result
        assert result["url"].startswith("http://localhost:8000/notes/")
        assert "public_url" not in result  # default is private


# ---------------------------------------------------------------------------
# 5. project_service uses only public note_service API
# ---------------------------------------------------------------------------


class TestProjectServiceUsesNoteServicePublicAPI:
    """AST-parse project_service.py and verify it only references public
    functions on note_service (no ``_parse_note``, ``_write_note``, etc.)."""

    def test_no_private_note_service_calls(self):
        tree = _parse_source(_SERVICE_DIR / "project_service.py")
        # Look for attribute accesses like ``note_service._something(...)``
        private_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                # Check if accessing via ``note_service.<name>``
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "note_service"
                    and node.attr.startswith("_")
                ):
                    private_calls.append(node.attr)
        assert private_calls == [], (
            f"project_service.py calls private note_service methods: {private_calls}"
        )

    def test_no_private_note_service_imports(self):
        tree = _parse_source(_SERVICE_DIR / "project_service.py")
        imported = _collect_imports_from(tree, "prax.services.note_service")
        private = [n for n in imported if n.startswith("_")]
        assert private == [], (
            f"project_service.py imports private note_service names: {private}"
        )


# ---------------------------------------------------------------------------
# 6. workspace_service public API stability
# ---------------------------------------------------------------------------


_EXPECTED_WORKSPACE_EXPORTS = [
    "workspace_root",
    "safe_join",
    "get_lock",
    "ensure_workspace",
    "git_commit",
    "save_file",
    "read_file",
    "list_active",
    "archive_file",
    "append_trace",
    "search_trace",
]


class TestWorkspaceServiceExportsAreStable:
    """Import workspace_service and verify the expected public API exists.
    This catches accidental renames or deletions."""

    @pytest.mark.parametrize("name", _EXPECTED_WORKSPACE_EXPORTS)
    def test_export_exists(self, name: str):
        from prax.services import workspace_service

        assert hasattr(workspace_service, name), (
            f"workspace_service is missing expected public function: {name}"
        )
        assert callable(getattr(workspace_service, name)), (
            f"workspace_service.{name} is not callable"
        )


# ---------------------------------------------------------------------------
# 7. slugify contract
# ---------------------------------------------------------------------------


class TestSlugifyContract:
    """Verify the basic contract of ``prax.utils.text.slugify``."""

    def test_returns_lowercase(self):
        from prax.utils.text import slugify

        assert slugify("Hello World") == slugify("Hello World").lower()

    def test_no_spaces(self):
        from prax.utils.text import slugify

        result = slugify("hello world foo")
        assert " " not in result

    def test_uses_separator(self):
        from prax.utils.text import slugify

        result = slugify("hello world", separator="_")
        assert "_" in result
        assert "-" not in result

    def test_respects_max_length(self):
        from prax.utils.text import slugify

        long_text = "a" * 200
        result = slugify(long_text, max_length=20)
        assert len(result) <= 20

    def test_fallback_for_empty_input(self):
        from prax.utils.text import slugify

        result = slugify("", fallback="default")
        assert result == "default"

    def test_fallback_for_special_chars(self):
        from prax.utils.text import slugify

        result = slugify("!@#$%", fallback="fallback")
        assert result == "fallback"

    def test_default_separator_is_hyphen(self):
        from prax.utils.text import slugify

        result = slugify("hello world")
        assert result == "hello-world"

    def test_strips_leading_trailing_separators(self):
        from prax.utils.text import slugify

        result = slugify("  hello  ")
        assert not result.startswith("-")
        assert not result.endswith("-")
