from prax.services import structured_memory_service, workspace_service

USER = "+15550001111"


def test_records_lists_and_archives_structured_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))

    record = structured_memory_service.record_memory(
        USER,
        bucket="preference",
        key="answer style",
        content="User prefers concise engineering summaries.",
        scope="user",
        source="test",
        confidence=0.9,
        importance=0.8,
        tags=["preference", "style"],
    )

    assert record["bucket"] == "preference"
    assert record["scope"] == "user"
    assert record["status"] == "active"

    matches = structured_memory_service.list_memories(USER, query="engineering")
    assert [m["id"] for m in matches] == [record["id"]]

    archived = structured_memory_service.archive_memory(USER, record["id"], reason="test")
    assert archived is not None
    assert archived["status"] == "archived"
    assert structured_memory_service.list_memories(USER, query="engineering") == []


def test_upsert_preserves_key_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))

    first = structured_memory_service.record_memory(
        USER,
        bucket="project_fact",
        key="Prax weather workflow",
        content="Weather workflow failed at geocoding.",
    )
    second = structured_memory_service.record_memory(
        USER,
        bucket="project_fact",
        key="Prax weather workflow",
        content="Weather workflow now falls back to ZIP lookup.",
    )

    assert second["id"] == first["id"]
    matches = structured_memory_service.list_memories(USER, bucket="project_fact")
    assert len(matches) == 1
    assert "ZIP lookup" in matches[0]["content"]
