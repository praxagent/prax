import importlib

from prax.agent.user_context import current_user_id
from prax.services import structured_memory_service, workspace_service

USER = "+15550004444"


def test_decision_record_writes_trace_and_structured_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    module = importlib.reload(importlib.import_module("prax.agent.workspace_tools"))
    token = current_user_id.set(USER)
    try:
        result = module.decision_record.invoke({
            "problem": "Weather workflow fallback",
            "chosen_action": "Use browser search after weather geocoder failure.",
            "rationale": "The user asked for the goal, not one specific data source.",
            "options_considered": "Ask user, retry same geocoder, browser fallback",
            "rejected_alternatives": "Stop after LOCATION_UNCERTAIN",
            "confidence": 0.85,
            "assumptions": "Location is already known from user notes.",
            "scope": "project",
            "tags": "weather, fallback",
        })

        assert "Decision recorded" in result
        memories = structured_memory_service.list_memories(
            USER,
            bucket="decision",
            query="browser search",
            status="active",
        )
        assert len(memories) == 1
        assert memories[0]["scope"] == "project"

        trace = workspace_service.read_trace_tail(USER, lines=20)
        assert "[DECISION]" in trace
        assert "Weather workflow fallback" in trace
    finally:
        current_user_id.reset(token)
