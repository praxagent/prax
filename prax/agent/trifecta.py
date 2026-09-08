"""Lethal-trifecta classification for the capability gateway.

The *lethal trifecta* (Simon Willison): a turn that simultaneously (1) ingests
**untrusted** content, (2) reads **private** data, and (3) can **exfiltrate / act
externally** is exploitable via indirect prompt injection. Once all three legs are
present in one turn, the external-sink action is gated (requires confirmation).

## Classification is per-leg, at TWO layers

A tool can touch **multiple** legs (the browser both reads untrusted pages AND
acts), so we expose per-leg predicates, not one category.

Governance wraps tools at the **orchestrator** layer — where it sees
``delegate_<spoke>`` tools — and, since spoke governance landed, at the spoke
layer, where it sees the spoke-internal tools themselves.  A delegate is
classified by what its spoke can DO (the browser spoke is both an untrusted
source and an external sink) and its legs are recorded BEFORE the spoke runs;
the spoke-internal / direct tool names cover the spoke layer, the MCP layer and
any directly-wrapped tools.

Delegate keys below are the REAL tool-name suffixes (``delegate_content_editor``
→ ``content_editor``).  A ``delegate_*`` tool whose key is in none of the three
sets is **unknown** and fails closed: it falls through to the name lists and is
treated as an external sink regardless, because a delegation we cannot
classify might act externally.  Tool code can also declare its legs directly
with a ``_trifecta_legs`` attribute (a set of ``LEG_*`` values), which wins over
both.  Enforcement is flag-gated behind ``LETHAL_TRIFECTA_GUARD``.
"""
from __future__ import annotations

from collections.abc import Iterable

LEG_UNTRUSTED = "untrusted"
LEG_PRIVATE = "private"
LEG_SINK = "sink"
ALL_LEGS = frozenset({LEG_UNTRUSTED, LEG_PRIVATE, LEG_SINK})

# --- Delegation boundary (delegate_<spoke>) — keyed by the real tool suffix ---
_DELEGATE_UNTRUSTED = frozenset({
    "research",          # web search / URL fetch / plugins that ingest external text
    "browser",           # renders attacker-controllable pages
    "content_editor",    # fetches URLs and edits external documents
    "sandbox",           # runs code that fetches whatever it likes; output is untrusted
    "tasks",             # Kanban/todo items can carry pasted external text
    "environment",       # weather / geocoding APIs return third-party text
})
_DELEGATE_PRIVATE = frozenset({
    "knowledge",         # library, notes, projects
    "workspace",         # the user's files
    "memory",            # long-term memory + knowledge graph
    "course",            # reads the learner's library (build_library_tools)
    "professor",         # reads courses, progress and library notes (faculty_tools)
})
_DELEGATE_SINK = frozenset({
    "browser",           # clicks, fills, submits
    "content_editor",    # publishes / shares edited content
    "scheduler",         # creates jobs that act later
    "sysadmin",          # installs / configures
    "desktop",           # drives a real desktop
    "sandbox",           # shell + network from inside the container
    "plugins",           # end-user plugin tools (media / vision / artifact generators)
    "tasks",             # pauses/resumes the task runner, mutates the user's board
    "self_improve",      # edits and deploys Prax's own code
    "plugin_fix",        # writes and activates plugins
    "finetune",          # uploads data to a fine-tuning provider
})
_KNOWN_DELEGATES = _DELEGATE_UNTRUSTED | _DELEGATE_PRIVATE | _DELEGATE_SINK

# --- Spoke-internal / direct tool names (MCP layer + directly-wrapped tools) ---
_SRC_NAMES = (
    "fetch_url", "url_content", "read_url", "fetch_content", "web_search",
    "background_search", "browser_navigate", "browser_read", "browser_extract",
    "browser_page", "screenshot", "arxiv", "rss",
    # Content ingestors that return attacker-controllable external text —
    # transcripts, fetched PDFs, page/feed summaries.  Anything that turns a
    # URL or external medium into model-visible text belongs here.
    "youtube", "transcribe", "web_summary", "pdf_summary", "news",
    # The library inbox (library/raw/) holds AUTO-CAPTURED external pages —
    # shared links, attachments, fetched articles. Reading it is reading
    # attacker-controllable text, so it belongs here and not among the
    # private-data readers. It was previously classified as NEITHER, which
    # meant MEDIUM risk with no provenance at all.
    # See docs/security/provenance-laundering.md.
    "library_raw", "raw_capture", "raw_promote",
)
_PRIVATE_NAMES = (
    "memory_search", "memory_recall", "memory_get", "knowledge_search",
    "workspace_read", "workspace_search", "workspace_list", "conversation_search",
    "conversation_history", "note_read", "user_notes_read", "progress_read",
    # progress_search reads the SAME session-detail files as progress_detail.
    # A reader that is classified differently from the store it reads is a
    # provenance hole by construction — see docs/security/provenance-laundering.md.
    "progress_detail", "progress_search",
    "trace_search", "trace_detail", "review_my_traces",
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


def _name_legs(tool_name: str) -> set[str]:
    n = (tool_name or "").lower()
    legs: set[str] = set()
    if any(p in n for p in _SRC_NAMES):
        legs.add(LEG_UNTRUSTED)
    if any(p in n for p in _PRIVATE_NAMES):
        legs.add(LEG_PRIVATE)
    if any(p in n for p in _SINK_NAMES):
        legs.add(LEG_SINK)
    return legs


def legs_for(tool_name: str, declared: Iterable[str] | None = None) -> frozenset[str]:
    """The static trifecta legs of *tool_name*.

    ``declared`` — a tool's code-set ``_trifecta_legs`` — wins outright.  A
    ``delegate_*`` tool is classified by its spoke key when the key is known;
    an UNKNOWN delegate falls through to the name lists AND is treated as an
    external sink (fail closed).  Any other tool is classified by name.
    """
    if declared is not None:
        legs = frozenset(str(leg) for leg in declared)
        unknown = legs - ALL_LEGS
        if unknown:
            raise ValueError(f"{tool_name}: unknown trifecta legs {sorted(unknown)}")
        return legs
    sp = _spoke(tool_name)
    if sp:
        if sp in _KNOWN_DELEGATES:
            legs = set()
            if sp in _DELEGATE_UNTRUSTED:
                legs.add(LEG_UNTRUSTED)
            if sp in _DELEGATE_PRIVATE:
                legs.add(LEG_PRIVATE)
            if sp in _DELEGATE_SINK:
                legs.add(LEG_SINK)
            return frozenset(legs)
        legs = _name_legs(tool_name)
        legs.add(LEG_SINK)  # unknown delegate: fail closed
        return frozenset(legs)
    return frozenset(_name_legs(tool_name))


def is_known_delegate(tool_name: str) -> bool:
    """True when *tool_name* is a ``delegate_*`` tool with an explicit
    classification (as opposed to the fail-closed default)."""
    return _spoke(tool_name) in _KNOWN_DELEGATES


def is_untrusted_source(tool_name: str) -> bool:
    return LEG_UNTRUSTED in legs_for(tool_name)


def is_private_data(tool_name: str) -> bool:
    return LEG_PRIVATE in legs_for(tool_name)


def is_external_sink(tool_name: str) -> bool:
    return LEG_SINK in legs_for(tool_name)


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
                         private_seen: bool,
                         legs: Iterable[str] | None = None) -> bool:
    """True iff *tool_name* is an external sink AND the turn already touched both
    other legs — the exact moment the injection-exfiltration chain closes.
    ``legs`` lets the caller pass a precomputed / declared classification."""
    if legs is None:
        legs = legs_for(tool_name)
    return bool(untrusted_seen and private_seen and LEG_SINK in set(legs))


def trifecta_guard_enabled() -> bool:
    try:
        from prax.settings import settings
        return bool(getattr(settings, "lethal_trifecta_guard", False))
    except Exception:
        return False
