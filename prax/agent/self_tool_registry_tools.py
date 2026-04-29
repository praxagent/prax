"""Agent-facing tools for the self-authored tool registry."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id


def _uid() -> str | None:
    return current_user_id.get()


def _csv(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@tool
def self_tool_register(
    name: str,
    description: str,
    capabilities: str = "",
    plugin_name: str = "",
    tool_names: str = "",
    tags: str = "",
    risk_level: str = "medium",
    examples: str = "",
    provenance_trace_id: str = "",
) -> str:
    """Register or update metadata for a tool Prax authored for itself.

    This records the tool in an inspectable workspace registry.  It does not
    execute, test, or activate plugin code; use plugin_write/plugin_test/
    plugin_activate for executable lifecycle operations.
    """
    uid = _uid()
    if not uid:
        return "Error: no active user context."

    from prax.services import self_tool_registry_service as registry

    try:
        record = registry.register_tool(
            uid,
            name=name,
            description=description,
            capabilities=_csv(capabilities),
            plugin_name=plugin_name,
            tool_names=_csv(tool_names),
            tags=_csv(tags),
            risk_level=risk_level,
            examples=_csv(examples),
            provenance_trace_id=provenance_trace_id,
        )
    except ValueError as exc:
        return f"Failed to register self-authored tool: {exc}"
    return (
        f"Registered self-authored tool `{record['id']}` "
        f"status={record['status']} version={record['version']}."
    )


@tool
def self_tool_list(status: str = "", query: str = "", limit: int = 50) -> str:
    """List registered self-authored tools, optionally filtered by status/query."""
    uid = _uid()
    if not uid:
        return "Error: no active user context."

    from prax.services import self_tool_registry_service as registry

    records = registry.list_tools(uid, status=status, query=query, limit=limit)
    if not records:
        return "No self-authored tools found."

    lines = ["Self-authored tools:"]
    for record in records:
        tools = f" tools={','.join(record.get('tool_names', []))}" if record.get("tool_names") else ""
        plugin = f" plugin={record.get('plugin_name')}" if record.get("plugin_name") else ""
        lines.append(
            f"- `{record['id']}` [{record['status']}] risk={record.get('risk_level', 'medium')}"
            f"{plugin}{tools}\n  {record['description']}"
        )
    return "\n".join(lines)


@tool
def self_tool_update_status(
    name: str,
    status: str,
    summary: str = "",
    trace_id: str = "",
    error: str = "",
) -> str:
    """Update a registered self-authored tool's lifecycle status.

    Valid statuses: draft, tested, active, deprecated, failed.
    """
    uid = _uid()
    if not uid:
        return "Error: no active user context."

    from prax.services import self_tool_registry_service as registry

    try:
        record = registry.update_status(
            uid,
            name=name,
            status=status,
            summary=summary,
            trace_id=trace_id,
            error=error,
        )
    except ValueError as exc:
        return f"Failed to update self-authored tool status: {exc}"
    if not record:
        return f"No self-authored tool found for `{name}`."
    return f"Updated `{record['id']}` status to {record['status']}."


@tool
def self_tool_record_result(
    name: str,
    passed: bool,
    summary: str,
    trace_id: str = "",
    error: str = "",
) -> str:
    """Record the latest test or runtime result for a self-authored tool."""
    status = "tested" if passed else "failed"
    return self_tool_update_status.invoke({
        "name": name,
        "status": status,
        "summary": summary,
        "trace_id": trace_id,
        "error": error,
    })


@tool
def self_tool_audit(name: str) -> str:
    """Show detailed registry metadata and lifecycle history for one tool."""
    uid = _uid()
    if not uid:
        return "Error: no active user context."

    from prax.services import self_tool_registry_service as registry

    record = registry.get_tool(uid, name)
    if not record:
        return f"No self-authored tool found for `{name}`."

    lines = [
        f"Tool `{record['id']}`",
        f"- Status: {record['status']}",
        f"- Risk: {record.get('risk_level', 'medium')}",
        f"- Version: {record.get('version', 1)}",
        f"- Plugin: {record.get('plugin_name') or '(none)'}",
        f"- Tools: {', '.join(record.get('tool_names', [])) or '(none)'}",
        f"- Description: {record['description']}",
    ]
    if record.get("capabilities"):
        lines.append(f"- Capabilities: {', '.join(record['capabilities'])}")
    if record.get("tags"):
        lines.append(f"- Tags: {', '.join(record['tags'])}")
    if record.get("provenance_trace_id"):
        lines.append(f"- Provenance trace: {record['provenance_trace_id']}")
    history = record.get("history", [])[-10:]
    if history:
        lines.append("History:")
        for event in history:
            detail = event.get("summary") or event.get("event", "")
            if event.get("error"):
                detail = f"{detail} error={event['error']}"
            lines.append(f"- {event.get('at', '?')} {event.get('event', '?')}: {detail}")
    return "\n".join(lines)


def build_self_tool_registry_tools() -> list:
    """Return all self-authored tool registry tools."""
    return [
        self_tool_register,
        self_tool_list,
        self_tool_update_status,
        self_tool_record_result,
        self_tool_audit,
    ]
