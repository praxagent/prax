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

__all__ = ["message_text", "content_text"]


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
