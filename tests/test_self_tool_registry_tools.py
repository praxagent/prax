import importlib

from prax.agent.user_context import current_user_id
from prax.services import workspace_service

USER = "+15550003333"


def test_self_tool_registry_tools_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    module = importlib.reload(importlib.import_module("prax.agent.self_tool_registry_tools"))
    token = current_user_id.set(USER)
    try:
        result = module.self_tool_register.invoke({
            "name": "Claim Audit Helper",
            "description": "Inspects trace evidence before a grounded final answer.",
            "capabilities": "trace,audit",
            "plugin_name": "audit_helpers",
            "tool_names": "claim_audit_helper",
            "tags": "grounding",
            "risk_level": "low",
        })
        assert "Registered" in result

        listed = module.self_tool_list.invoke({"query": "grounded"})
        assert "claim_audit_helper" in listed

        status = module.self_tool_record_result.invoke({
            "name": "Claim Audit Helper",
            "passed": True,
            "summary": "Targeted test passed.",
            "trace_id": "trace-3",
        })
        assert "tested" in status

        audit = module.self_tool_audit.invoke({"name": "claim_audit_helper"})
        assert "Targeted test passed" in audit
    finally:
        current_user_id.reset(token)


def test_self_tool_registry_tools_require_user_context():
    module = importlib.reload(importlib.import_module("prax.agent.self_tool_registry_tools"))
    token = current_user_id.set(None)
    try:
        result = module.self_tool_list.invoke({})
        assert "no active user context" in result
    finally:
        current_user_id.reset(token)
