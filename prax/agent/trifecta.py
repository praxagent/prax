"""Lethal-trifecta classification for the capability gateway.

The *lethal trifecta* (Simon Willison): a turn that simultaneously (1) ingests
**untrusted** content, (2) reads **private** data, and (3) can **exfiltrate / act
externally** is exploitable via indirect prompt injection. Once all three legs are
present in one turn, the external-sink action is gated (requires confirmation).

## Classification is per-leg, at TWO layers

A tool can touch **multiple** legs (the browser both reads untrusted pages AND
acts), so we expose per-leg predicates, not one category.

Crucially, governance wraps tools at the layer the **orchestrator** governs —
where it sees ``delegate_<spoke>`` tools, not the spoke-internal tools. So a spoke
is classified by what it can DO (the browser spoke is both an untrusted source and
an external sink). We also classify spoke-internal / direct tool names for the MCP
layer and any directly-wrapped tools. Flag-gated behind ``LETHAL_TRIFECTA_GUARD``.
"""
from __future__ import annotations

# --- Delegation boundary (delegate_<spoke>) — what the orchestrator governs ---
_DELEGATE_UNTRUSTED = frozenset({"research", "browser", "content"})   # ingest web/external text
_DELEGATE_PRIVATE = frozenset({"knowledge", "workspace", "memory"})   # read the user's data
_DELEGATE_SINK = frozenset({"browser", "content", "scheduler", "sysadmin", "desktop"})  # act externally

# --- Spoke-internal / direct tool names (MCP layer + directly-wrapped tools) ---
_SRC_NAMES = (
    "fetch_url", "url_content", "read_url", "fetch_content", "web_search",
    "background_search", "browser_navigate", "browser_read", "browser_extract",
    "browser_page", "screenshot", "arxiv", "rss",
)
_PRIVATE_NAMES = (
    "memory_search", "memory_recall", "memory_get", "knowledge_search",
    "workspace_read", "workspace_search", "workspace_list", "conversation_search",
    "conversation_history", "note_read", "user_notes_read", "progress_read",
    "progress_detail", "trace_search", "trace_detail", "review_my_traces",
    "artifact_locator", "library_read", "browser_credentials",
)
_SINK_NAMES = (
    "send_sms", "send_email", "send_message", "discord", "_publish", "_share",
    "workspace_share", "browser_click", "browser_press", "browser_type",
    "browser_fill", "browser_submit", "sandbox_browser_act", "schedule_create",
    "schedule_reminder", "sysadmin", "run_python", "sandbox_shell", "http_post", "post_",
)


def _spoke(tool_name: str) -> str:
    n = (tool_name or "").lower()
    return n[len("delegate_"):] if n.startswith("delegate_") else ""


def is_untrusted_source(tool_name: str) -> bool:
    sp = _spoke(tool_name)
    if sp:
        return sp in _DELEGATE_UNTRUSTED
    n = (tool_name or "").lower()
    return any(p in n for p in _SRC_NAMES)


def is_private_data(tool_name: str) -> bool:
    sp = _spoke(tool_name)
    if sp:
        return sp in _DELEGATE_PRIVATE
    n = (tool_name or "").lower()
    return any(p in n for p in _PRIVATE_NAMES)


def is_external_sink(tool_name: str) -> bool:
    sp = _spoke(tool_name)
    if sp:
        return sp in _DELEGATE_SINK
    n = (tool_name or "").lower()
    return any(p in n for p in _SINK_NAMES)


def classify_trifecta(tool_name: str) -> str | None:
    """Convenience single label (sink > source > private) for reporting/tests.
    Note a tool may touch several legs — use the predicates for the guard logic."""
    if is_external_sink(tool_name):
        return "external_sink"
    if is_untrusted_source(tool_name):
        return "untrusted_source"
    if is_private_data(tool_name):
        return "private_data"
    return None


def should_escalate_sink(tool_name: str, *, untrusted_seen: bool,
                         private_seen: bool) -> bool:
    """True iff *tool_name* is an external sink AND the turn already touched both
    other legs — the exact moment the injection-exfiltration chain closes."""
    return bool(untrusted_seen and private_seen and is_external_sink(tool_name))


def trifecta_guard_enabled() -> bool:
    try:
        from prax.settings import settings
        return bool(getattr(settings, "lethal_trifecta_guard", False))
    except Exception:
        return False
