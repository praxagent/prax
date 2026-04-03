"""Tests for the TeamWork memory API endpoints in teamwork_routes."""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from prax.blueprints.teamwork_routes import teamwork_routes

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_USER_ID = "user-abc-123"


@pytest.fixture()
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(teamwork_routes)
    return app


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _patch_user_id():
    with patch(
        "prax.blueprints.teamwork_routes._get_teamwork_user_id",
        return_value=FIXED_USER_ID,
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers — lightweight dataclass mocks that mirror memory.models
# ---------------------------------------------------------------------------

@dataclass
class FakeSTMEntry:
    key: str
    content: str
    tags: list[str] = field(default_factory=list)
    created_at: str = "2026-04-01T00:00:00"
    access_count: int = 0
    importance: float = 0.5


@dataclass
class FakeMemoryResult:
    memory_id: str
    content: str
    score: float = 0.9
    source: str = "api"
    importance: float = 0.5
    created_at: str = "2026-04-01T00:00:00"
    entities: list[str] = field(default_factory=list)


@dataclass
class FakeEntity:
    id: str = "ent-1"
    name: str = "Python"
    display_name: str = "Python"
    entity_type: str = "topic"
    importance: float = 0.8
    mention_count: int = 5
    first_seen: str = "2026-01-01T00:00:00"
    last_seen: str = "2026-04-01T00:00:00"
    properties: dict = field(default_factory=dict)
    relations: list[dict] = field(default_factory=list)


def _mock_memory_service(available=True, **overrides):
    """Return a MagicMock that mimics MemoryService."""
    svc = MagicMock()
    svc.available = available
    for k, v in overrides.items():
        setattr(svc, k, v)
    return svc


# ---------------------------------------------------------------------------
# 1. GET /teamwork/memory/config
# ---------------------------------------------------------------------------

class TestMemoryConfig:
    def test_returns_config(self, client):
        svc = _mock_memory_service(available=True)
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get("/teamwork/memory/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is True
        assert data["memory_enabled"] is True
        assert data["user_id"] == FIXED_USER_ID

    def test_disabled_memory(self, client):
        svc = _mock_memory_service(available=False)
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get("/teamwork/memory/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is False


# ---------------------------------------------------------------------------
# 2. GET /teamwork/memory/stm/<user_id>
# ---------------------------------------------------------------------------

class TestSTMList:
    def test_returns_entries(self, client):
        entries = [
            FakeSTMEntry(key="tz", content="UTC"),
            FakeSTMEntry(key="lang", content="en", tags=["pref"]),
        ]
        with patch("prax.blueprints.teamwork_routes.stm_read", create=True):
            # The endpoint does a lazy import, so patch at the source module.
            with patch("prax.services.memory.stm.stm_read", return_value=entries):
                resp = client.get(f"/teamwork/memory/stm/{FIXED_USER_ID}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["user_id"] == FIXED_USER_ID
        assert len(data["entries"]) == 2
        assert data["entries"][0]["key"] == "tz"
        assert data["entries"][1]["tags"] == ["pref"]

    def test_empty_stm(self, client):
        with patch("prax.services.memory.stm.stm_read", return_value=[]):
            resp = client.get(f"/teamwork/memory/stm/{FIXED_USER_ID}")
        assert resp.status_code == 200
        assert resp.get_json()["entries"] == []


# ---------------------------------------------------------------------------
# 3. PUT /teamwork/memory/stm/<user_id>
# ---------------------------------------------------------------------------

class TestSTMUpsert:
    def test_creates_entry(self, client):
        entry = FakeSTMEntry(key="tz", content="UTC")
        with patch("prax.services.memory.stm.stm_write", return_value=entry):
            resp = client.put(
                f"/teamwork/memory/stm/{FIXED_USER_ID}",
                json={"key": "tz", "content": "UTC"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["key"] == "tz"
        assert data["content"] == "UTC"

    def test_missing_key_returns_400(self, client):
        resp = client.put(
            f"/teamwork/memory/stm/{FIXED_USER_ID}",
            json={"content": "UTC"},
        )
        assert resp.status_code == 400
        assert "key" in resp.get_json()["error"]

    def test_missing_content_returns_400(self, client):
        resp = client.put(
            f"/teamwork/memory/stm/{FIXED_USER_ID}",
            json={"key": "tz"},
        )
        assert resp.status_code == 400
        assert "content" in resp.get_json()["error"]

    def test_empty_body_returns_400(self, client):
        resp = client.put(
            f"/teamwork/memory/stm/{FIXED_USER_ID}",
            json={},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 4. DELETE /teamwork/memory/stm/<user_id>/<key>
# ---------------------------------------------------------------------------

class TestSTMDelete:
    def test_deletes_entry(self, client):
        with patch("prax.services.memory.stm.stm_delete", return_value=True):
            resp = client.delete(f"/teamwork/memory/stm/{FIXED_USER_ID}/tz")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deleted"] is True
        assert data["key"] == "tz"

    def test_not_found_returns_404(self, client):
        with patch("prax.services.memory.stm.stm_delete", return_value=False):
            resp = client.delete(f"/teamwork/memory/stm/{FIXED_USER_ID}/missing")
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()


# ---------------------------------------------------------------------------
# 5. GET /teamwork/memory/ltm/<user_id>?q=...
# ---------------------------------------------------------------------------

class TestLTMRecall:
    def test_recall_memories(self, client):
        memories = [
            FakeMemoryResult(memory_id="m1", content="Python is great"),
        ]
        svc = _mock_memory_service()
        svc.recall.return_value = memories
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get(
                f"/teamwork/memory/ltm/{FIXED_USER_ID}?q=python&top_k=3"
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["user_id"] == FIXED_USER_ID
        assert data["query"] == "python"
        assert len(data["memories"]) == 1
        assert data["memories"][0]["memory_id"] == "m1"
        svc.recall.assert_called_once_with(FIXED_USER_ID, "python", top_k=3)

    def test_missing_query_returns_400(self, client):
        svc = _mock_memory_service()
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get(f"/teamwork/memory/ltm/{FIXED_USER_ID}")
        assert resp.status_code == 400
        assert "q" in resp.get_json()["error"]

    def test_memory_disabled_returns_503(self, client):
        svc = _mock_memory_service(available=False)
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get(f"/teamwork/memory/ltm/{FIXED_USER_ID}?q=test")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 6. POST /teamwork/memory/ltm/<user_id>
# ---------------------------------------------------------------------------

class TestLTMStore:
    def test_stores_memory(self, client):
        svc = _mock_memory_service()
        svc.remember.return_value = "mem-42"
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.post(
                f"/teamwork/memory/ltm/{FIXED_USER_ID}",
                json={"content": "Remember this", "importance": 0.8, "source": "test"},
            )
        assert resp.status_code == 201
        assert resp.get_json()["memory_id"] == "mem-42"
        svc.remember.assert_called_once_with(
            FIXED_USER_ID,
            content="Remember this",
            importance=0.8,
            tags=None,
            source="test",
        )

    def test_missing_content_returns_400(self, client):
        svc = _mock_memory_service()
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.post(
                f"/teamwork/memory/ltm/{FIXED_USER_ID}",
                json={"importance": 0.5},
            )
        assert resp.status_code == 400
        assert "content" in resp.get_json()["error"]

    def test_remember_returns_none_gives_500(self, client):
        svc = _mock_memory_service()
        svc.remember.return_value = None
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.post(
                f"/teamwork/memory/ltm/{FIXED_USER_ID}",
                json={"content": "Something"},
            )
        assert resp.status_code == 500

    def test_memory_disabled_returns_503(self, client):
        svc = _mock_memory_service(available=False)
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.post(
                f"/teamwork/memory/ltm/{FIXED_USER_ID}",
                json={"content": "Something"},
            )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 7. DELETE /teamwork/memory/ltm/<user_id>/<memory_id>
# ---------------------------------------------------------------------------

class TestLTMForget:
    def test_deletes_memory(self, client):
        svc = _mock_memory_service()
        svc.forget.return_value = True
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.delete(f"/teamwork/memory/ltm/{FIXED_USER_ID}/mem-42")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deleted"] is True
        assert data["memory_id"] == "mem-42"

    def test_not_found_returns_404(self, client):
        svc = _mock_memory_service()
        svc.forget.return_value = False
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.delete(f"/teamwork/memory/ltm/{FIXED_USER_ID}/nope")
        assert resp.status_code == 404

    def test_memory_disabled_returns_503(self, client):
        svc = _mock_memory_service(available=False)
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.delete(f"/teamwork/memory/ltm/{FIXED_USER_ID}/mem-42")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 8. GET /teamwork/memory/graph/<user_id>
# ---------------------------------------------------------------------------

class TestGraphStats:
    def test_returns_stats_and_entities(self, client):
        svc = _mock_memory_service()
        stats = {"entities": 10, "relations": 25}
        entities = [{"name": "Python", "mentions": 5}]
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc), \
             patch("prax.services.memory.graph_store.get_stats", return_value=stats), \
             patch("prax.services.memory.graph_store.search_entities", return_value=entities):
            resp = client.get(f"/teamwork/memory/graph/{FIXED_USER_ID}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["user_id"] == FIXED_USER_ID
        assert data["stats"]["entities"] == 10
        assert len(data["entities"]) == 1

    def test_memory_disabled_returns_503(self, client):
        svc = _mock_memory_service(available=False)
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get(f"/teamwork/memory/graph/{FIXED_USER_ID}")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 9. GET /teamwork/memory/graph/<user_id>/entity/<name>
# ---------------------------------------------------------------------------

class TestGraphEntity:
    def test_returns_entity(self, client):
        entity = FakeEntity(name="Python", display_name="Python lang")
        svc = _mock_memory_service()
        svc.entity_lookup.return_value = entity
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get(f"/teamwork/memory/graph/{FIXED_USER_ID}/entity/Python")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Python"
        assert data["display_name"] == "Python lang"
        assert data["entity_type"] == "topic"
        assert data["mention_count"] == 5

    def test_entity_not_found_returns_404(self, client):
        svc = _mock_memory_service()
        svc.entity_lookup.return_value = None
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get(f"/teamwork/memory/graph/{FIXED_USER_ID}/entity/Nope")
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_memory_disabled_returns_503(self, client):
        svc = _mock_memory_service(available=False)
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get(f"/teamwork/memory/graph/{FIXED_USER_ID}/entity/X")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 10. GET /teamwork/memory/stats/<user_id>
# ---------------------------------------------------------------------------

class TestMemoryStats:
    def test_returns_stats(self, client):
        svc = _mock_memory_service()
        svc.stats.return_value = {
            "memory_enabled": True,
            "stm_entries": 3,
            "vector_memories": 42,
            "graph_store_stats": {"entities": 10, "relations": 25},
        }
        with patch("prax.blueprints.teamwork_routes._memory_service", return_value=svc):
            resp = client.get(f"/teamwork/memory/stats/{FIXED_USER_ID}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["user_id"] == FIXED_USER_ID
        assert data["memory_enabled"] is True
        assert data["stm_entries"] == 3
        assert data["vector_memories"] == 42
        svc.stats.assert_called_once_with(FIXED_USER_ID)
