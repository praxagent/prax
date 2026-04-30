"""End-to-end test for the daily briefing lifecycle.

Exercises the full path with only the network fetchers mocked:

  news plugin._do_briefing
    -> library_service.write_output (real, hits disk)
      -> file appears in library/outputs/
        -> GET  /teamwork/library/outputs        (real Flask route)
        -> GET  /teamwork/library/outputs/<slug> (real Flask route)
        -> DELETE /teamwork/library/outputs/<slug> (real Flask route — newly added)
          -> file removed from disk
          -> subsequent GET returns 404
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask

from prax.blueprints.teamwork_routes import teamwork_routes
from prax.plugins.tools.news.plugin import _do_briefing
from prax.services import library_service

USER_ID = "briefing-e2e-user"
NEWS_MODULE = "prax.plugins.tools.news.plugin"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Isolate library_service to a per-test workspace."""
    ws = tmp_path / USER_ID
    ws.mkdir()
    monkeypatch.setattr(library_service, "workspace_root", lambda _uid: str(ws))
    return ws


@pytest.fixture
def client(workspace):
    """Flask test client with the real teamwork blueprint mounted, and
    the user-id resolver pinned to the test user."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(teamwork_routes)
    with patch(
        "prax.blueprints.teamwork_routes._get_teamwork_user_id",
        return_value=USER_ID,
    ), app.test_client() as c:
        yield c


def test_daily_briefing_full_lifecycle(workspace, client):
    sources = [
        {"name": "Feed1", "type": "rss", "url": "https://example.com/feed"},
        {"name": "HN", "type": "hackernews", "url": ""},
    ]

    # Mock only the network fetchers — everything downstream is real.
    with (
        patch(
            f"{NEWS_MODULE}._fetch_rss",
            return_value="### Feed1\n1. [Headline](https://example.com/a) — summary",
        ),
        patch(
            f"{NEWS_MODULE}._fetch_hackernews",
            return_value="### HN\n1. [Story](https://news.ycombinator.com/item?id=1) — 100 pts",
        ),
    ):
        briefing_msg = _do_briefing(USER_ID, sources)

    # 1. Briefing returned a slug + persisted to disk
    assert "Saved to library/outputs/" in briefing_msg
    outputs_dir = workspace / "library" / "outputs"
    md_files = list(outputs_dir.glob("*.md"))
    assert len(md_files) == 1, f"expected one briefing on disk, got {md_files}"
    on_disk = md_files[0].read_text(encoding="utf-8")
    assert "kind: news-briefing" in on_disk
    assert "### Feed1" in on_disk
    assert "### HN" in on_disk

    # 2. List endpoint sees it
    resp = client.get("/teamwork/library/outputs")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "outputs" in payload
    assert len(payload["outputs"]) == 1
    item = payload["outputs"][0]
    assert item["kind"] == "news-briefing"
    slug = item["slug"]
    assert slug  # non-empty

    # 3. Detail endpoint returns frontmatter + body
    resp = client.get(f"/teamwork/library/outputs/{slug}")
    assert resp.status_code == 200
    detail = resp.get_json()
    assert detail["meta"]["slug"] == slug
    assert detail["meta"]["kind"] == "news-briefing"
    assert "### Feed1" in detail["content"]

    # 4. Delete endpoint removes it
    resp = client.delete(f"/teamwork/library/outputs/{slug}")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "deleted", "slug": slug}

    # 5. File is gone, list is empty, detail 404s
    assert not (outputs_dir / f"{slug}.md").exists()

    resp = client.get("/teamwork/library/outputs")
    assert resp.status_code == 200
    assert resp.get_json()["outputs"] == []

    resp = client.get(f"/teamwork/library/outputs/{slug}")
    assert resp.status_code == 404


def test_delete_missing_briefing_returns_404(client):
    resp = client.delete("/teamwork/library/outputs/no-such-briefing")
    assert resp.status_code == 404
    assert "error" in resp.get_json()
