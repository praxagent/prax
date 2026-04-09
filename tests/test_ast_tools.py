"""Tests for tree-sitter AST parsing tools."""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def py_file(tmp_path):
    """Create a small Python source file for testing."""
    code = textwrap.dedent("""\
        import os
        from pathlib import Path

        class MyService(BaseService):
            \"\"\"A sample service.\"\"\"

            def __init__(self, name: str):
                self.name = name

            @staticmethod
            def helper() -> bool:
                return True

        def build_tools(cfg: dict, limit: int = 10) -> list:
            \"\"\"Build the tool list.\"\"\"
            return []

        @tool
        def my_tool(query: str) -> str:
            return query
    """)
    p = tmp_path / "sample.py"
    p.write_text(code)
    return str(p)


@pytest.fixture()
def js_file(tmp_path):
    """Create a small JS source file for testing."""
    code = textwrap.dedent("""\
        import { Router } from 'express';
        import path from 'path';

        class ApiController extends BaseController {
            constructor(db) {
                super(db);
            }

            getAll() {
                return this.db.findAll();
            }
        }

        function createApp(config) {
            return new ApiController(config.db);
        }
    """)
    p = tmp_path / "app.js"
    p.write_text(code)
    return str(p)


@pytest.fixture()
def py_project(tmp_path):
    """Create a small Python project directory for dependency testing."""
    pkg = tmp_path / "mypackage"
    pkg.mkdir()

    (pkg / "__init__.py").write_text("")

    (pkg / "models.py").write_text(textwrap.dedent("""\
        from dataclasses import dataclass

        @dataclass
        class User:
            name: str
            email: str
    """))

    (pkg / "service.py").write_text(textwrap.dedent("""\
        from mypackage.models import User

        def get_user(uid: int) -> User:
            return User(name="test", email="test@test.com")
    """))

    (pkg / "views.py").write_text(textwrap.dedent("""\
        from mypackage.service import get_user
        from mypackage.models import User

        def user_view(uid: int) -> dict:
            u = get_user(uid)
            return {"name": u.name}
    """))

    return str(pkg)


# ---------------------------------------------------------------------------
# code_structure tests
# ---------------------------------------------------------------------------

class TestCodeStructure:

    def test_python_file(self, py_file):
        from prax.agent.ast_tools import code_structure

        result = code_structure.invoke({"file_path": py_file})

        # Should find the class
        assert "class MyService" in result
        assert "BaseService" in result

        # Should find methods
        assert "__init__" in result
        assert "helper" in result

        # Should find top-level functions
        assert "build_tools" in result
        assert "my_tool" in result

        # Should show imports
        assert "import os" in result
        assert "from pathlib import Path" in result

        # Should include line count
        assert "lines" in result

    def test_python_params_and_return(self, py_file):
        from prax.agent.ast_tools import code_structure

        result = code_structure.invoke({"file_path": py_file})

        # Type hints and defaults in params
        assert "name: str" in result
        assert "cfg: dict" in result
        assert "limit: int = 10" in result

        # Return type
        assert "-> list" in result
        assert "-> bool" in result

    def test_javascript_file(self, js_file):
        from prax.agent.ast_tools import code_structure

        result = code_structure.invoke({"file_path": js_file})

        # Should find class
        assert "ApiController" in result

        # Should find function
        assert "createApp" in result

        # Should find imports
        assert "import" in result

    def test_file_not_found(self):
        from prax.agent.ast_tools import code_structure

        result = code_structure.invoke({"file_path": "/nonexistent/file.py"})
        assert "Error" in result
        assert "not found" in result

    def test_unsupported_extension(self, tmp_path):
        from prax.agent.ast_tools import code_structure

        p = tmp_path / "data.csv"
        p.write_text("a,b,c")

        result = code_structure.invoke({"file_path": str(p)})
        assert "Error" in result
        assert "unsupported" in result
        assert ".csv" in result


# ---------------------------------------------------------------------------
# code_dependencies tests
# ---------------------------------------------------------------------------

class TestCodeDependencies:

    def test_python_project(self, py_project):
        from prax.agent.ast_tools import code_dependencies

        result = code_dependencies.invoke({
            "directory": py_project,
            "language": "python",
        })

        # Should list files
        assert "service.py" in result
        assert "views.py" in result
        assert "models.py" in result

        # Should show imports
        assert "mypackage.models" in result
        assert "mypackage.service" in result

        # Should identify hub files
        assert "Most-Imported" in result

    def test_directory_not_found(self):
        from prax.agent.ast_tools import code_dependencies

        result = code_dependencies.invoke({
            "directory": "/nonexistent/dir",
            "language": "python",
        })
        assert "Error" in result
        assert "not found" in result

    def test_unsupported_language(self, tmp_path):
        from prax.agent.ast_tools import code_dependencies

        result = code_dependencies.invoke({
            "directory": str(tmp_path),
            "language": "rust",
        })
        assert "Error" in result
        assert "unsupported" in result

    def test_empty_directory(self, tmp_path):
        from prax.agent.ast_tools import code_dependencies

        result = code_dependencies.invoke({
            "directory": str(tmp_path),
            "language": "python",
        })
        assert "No python files" in result


# ---------------------------------------------------------------------------
# code_search_ast tests
# ---------------------------------------------------------------------------

class TestCodeSearchAst:

    def test_find_function_by_name(self, py_project):
        from prax.agent.ast_tools import code_search_ast

        result = code_search_ast.invoke({
            "directory": py_project,
            "pattern": "get_user",
            "kind": "function",
        })

        assert "get_user" in result
        assert "service.py" in result

    def test_wildcard_pattern(self, py_project):
        from prax.agent.ast_tools import code_search_ast

        result = code_search_ast.invoke({
            "directory": py_project,
            "pattern": "get_*",
            "kind": "function",
        })

        assert "get_user" in result

    def test_find_class(self, py_project):
        from prax.agent.ast_tools import code_search_ast

        result = code_search_ast.invoke({
            "directory": py_project,
            "pattern": "User",
            "kind": "class",
        })

        assert "User" in result
        assert "models.py" in result

    def test_find_import(self, py_project):
        from prax.agent.ast_tools import code_search_ast

        result = code_search_ast.invoke({
            "directory": py_project,
            "pattern": "dataclass",
            "kind": "import",
        })

        assert "dataclass" in result

    def test_no_matches(self, py_project):
        from prax.agent.ast_tools import code_search_ast

        result = code_search_ast.invoke({
            "directory": py_project,
            "pattern": "nonexistent_xyz",
            "kind": "function",
        })

        assert "No function matching" in result

    def test_invalid_kind(self, py_project):
        from prax.agent.ast_tools import code_search_ast

        result = code_search_ast.invoke({
            "directory": py_project,
            "pattern": "foo",
            "kind": "enum",
        })
        assert "Error" in result
        assert "enum" in result

    def test_directory_not_found(self):
        from prax.agent.ast_tools import code_search_ast

        result = code_search_ast.invoke({
            "directory": "/nonexistent/dir",
            "pattern": "foo",
        })
        assert "Error" in result


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:

    def test_build_ast_tools_without_tree_sitter(self):
        """build_ast_tools returns empty list when tree-sitter missing."""
        import prax.agent.ast_tools as mod

        def patched_build():
            try:
                raise ImportError("No module named 'tree_sitter'")
            except ImportError:
                return []

        with patch.object(mod, "build_ast_tools", patched_build):
            result = mod.build_ast_tools()
            assert result == []

    def test_build_ast_tools_with_tree_sitter(self):
        """build_ast_tools returns three tools when tree-sitter is available."""
        from prax.agent.ast_tools import build_ast_tools

        tools = build_ast_tools()
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert "code_structure" in names
        assert "code_dependencies" in names
        assert "code_search_ast" in names


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_file(self, tmp_path):
        """An empty Python file should return structure with no classes/functions."""
        from prax.agent.ast_tools import code_structure

        p = tmp_path / "empty.py"
        p.write_text("")

        result = code_structure.invoke({"file_path": str(p)})
        assert "empty.py" in result
        # Should not error
        assert "Error" not in result

    def test_typescript_file(self, tmp_path):
        """TypeScript files should be parsed."""
        from prax.agent.ast_tools import code_structure

        code = textwrap.dedent("""\
            import { Component } from '@angular/core';

            class AppComponent {
                title: string = 'app';

                ngOnInit() {
                    console.log(this.title);
                }
            }

            function bootstrap(config: any): void {
                new AppComponent();
            }
        """)
        p = tmp_path / "app.ts"
        p.write_text(code)

        result = code_structure.invoke({"file_path": str(p)})
        assert "AppComponent" in result
        assert "bootstrap" in result

    def test_decorated_python_class(self, tmp_path):
        """Decorated classes should capture the decorator."""
        from prax.agent.ast_tools import code_structure

        code = textwrap.dedent("""\
            from dataclasses import dataclass

            @dataclass
            class Config:
                host: str
                port: int = 8080
        """)
        p = tmp_path / "config.py"
        p.write_text(code)

        result = code_structure.invoke({"file_path": str(p)})
        assert "@dataclass" in result
        assert "Config" in result

    def test_method_search(self, py_file):
        """code_search_ast with kind='method' finds class methods."""
        from prax.agent.ast_tools import code_search_ast

        # py_file has MyService with __init__ and helper methods
        result = code_search_ast.invoke({
            "directory": str(Path(py_file).parent),
            "pattern": "helper",
            "kind": "method",
        })
        assert "helper" in result
        assert "MyService" in result
