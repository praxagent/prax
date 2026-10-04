"""Library search: notes found by words in their title, tags and text — for
the agent (library_search) and the UI (GET /teamwork/library/search).
Before, neither could search note text at all."""
from __future__ import annotations

import pytest
from flask import Flask

from prax.services import library_service

USER = "usr_search"


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = tmp_path / USER
    root.mkdir()
    monkeypatch.setattr(library_service, "workspace_root", lambda _uid: str(root))
    library_service.create_space(USER, "Linear Algebra")
    library_service.create_space(USER, "Cooking")
    library_service.create_notebook(USER, "linear-algebra", "Lectures")
    library_service.create_notebook(USER, "cooking", "Recipes")
    add = library_service.create_note
    add(USER, title="Eigenvalues", content="An eigenvector keeps its direction. Eigenvalues scale it.",
        project="linear-algebra", notebook="lectures", tags=["spectral"])
    add(USER, title="Matrix multiplication", content="Rows times columns; eigenvalues come later.",
        project="linear-algebra", notebook="lectures")
    add(USER, title="Sourdough", content="Feed the starter. Long proof, cold oven spring.",
        project="cooking", notebook="recipes", tags=["bread"])
    return root


def test_title_matches_rank_first_with_a_snippet(lib):
    hits = library_service.search_notes(USER, "eigenvalues")
    assert [h["slug"] for h in hits] == ["eigenvalues", "matrix-multiplication"]
    assert "eigenvalues" in hits[1]["snippet"].lower()
    assert hits[0]["space"] == "linear-algebra" and hits[0]["notebook"] == "lectures"


def test_every_word_must_match_and_phrases_count_as_one(lib):
    assert [h["slug"] for h in library_service.search_notes(USER, "eigenvalues columns")] == ["matrix-multiplication"]
    assert [h["slug"] for h in library_service.search_notes(USER, '"cold oven"')] == ["sourdough"]
    assert library_service.search_notes(USER, '"oven cold"') == []


def test_tags_and_space_scope(lib):
    assert [h["slug"] for h in library_service.search_notes(USER, "bread")] == ["sourdough"]
    assert library_service.search_notes(USER, "eigenvalues", space="cooking") == []


def test_nothing_to_search_for(lib):
    assert library_service.search_notes(USER, "   ") == []
    assert library_service.search_notes(USER, "quaternions") == []


def test_a_bad_space_is_refused(lib):
    with pytest.raises(ValueError):
        library_service.search_notes(USER, "x", space="../../etc")


def test_the_agent_tool(lib, monkeypatch):
    from prax.agent import library_tools
    monkeypatch.setattr(library_tools, "_uid", lambda: USER)
    out = library_tools.library_search.invoke({"query": "starter"})
    assert "**Sourdough** (cooking/recipes/sourdough)" in out
    assert "No notes match" in library_tools.library_search.invoke({"query": "quaternions"})
    from prax.agent.action_policy import TOOL_RISK_MAP, RiskLevel
    assert TOOL_RISK_MAP["library_search"] is RiskLevel.LOW


def test_the_route(lib, monkeypatch):
    import prax.settings
    from prax.blueprints import teamwork_routes as tr
    monkeypatch.setattr(prax.settings.settings, "prax_api_key", "")
    monkeypatch.setattr(tr, "_get_teamwork_user_id", lambda: USER)
    app = Flask(__name__)
    app.register_blueprint(tr.teamwork_routes)
    c = app.test_client()
    body = c.get("/teamwork/library/search?q=eigenvalues&limit=1").get_json()
    assert [r["slug"] for r in body["results"]] == ["eigenvalues"]
    assert c.get("/teamwork/library/search?q=x&space=../x").status_code == 400
