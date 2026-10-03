"""Flatten LangChain message content to plain text.

``BaseMessage.content`` is a **string for some providers and a list of content
blocks for others** (``[{"type": "text", "text": "..."}, ...]``) — notably
OpenAI's Responses API (the `-pro`/o-series path, and every OpenAI model once
keyless mode routes through the proxy) and Anthropic. Code that assumes `str`
fails two ways against the list form, and only one of them is loud:

- ``content + "suffix"`` raises ``TypeError: can only concatenate list (not
  "str") to list`` — a hard crash on every orchestrator turn.
- ``str(content)`` silently yields ``"[{'type': 'text', 'text': '...'}]"``,
  the Python repr, which then flows into markers, ``startswith`` checks,
  audits and the user's reply as garbage.

Both were real: the spoke runner hit the silent one (a prefix check on
preserved tool evidence), and the orchestrator hit the loud one the first time
a live eval ran an o-series model through the secrets proxy. Every extraction
of text from a message goes through ``message_text`` so a third provider
shape is one edit, not a codebase sweep.
"""
from __future__ import annotations

from typing import Any

__all__ = ["message_text", "content_text", "tool_output_text"]


def content_text(raw: Any) -> str:
    """Return the plain text of a message ``content`` value, any shape."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # Skip non-text blocks (reasoning summaries, images, tool-use
                # payloads): they are not part of the visible reply, and
                # concatenating them would leak internals into the answer.
                btype = block.get("type")
                if btype in {"reasoning", "thinking", "image", "image_url",
                             "tool_use", "tool_result"}:
                    continue
                # Common shapes: {"type": "text", "text": "..."} (Anthropic /
                # Responses API), {"text": "..."}, or {"content": "..."}.
                text = block.get("text") or block.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p for p in parts if p)
    return str(raw)


def message_text(msg: Any) -> str:
    """Return the plain text of a message object (or "" if it has none)."""
    return content_text(getattr(msg, "content", None))


def _is_content_block(item: Any) -> bool:
    return isinstance(item, str) or (
        isinstance(item, dict) and any(k in item for k in ("type", "text", "content"))
    )


def tool_output_text(output: Any) -> str:
    """Return the plain text of a tool result, as a tool callback receives it.

    ``on_tool_end`` is handed a ``ToolMessage`` whenever the tool was invoked
    with a ToolCall — every ToolNode call — and the tool's raw return value
    otherwise: a str, a dict, a list of records, a ``Command``, anything.
    ``str()`` of the message is its pydantic repr (``content='…' name='…'
    tool_call_id='…'``) and slicing the message raises ``TypeError``, so read
    its content and flatten it. Never raises: callbacks run on every tool call,
    and a preview must not cost the span or trace node it decorates.
    """
    if output is None:
        return ""
    try:
        raw = getattr(output, "content", output)
        if isinstance(raw, list) and not all(_is_content_block(b) for b in raw):
            # A list of records, not message content: content_text keeps only
            # text blocks and would reduce it to "".
            return str(raw)
        return content_text(raw)
    except Exception:  # noqa: BLE001 - a broken __str__ must not escape a callback
        return f"<unprintable {type(output).__name__}>"
