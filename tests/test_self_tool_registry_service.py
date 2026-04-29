from prax.services import self_tool_registry_service, workspace_service

USER = "+15550002222"


def test_register_list_update_and_get_self_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))

    record = self_tool_registry_service.register_tool(
        USER,
        name="Weather Fallback",
        description="Fallback weather lookup when the primary geocoder fails.",
        capabilities=["weather", "fallback"],
        plugin_name="weather_tools",
        tool_names=["weather_fallback"],
        tags=["resilience"],
        risk_level="low",
        examples=["Use after LOCATION_UNCERTAIN"],
        provenance_trace_id="trace-1",
    )

    assert record["id"] == "weather_fallback"
    assert record["status"] == "draft"
    assert record["version"] == 1

    matches = self_tool_registry_service.list_tools(USER, query="geocoder")
    assert [m["id"] for m in matches] == ["weather_fallback"]

    updated = self_tool_registry_service.update_status(
        USER,
        name="weather_fallback",
        status="tested",
        summary="Sandbox test passed.",
        trace_id="trace-2",
    )
    assert updated is not None
    assert updated["status"] == "tested"
    assert updated["history"][-1]["trace_id"] == "trace-2"

    fetched = self_tool_registry_service.get_tool(USER, "Weather Fallback")
    assert fetched is not None
    assert fetched["id"] == "weather_fallback"
