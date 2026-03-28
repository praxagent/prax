"""JSON-RPC protocol for subprocess plugin isolation (Phase 2).

Shared between the parent-side bridge and the child-side host.
Messages are newline-delimited JSON ("JSON-lines") on stdin/stdout.

Message types:

  Parent → Child:
    register  — load plugin, call register(caps), return tool metadata
    invoke    — call a tool with kwargs, return result
    shutdown  — exit cleanly

  Child → Parent (capability callbacks):
    caps_call — plugin called a PluginCapabilities method; parent executes it
                and sends the result back

  Child → Parent (responses):
    tools     — response to 'register' with tool metadata
    result    — response to 'invoke' with tool return value
    error     — response to any request that failed
    caps_result — response to 'caps_call' (parent → child direction)
    ready     — handshake after subprocess starts
"""
from __future__ import annotations

import json
from typing import Any


def send(stream: Any, msg: dict) -> None:
    """Write a JSON message followed by a newline to *stream*."""
    line = json.dumps(msg, default=str) + "\n"
    stream.write(line)
    stream.flush()


def recv(stream: Any) -> dict | None:
    """Read one JSON-lines message from *stream*.  Returns None on EOF."""
    line = stream.readline()
    if not line:
        return None
    return json.loads(line)


# ---------------------------------------------------------------------------
# Message constructors
# ---------------------------------------------------------------------------

def msg_register(plugin_path: str, rel_key: str, trust_tier: str) -> dict:
    return {
        "type": "register",
        "plugin_path": plugin_path,
        "rel_key": rel_key,
        "trust_tier": trust_tier,
    }


def msg_invoke(tool_name: str, kwargs: dict) -> dict:
    return {"type": "invoke", "tool_name": tool_name, "kwargs": kwargs}


def msg_shutdown() -> dict:
    return {"type": "shutdown"}


def msg_ready(tools: list[dict]) -> dict:
    return {"type": "ready", "tools": tools}


def msg_result(value: Any) -> dict:
    return {"type": "result", "value": value}


def msg_error(message: str, traceback: str = "") -> dict:
    return {"type": "error", "message": message, "traceback": traceback}


def msg_caps_call(method: str, args: list, kwargs: dict) -> dict:
    return {"type": "caps_call", "method": method, "args": args, "kwargs": kwargs}


def msg_caps_result(value: Any) -> dict:
    return {"type": "caps_result", "value": value}


def msg_caps_error(message: str) -> dict:
    return {"type": "caps_error", "message": message}


# ---------------------------------------------------------------------------
# Tool metadata serialization
# ---------------------------------------------------------------------------

def tool_to_metadata(tool: Any) -> dict:
    """Extract serializable metadata from a LangChain tool."""
    schema = {}
    if hasattr(tool, "args_schema") and tool.args_schema is not None:
        try:
            schema = tool.args_schema.model_json_schema()
        except AttributeError:
            schema = tool.args_schema.schema()
    return {
        "name": tool.name,
        "description": tool.description or "",
        "args_schema": schema,
    }
