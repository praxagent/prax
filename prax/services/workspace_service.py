"""Git-backed per-user workspace for long-term document memory."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, datetime

import yaml

from prax.settings import settings

logger = logging.getLogger(__name__)

_USER_NOTES_CONTEXT_MAX_CHARS = 1200
_USER_NOTES_MAX_MATCHES = 8
_USER_NOTES_STOPWORDS = {
    "about", "after", "again", "also", "and", "any", "are", "but", "can",
    "could", "does", "for", "from", "get", "give", "have", "how", "into",
    "just", "know", "like", "make", "more", "need", "now", "our", "please",
    "should", "that", "the", "their", "them", "then", "there", "this",
    "what", "when", "where", "which", "who", "why", "with", "would", "you",
    "your",
}
_USER_NOTES_TIME_QUERY_TOKENS = {
    "alarm", "calendar", "date", "deadline", "later", "morning", "night",
    "remind", "reminder", "schedule", "scheduled", "time", "timezone",
    "today", "tomorrow", "tonight",
}
_USER_NOTES_IDENTITY_QUERY_TOKENS = {"name", "call", "called", "identity"}
_USER_NOTES_COMPACT_MIN_CHARS = 4096
_USER_NOTES_COMPACT_MIN_LINES = 80
_USER_NOTES_SECTION_MAX_ITEMS = 16
_USER_NOTES_PROMOTION_MAX = 10

type _UserNotePromotionCandidate = tuple[str, float, list[str]]

# ---------------------------------------------------------------------------
# Workspace .gitignore — written on init to every new workspace.
# Blocks media, LaTeX build artifacts, and Python caches.
# Allows: .pdf, .tex, .png, .jpg, .txt, .md, .json, etc.
# ---------------------------------------------------------------------------

_WORKSPACE_GITIGNORE = """\
# === Python ===
__pycache__/
*.py[cod]
*.pyo
*.egg-info/
*.egg
.eggs/
*.so

# === LaTeX build artifacts ===
*.aux
*.bbl
*.bcf
*.blg
*.fdb_latexmk
*.fls
*.idx
*.ilg
*.ind
*.lof
*.log
*.lot
*.nav
*.nlo
*.nls
*.out
*.run.xml
*.snm
*.synctex.gz
*.toc

# === Media (audio/video) ===
*.mp3
*.mp4
*.m4a
*.wav
*.ogg
*.flac
*.aac
*.wma
*.avi
*.mkv
*.mov
*.webm
*.wmv

# === OS junk ===
.DS_Store
Thumbs.db

# === Browser profile (binary blobs — not for git) ===
.browser_profile/

# === Rotated logs (kept as plain text for grep) ===
# archive/trace_logs/ — tracked by git for searchability

# === Shared temp dir (sandbox ↔ app scratch space) ===
.tmp/

# === Misc ===
*.tmp
*.swp
*~
"""


def safe_join(base: str, *parts: str) -> str:
    """Join paths and verify the result stays within *base*.

    Raises ``ValueError`` if the resolved path escapes the base directory
    (e.g. via ``../`` traversal or absolute path injection).
    """
    joined = os.path.normpath(os.path.join(base, *parts))
    # Use os.path.commonpath to ensure containment.
    base_resolved = os.path.realpath(base)
    joined_resolved = os.path.realpath(joined)
    if not joined_resolved.startswith(base_resolved + os.sep) and joined_resolved != base_resolved:
        raise ValueError(f"Path traversal blocked: {parts!r} escapes {base}")
    return joined


# Per-user locks to prevent concurrent git operations on the same workspace.
_workspace_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def get_lock(user_id: str) -> threading.Lock:
    with _lock_guard:
        if user_id not in _workspace_locks:
            _workspace_locks[user_id] = threading.Lock()
        return _workspace_locks[user_id]


def workspace_root(user_id: str) -> str:
    """Return the workspace root path for *user_id* (without creating it).

    ``settings.workspace_dir`` is resolved to an absolute path at settings
    load time (see ``AppSettings._absolute_workspace_dir``) — so all
    subsequent joins are absolute regardless of process CWD.

    All new users go through ``resolve_user()`` which assigns a UUID and a
    ``usr_*`` workspace directory.  The legacy fallback (phone numbers,
    ``D{discord_id}``) is only used for pre-existing directories — it will
    NOT create new phone-number-named directories.
    """
    try:
        from prax.services.identity_service import get_user
        user = get_user(user_id)
        if user:
            return os.path.join(settings.workspace_dir, user.workspace_dir)
    except Exception:
        pass
    # Legacy fallback — only for pre-existing directories.
    # Refuses to create new directories named after phone numbers or raw IDs;
    # callers must go through resolve_user() for new users.
    safe_id = user_id.lstrip("+")
    legacy_path = os.path.join(settings.workspace_dir, safe_id)
    if not os.path.isdir(legacy_path):
        logger.warning(
            "workspace_root called with unresolvable user_id %s and no "
            "existing legacy directory — caller should use resolve_user() first",
            user_id[:12],
        )
    return legacy_path


def ensure_workspace(user_id: str) -> str:
    """Create workspace dirs + git init if they don't exist. Returns workspace root."""
    root = workspace_root(user_id)
    active = os.path.join(root, "active")
    archive = os.path.join(root, "archive")
    plugins_custom = os.path.join(root, "plugins", "custom")
    plugins_shared = os.path.join(root, "plugins", "shared")
    os.makedirs(active, exist_ok=True)
    os.makedirs(archive, exist_ok=True)
    os.makedirs(plugins_custom, exist_ok=True)
    os.makedirs(plugins_shared, exist_ok=True)
    if not os.path.isdir(os.path.join(root, ".git")):
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", settings.git_author_email],
            cwd=root, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", settings.git_author_name],
            cwd=root, check=True, capture_output=True,
        )
        # Write mandatory .gitignore.
        with open(os.path.join(root, ".gitignore"), "w", encoding="utf-8") as f:
            f.write(_WORKSPACE_GITIGNORE)
        git_commit(root, "Initialize workspace")
    # Ensure .gitignore exists even for workspaces created before this change.
    gitignore_path = os.path.join(root, ".gitignore")
    if not os.path.isfile(gitignore_path):
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(_WORKSPACE_GITIGNORE)
        git_commit(root, "Add workspace .gitignore")
    return root


def git_commit(root: str, message: str) -> None:
    """Stage all changes and commit if there's anything to commit."""
    r = subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        # Self-heal "dubious ownership" errors from Docker UID mismatch.
        if "dubious ownership" in r.stderr:
            subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", root],
                capture_output=True,
            )
            r = subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, text=True)
        if r.returncode != 0:
            logger.warning("git add -A failed (rc=%d): %s", r.returncode, r.stderr[:300])
            # Fallback: try adding without -A (just tracked files + new).
            subprocess.run(["git", "add", "."], cwd=root, capture_output=True)
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True,
    )
    if result.stdout.strip():
        subprocess.run(
            ["git", "commit", "-m", message], cwd=root, check=True, capture_output=True,
        )


def save_user_notes(user_id: str, content: str) -> str:
    """Save user_notes.md to the workspace root (not active/). Git commit."""
    promotion_candidates: list[_UserNotePromotionCandidate] = []
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        filepath = os.path.join(root, "user_notes.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        git_commit(root, "Update user notes")
        if _should_compact_user_notes(content):
            compacted = _compact_user_notes_content(content)
            if compacted.strip() and compacted.strip() != content.strip():
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(compacted)
                git_commit(root, "Compact user notes")
                promotion_candidates = _user_notes_ltm_promotion_candidates(content, compacted)
                logger.info("Compacted user_notes.md for %s", user_id)
        logger.info("Updated user_notes.md for %s", user_id)

    _promote_user_notes_ltm_candidates(user_id, promotion_candidates)
    return filepath


def read_user_notes(user_id: str) -> str:
    """Read user_notes.md from the workspace root. Returns empty string if missing."""
    root = workspace_root(user_id)
    filepath = os.path.join(root, "user_notes.md")
    if not os.path.isfile(filepath):
        return ""
    with open(filepath, encoding="utf-8") as f:
        return f.read()


def _canonical_user_notes_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _display_user_notes_key(key: str) -> str:
    canonical = _canonical_user_notes_key(key)
    preferred = {
        "timezone": "timezone",
        "time_zone": "timezone",
        "name": "name",
        "preferences": "preferences",
        "interests": "interests",
    }
    return preferred.get(canonical, canonical.replace("_", " "))


def _normalize_user_notes_item(line: str) -> str:
    line = re.sub(r"\s+", " ", line.strip())
    if line.startswith(("-", "*")):
        line = line[1:].strip()
    return f"- {line}" if line else ""


def _fingerprint_user_notes_item(line: str) -> str:
    line = line.strip().lower()
    if line.startswith(("-", "*")):
        line = line[1:].strip()
    return re.sub(r"\s+", " ", line)


def _user_notes_scalar(line: str) -> tuple[str, str] | None:
    if line.startswith(("-", "*")):
        return None
    match = re.match(r"^([A-Za-z][\w /-]{0,60}):\s+(.+)$", line)
    if not match:
        return None
    key = _display_user_notes_key(match.group(1))
    value = re.sub(r"\s+", " ", match.group(2).strip())
    if not key or not value:
        return None
    return key, value


def _should_compact_user_notes(content: str) -> bool:
    stripped_lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(content) > _USER_NOTES_COMPACT_MIN_CHARS:
        return True
    if len(stripped_lines) > _USER_NOTES_COMPACT_MIN_LINES:
        return True

    scalar_keys: set[str] = set()
    item_keys: set[tuple[str, str]] = set()
    section = ""
    for line in stripped_lines:
        if _is_user_notes_section_header(line):
            section = _display_user_notes_key(line[:-1])
            continue
        scalar = _user_notes_scalar(line)
        if scalar:
            key, _value = scalar
            if key in scalar_keys:
                return True
            scalar_keys.add(key)
            section = ""
            continue
        fingerprint = _fingerprint_user_notes_item(line)
        item_key = (section, fingerprint)
        if fingerprint and item_key in item_keys:
            return True
        item_keys.add(item_key)
    return False


def _compact_user_notes_content(content: str) -> str:
    """Rewrite user notes into a concise canonical form.

    The compactor is intentionally deterministic: no LLM call, no semantic
    guessing.  It removes duplicate lines, resolves duplicate scalar keys by
    keeping the latest value, and caps oversized list sections to the most
    recent items.  The raw pre-compaction write is committed first by
    ``save_user_notes``, so recoverability comes from git history.
    """
    scalar_values: dict[str, tuple[str, str, int]] = {}
    section_names: dict[str, tuple[str, int]] = {}
    section_items: dict[str, dict[str, tuple[str, int]]] = {}
    loose_items: dict[str, tuple[str, int]] = {}

    section = ""
    for idx, raw_line in enumerate(content.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if _is_user_notes_section_header(line):
            section = _display_user_notes_key(line[:-1])
            section_names[section] = (section, idx)
            section_items.setdefault(section, {})
            continue

        scalar = _user_notes_scalar(line)
        if scalar:
            key, value = scalar
            scalar_values[key] = (key, value, idx)
            section = ""
            continue

        normalized = _normalize_user_notes_item(line)
        if not normalized:
            continue
        fingerprint = _fingerprint_user_notes_item(normalized)
        if section:
            section_names.setdefault(section, (section, idx))
            section_items.setdefault(section, {})[fingerprint] = (normalized, idx)
        else:
            loose_items[fingerprint] = (normalized, idx)

    lines: list[str] = []
    scalar_order = {
        "timezone": 0,
        "name": 1,
    }
    for key, value, _idx in sorted(
        scalar_values.values(),
        key=lambda item: (scalar_order.get(item[0], 100), item[2]),
    ):
        lines.append(f"{key}: {value}")

    if loose_items:
        section_items.setdefault("notes", {}).update(loose_items)
        section_names.setdefault("notes", ("notes", len(content.splitlines())))

    preferred_sections = {
        "preferences": 0,
        "interests": 1,
        "notes": 99,
    }
    sorted_sections = sorted(
        section_items.items(),
        key=lambda item: (
            preferred_sections.get(item[0], 50),
            section_names.get(item[0], (item[0], 0))[1],
        ),
    )
    for section_key, items_by_fingerprint in sorted_sections:
        items = sorted(items_by_fingerprint.values(), key=lambda item: item[1])
        if not items:
            continue
        if lines:
            lines.append("")
        lines.append(f"{section_key}:")
        capped = items[-_USER_NOTES_SECTION_MAX_ITEMS:]
        for item, _idx in capped:
            lines.append(item)

    return "\n".join(lines).rstrip() + "\n" if lines else ""


def _extract_user_notes_list_items(content: str) -> list[tuple[str, str, str, int]]:
    """Return list-style notes as (section, item_text, fingerprint, line_index)."""
    items: list[tuple[str, str, str, int]] = []
    section = "notes"
    for idx, raw_line in enumerate(content.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if _is_user_notes_section_header(line):
            section = _display_user_notes_key(line[:-1])
            continue
        if _user_notes_scalar(line):
            section = "notes"
            continue

        normalized = _normalize_user_notes_item(line)
        if not normalized:
            continue
        item_text = normalized[2:] if normalized.startswith("- ") else normalized
        fingerprint = _fingerprint_user_notes_item(normalized)
        if fingerprint:
            items.append((section, item_text, fingerprint, idx))
    return items


def _user_note_promotion_candidate(
    section: str,
    item_text: str,
) -> _UserNotePromotionCandidate | None:
    """Classify a dropped user-note item for selective LTM promotion.

    This is intentionally conservative.  User-note compaction should not dump
    old clutter into semantic memory; it should only preserve durable,
    user-specific preferences, workflows, aliases, and project constraints.
    """
    text = re.sub(r"\s+", " ", item_text.strip())
    if not text or len(text) < 24:
        return None

    lower = text.lower()
    word_count = len(re.findall(r"[a-zA-Z]{3,}", text))
    if word_count < 4:
        return None

    transient_patterns = (
        r"\btoday\b", r"\btomorrow\b", r"\byesterday\b", r"\btonight\b",
        r"\bthis week\b", r"\bnext week\b", r"\btemporary\b",
        r"\bremind(er)?\b", r"\bschedule[sd]?\b", r"\bappointment\b",
        r"\bmeeting\b", r"\btodo\b", r"\bto-do\b", r"\bcurrently\b",
        r"\bright now\b", r"\bin progress\b", r"\b\d{4}-\d{2}-\d{2}\b",
    )
    if any(re.search(pattern, lower) for pattern in transient_patterns):
        return None

    section_key = _canonical_user_notes_key(section)
    durable_sections = {
        "preferences", "preference", "workflows", "workflow", "projects",
        "project", "tools", "tool", "aliases", "alias", "interests",
        "interest",
    }
    durable_phrases = (
        "user prefers", "prefers ", "likes ", "dislikes ", "wants ",
        "needs ", "always ", "never ", "default", "format", "style",
        "workflow", "when user says", "interpret ", "means ", "alias",
        "project", "uses ", "constraint", "tradeoff", "trade-off",
    )
    if section_key not in durable_sections and not any(phrase in lower for phrase in durable_phrases):
        return None

    tags = ["user_notes_compaction"]
    if "prefer" in lower or section_key in {"preferences", "preference"}:
        tags.append("preference")
    if "workflow" in lower or section_key in {"workflows", "workflow"}:
        tags.append("workflow")
    if "project" in lower or section_key in {"projects", "project"}:
        tags.append("project")
    if "alias" in lower or "when user says" in lower or "interpret " in lower:
        tags.append("alias")
    if "format" in lower or "style" in lower:
        tags.append("formatting")

    if len(text) > 500:
        text = text[:497].rstrip() + "..."
    content = f"From user_notes compaction ({_display_user_notes_key(section)}): {text}"
    return content, 0.65, tags


def _user_notes_ltm_promotion_candidates(
    original: str,
    compacted: str,
) -> list[_UserNotePromotionCandidate]:
    """Return durable dropped items worth preserving in LTM."""
    original_items = _extract_user_notes_list_items(original)
    retained_fingerprints = {
        fingerprint for _section, _item, fingerprint, _idx in _extract_user_notes_list_items(compacted)
    }

    candidates: list[_UserNotePromotionCandidate] = []
    seen: set[str] = set()
    for section, item_text, fingerprint, _idx in original_items:
        if fingerprint in retained_fingerprints or fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidate = _user_note_promotion_candidate(section, item_text)
        if not candidate:
            continue
        candidates.append(candidate)
        if len(candidates) >= _USER_NOTES_PROMOTION_MAX:
            break
    return candidates


def _promote_user_notes_ltm_candidates(
    user_id: str,
    candidates: list[_UserNotePromotionCandidate],
) -> None:
    """Store selected compaction drops in LTM when memory infra is available."""
    if not candidates:
        return
    try:
        from prax.services.memory_service import get_memory_service

        svc = get_memory_service()
        if not getattr(svc, "available", False):
            logger.info(
                "Skipped %d user_notes LTM promotion candidate(s): memory unavailable",
                len(candidates),
            )
            return

        promoted = 0
        for content, importance, tags in candidates:
            memory_id = svc.remember(
                user_id,
                content,
                source="user_notes_compaction",
                importance=importance,
                tags=tags,
            )
            if memory_id:
                promoted += 1
        if promoted:
            logger.info("Promoted %d user_notes compaction item(s) to LTM for %s", promoted, user_id)
    except Exception:
        logger.debug("User notes LTM promotion failed for %s", user_id, exc_info=True)


def _tokenize_user_notes_text(text: str) -> set[str]:
    """Return lightweight matching tokens for user-note retrieval."""
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_/-]{1,}", text.lower()))
    return {t for t in tokens if t not in _USER_NOTES_STOPWORDS}


def _is_user_notes_section_header(line: str) -> bool:
    return (
        line.endswith(":")
        and not line.startswith(("-", "*"))
        and len(line) <= 80
    )


def _build_relevant_user_notes_context(notes: str, user_input: str) -> str:
    """Select a bounded set of user-note snippets relevant to this turn.

    ``user_notes.md`` can grow over time.  Injecting the whole file every
    turn is both expensive and pollutes task interpretation, so this keeps
    retrieval deterministic and cheap: exact-ish token overlap plus a few
    high-signal heuristics for time/identity requests.
    """
    notes = notes.strip()
    if not notes or not user_input.strip():
        return ""

    query_tokens = _tokenize_user_notes_text(user_input)
    if not query_tokens:
        return ""

    time_query = bool(query_tokens & _USER_NOTES_TIME_QUERY_TOKENS)
    identity_query = bool(query_tokens & _USER_NOTES_IDENTITY_QUERY_TOKENS)

    matches: list[tuple[int, int, str]] = []
    section = ""
    for idx, raw_line in enumerate(notes.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if _is_user_notes_section_header(line):
            section = line[:-1].strip()
            continue

        line_tokens = _tokenize_user_notes_text(line)
        overlap = query_tokens & line_tokens
        score = len(overlap) * 4

        lower = line.lower()
        if time_query and ("timezone" in line_tokens or "timezone:" in lower):
            score += 8
        if identity_query and (lower.startswith("name:") or " name " in f" {lower} "):
            score += 8

        # Preserve short all-caps aliases such as NPR even when punctuation
        # makes token boundaries awkward.
        for token in query_tokens:
            if len(token) >= 3 and token in lower:
                score += 1

        if score <= 0:
            continue

        display = line
        if section and line.startswith(("-", "*")):
            display = f"{section}: {line}"
        matches.append((score, idx, display))

    if not matches:
        return ""

    selected = [
        line for _score, _idx, line in sorted(matches, key=lambda item: (-item[0], item[1]))[
            :_USER_NOTES_MAX_MATCHES
        ]
    ]

    rendered_lines: list[str] = []
    total = 0
    for line in selected:
        extra = len(line) + 3
        if total + extra > _USER_NOTES_CONTEXT_MAX_CHARS:
            break
        rendered_lines.append(f"- {line}")
        total += extra

    if not rendered_lines:
        return ""

    return (
        "\n\n## Relevant User Notes\n"
        "Only snippets matching the current request are injected. Use "
        "user_notes_read for a full notes lookup when the user asks about "
        "stored preferences or past personal context.\n"
        + "\n".join(rendered_lines)
    )


def append_link(user_id: str, url: str, description: str = "") -> str:
    """Append a link entry to links.md in the workspace root. Git commit."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        filepath = os.path.join(root, "links.md")

        existing = ""
        if os.path.isfile(filepath):
            with open(filepath, encoding="utf-8") as f:
                existing = f.read()

        if not existing:
            existing = "# Links\n\nAll links shared by this user.\n\n"

        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"- [{timestamp}] {url}"
        if description:
            entry += f" — {description}"
        entry += "\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(existing + entry)

        git_commit(root, f"Log link: {url[:50]}")
        logger.info("Logged link for %s: %s", user_id, url[:80])
        return filepath


def read_links(user_id: str) -> str:
    """Read links.md from the workspace root. Returns empty string if missing."""
    root = workspace_root(user_id)
    filepath = os.path.join(root, "links.md")
    if not os.path.isfile(filepath):
        return ""
    with open(filepath, encoding="utf-8") as f:
        return f.read()


def save_file(user_id: str, filename: str, content: str | bytes) -> str:
    """Save content to active/{filename}, git commit. Returns the file path.

    Accepts both text (str) and binary (bytes) content — binary is written
    in ``"wb"`` mode so plugins can save audio, images, etc.
    """
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        filepath = safe_join(root, "active", filename)
        if isinstance(content, bytes):
            with open(filepath, "wb") as f:
                f.write(content)
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        git_commit(root, f"Save {filename} to active workspace")
        logger.info("Saved %s to workspace for %s", filename, user_id)
        return filepath


def save_binary(user_id: str, filename: str, src_path: str) -> str:
    """Copy a binary file to archive/{filename}, git commit. Returns dest path."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        dest = safe_join(root, "archive", filename)
        shutil.copy2(src_path, dest)
        git_commit(root, f"Archive original: {filename}")
        logger.info("Archived binary %s for %s", filename, user_id)
        return dest


def read_file(user_id: str, filename: str) -> str:
    """Read a file from active/. Raises FileNotFoundError if missing."""
    root = workspace_root(user_id)
    filepath = safe_join(root, "active", filename)
    with open(filepath, encoding="utf-8") as f:
        return f.read()


_BUILD_ARTIFACT_EXTS = frozenset({
    ".aux", ".log", ".nav", ".snm", ".toc", ".out",
    ".synctex.gz", ".fls", ".fdb_latexmk",
})


def list_active(user_id: str) -> list[str]:
    """List filenames in active/. Filters out hidden files, dirs, and build artifacts."""
    root = workspace_root(user_id)
    active_dir = os.path.join(root, "active")
    if not os.path.isdir(active_dir):
        return []
    results = []
    for f in os.listdir(active_dir):
        if f.startswith("."):
            continue
        if os.path.isdir(os.path.join(active_dir, f)):
            continue
        if any(f.endswith(ext) for ext in _BUILD_ARTIFACT_EXTS):
            continue
        results.append(f)
    return sorted(results)


def archive_file(user_id: str, filename: str) -> str:
    """Move file from active/ to archive/. Git commit. Returns new path."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        src = safe_join(root, "active", filename)
        dst = safe_join(root, "archive", filename)
        if not os.path.exists(src):
            raise FileNotFoundError(f"{filename} not found in active workspace")
        shutil.move(src, dst)
        git_commit(root, f"Archive {filename}: moved from active to archive")
        logger.info("Archived %s for %s", filename, user_id)
        return dst


def search_archive(user_id: str, query: str) -> list[dict]:
    """Grep archive/ for query. Returns list of {filename, snippet} dicts."""
    root = workspace_root(user_id)
    archive_dir = os.path.join(root, "archive")
    if not os.path.isdir(archive_dir):
        return []
    results = []
    try:
        proc = subprocess.run(
            ["grep", "-ril", "--include=*.md", "--", query, archive_dir],
            capture_output=True, text=True, timeout=10,
        )
        for filepath in proc.stdout.strip().splitlines():
            if not filepath:
                continue
            fname = os.path.basename(filepath)
            snippet_proc = subprocess.run(
                ["grep", "-i", "-m", "3", "-C", "1", "--", query, filepath],
                capture_output=True, text=True, timeout=5,
            )
            results.append({"filename": fname, "snippet": snippet_proc.stdout.strip()[:500]})
    except subprocess.TimeoutExpired:
        logger.warning("Archive search timed out for user %s query '%s'", user_id, query)
    return results


def restore_file(user_id: str, filename: str) -> str:
    """Move file from archive/ to active/. Git commit. Returns new path."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        src = safe_join(root, "archive", filename)
        dst = safe_join(root, "active", filename)
        if not os.path.exists(src):
            raise FileNotFoundError(f"{filename} not found in archive")
        shutil.move(src, dst)
        git_commit(root, f"Restore {filename}: moved from archive to active")
        logger.info("Restored %s for %s", filename, user_id)
        return dst


# ---------------------------------------------------------------------------
# User todo list
# ---------------------------------------------------------------------------

def _todos_path(user_id: str) -> str:
    return os.path.join(workspace_root(user_id), "todos.yaml")


def _read_todos(user_id: str) -> list[dict]:
    path = _todos_path(user_id)
    # Migrate: read legacy .json if .yaml doesn't exist yet.
    if not os.path.isfile(path):
        legacy = os.path.join(workspace_root(user_id), "todos.json")
        if os.path.isfile(legacy):
            with open(legacy, encoding="utf-8") as f:
                return json.load(f)
        return []
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _write_todos(user_id: str, todos: list[dict]) -> None:
    root = ensure_workspace(user_id)
    with open(os.path.join(root, "todos.yaml"), "w", encoding="utf-8") as f:
        yaml.dump(todos, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    # Remove legacy .json if it exists.
    legacy = os.path.join(root, "todos.json")
    if os.path.isfile(legacy):
        os.remove(legacy)
    git_commit(root, "Update todos")


def add_todo(user_id: str, task: str, *, assignee: str = "user") -> dict:
    """Add a task to the user's todo list.

    ``assignee`` defaults to "user" (human handles it). Pass "prax" to
    let the task-runner auto-pick it up when ``task_runner_enabled``.
    """
    with get_lock(user_id):
        ensure_workspace(user_id)
        todos = _read_todos(user_id)
        entry = {
            "id": len(todos) + 1,
            "task": task,
            "done": False,
            "assignee": assignee,
            "created_at": datetime.now(UTC).isoformat(),
        }
        todos.append(entry)
        # Re-number sequentially.
        for i, t in enumerate(todos):
            t["id"] = i + 1
        _write_todos(user_id, todos)
    return entry


def list_todos(user_id: str, show_completed: bool = False) -> list[dict]:
    """Return the user's todo list. By default hides completed items."""
    todos = _read_todos(user_id)
    if not show_completed:
        todos = [t for t in todos if not t.get("done", False)]
    return todos


def complete_todo(user_id: str, item_ids: list[int]) -> dict:
    """Mark one or more todo items as completed."""
    with get_lock(user_id):
        todos = _read_todos(user_id)
        completed = []
        for t in todos:
            if t["id"] in item_ids:
                t["done"] = True
                t["completed_at"] = datetime.now(UTC).isoformat()
                completed.append(t["id"])
        if not completed:
            return {"error": f"No todos found with ids {item_ids}"}
        _write_todos(user_id, todos)
    return {"status": "completed", "ids": completed}


def remove_todos(user_id: str, item_ids: list[int]) -> dict:
    """Remove items from the todo list entirely and re-number."""
    with get_lock(user_id):
        todos = _read_todos(user_id)
        original_len = len(todos)
        todos = [t for t in todos if t["id"] not in item_ids]
        if len(todos) == original_len:
            return {"error": f"No todos found with ids {item_ids}"}
        # Re-number sequentially.
        for i, t in enumerate(todos):
            t["id"] = i + 1
        _write_todos(user_id, todos)
    return {"status": "removed", "remaining": len(todos)}


# ---------------------------------------------------------------------------
# Agent internal plan / task decomposition
# ---------------------------------------------------------------------------

def _plan_path(user_id: str) -> str:
    return os.path.join(workspace_root(user_id), "agent_plan.yaml")


def _write_plan(root: str, plan: dict) -> None:
    with open(os.path.join(root, "agent_plan.yaml"), "w", encoding="utf-8") as f:
        yaml.dump(plan, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    # Remove legacy .json if it exists.
    legacy = os.path.join(root, "agent_plan.json")
    if os.path.isfile(legacy):
        os.remove(legacy)


_VALID_PLAN_CONFIDENCE = {"low", "medium", "high"}


def create_plan(
    user_id: str,
    goal: str,
    steps: list[str],
    *,
    confidence: str = "medium",
) -> dict:
    """Create a multi-step plan for a complex request.

    ``confidence`` is Prax's self-reported hint ("low" / "medium" /
    "high") about how sure he is the plan is correct and complete.
    This is a situational-awareness signal for the user — it is NOT
    a calibrated probability and should not be used for automated
    gating.  See ``docs/research/prax-changes-from-todo-research.md``
    (P2) for the rationale.
    """
    if confidence not in _VALID_PLAN_CONFIDENCE:
        confidence = "medium"
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        plan = {
            "id": f"plan-{uuid.uuid4().hex[:6]}",
            "goal": goal,
            "confidence": confidence,
            "steps": [
                {"step": i + 1, "description": s, "done": False}
                for i, s in enumerate(steps)
            ],
            "created_at": datetime.now(UTC).isoformat(),
        }
        _write_plan(root, plan)
        git_commit(root, f"Plan: {goal[:40]}")
    return plan


def read_plan(user_id: str) -> dict | None:
    """Read the current plan. Returns None if no plan exists."""
    path = _plan_path(user_id)
    # Migrate: read legacy .json if .yaml doesn't exist yet.
    if not os.path.isfile(path):
        legacy = os.path.join(workspace_root(user_id), "agent_plan.json")
        if os.path.isfile(legacy):
            with open(legacy, encoding="utf-8") as f:
                return json.load(f)
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def complete_plan_step(user_id: str, step: int) -> dict:
    """Mark a plan step as done."""
    with get_lock(user_id):
        plan = read_plan(user_id)
        if not plan:
            return {"error": "No active plan"}
        for s in plan["steps"]:
            if s["step"] == step:
                s["done"] = True
                root = ensure_workspace(user_id)
                _write_plan(root, plan)
                git_commit(root, f"Step {step} done: {s['description'][:30]}")
                return {"status": "completed", "step": s}
        return {"error": f"Step {step} not found"}


def clear_plan(user_id: str) -> dict:
    """Remove the current plan (call when all steps are done)."""
    path = _plan_path(user_id)
    if os.path.isfile(path):
        os.remove(path)
        root = workspace_root(user_id)
        git_commit(root, "Plan completed")
    # Also clean up legacy .json.
    legacy = os.path.join(workspace_root(user_id), "agent_plan.json")
    if os.path.isfile(legacy):
        os.remove(legacy)
        root = workspace_root(user_id)
        git_commit(root, "Plan completed")
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Instructions reference (so the agent can re-read its prompt)
# ---------------------------------------------------------------------------

def save_instructions(user_id: str, content: str) -> None:
    """Write the system prompt to instructions.md in the workspace root.

    Only writes if the content has actually changed (avoids noisy git history).
    """
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        filepath = os.path.join(root, "instructions.md")
        if os.path.isfile(filepath):
            with open(filepath, encoding="utf-8") as f:
                if f.read() == content:
                    return
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        git_commit(root, "Update instructions reference")


def read_instructions(user_id: str) -> str:
    """Read the instructions reference file. Returns empty string if missing."""
    root = workspace_root(user_id)
    filepath = os.path.join(root, "instructions.md")
    if not os.path.isfile(filepath):
        return ""
    with open(filepath, encoding="utf-8") as f:
        return f.read()


def get_workspace_context(user_id: str, user_input: str = "") -> str:
    """Build a context string for the system prompt listing active workspace files.

    ``user_notes.md`` is not injected wholesale.  Only a bounded set of
    request-relevant snippets is included, keeping the full file available
    through ``user_notes_read`` without paying its context cost every turn.
    """
    parts: list[str] = []

    # Load only relevant user-note snippets if they exist.
    root = workspace_root(user_id)
    notes_path = os.path.join(root, "user_notes.md")
    if os.path.isfile(notes_path):
        try:
            with open(notes_path, encoding="utf-8") as f:
                notes = f.read()
            notes_context = _build_relevant_user_notes_context(notes, user_input)
            if notes_context:
                parts.append(notes_context)
        except Exception:
            pass

    # Load active plan if one exists.
    #
    # Compact rendering for large plans: when a plan has more than 6
    # steps or its steps total more than 800 characters of description,
    # we show a trimmed view instead of inlining everything — the goal,
    # the current step in full, the next 2 steps brief, and a pointer
    # to ``agent_plan_status`` for the full list.  Long plans otherwise
    # burn a significant chunk of every turn's context on stale steps
    # Prax has already completed.  This matches the "externalize
    # working memory" finding in docs/research/agentic-todo-flows.md §25.
    plan = read_plan(user_id)
    if plan:
        steps = plan.get("steps", [])
        done_count = sum(1 for s in steps if s.get("done"))
        total = len(steps)
        goal = plan.get("goal", "(unknown)")
        confidence = plan.get("confidence", "medium")

        # Decide full vs compact rendering
        total_chars = sum(len(s.get("description", "")) for s in steps)
        PLAN_STEP_LIMIT = 6
        PLAN_CHAR_LIMIT = 800
        compact = total > PLAN_STEP_LIMIT or total_chars > PLAN_CHAR_LIMIT

        if not compact:
            steps_text = [
                f"  [{'x' if s.get('done') else ' '}] {s['step']}. {s['description']}"
                for s in steps
            ]
            parts.append(
                f"\n\n## Active Plan ({done_count}/{total} done — confidence: {confidence})\n"
                f"Goal: {goal}\n"
                + "\n".join(steps_text)
                + "\n\nContinue working through this plan. Mark steps done "
                "with agent_step_done as you complete them. Do NOT respond "
                "to the user about completed work until the relevant plan "
                "steps are actually done."
            )
        else:
            # Compact rendering
            current_idx = next(
                (i for i, s in enumerate(steps) if not s.get("done")),
                total,  # all done
            )
            lines = [
                f"\n\n## Active Plan ({done_count}/{total} done — confidence: {confidence})",
                f"Goal: {goal}",
                "",
            ]
            if current_idx < total:
                current = steps[current_idx]
                lines.append(
                    f"Current step: [{current['step']}] {current['description']}"
                )
                # Show the next 2 upcoming steps briefly
                upcoming = steps[current_idx + 1 : current_idx + 3]
                if upcoming:
                    lines.append("Next up:")
                    for s in upcoming:
                        desc = s.get("description", "")
                        if len(desc) > 80:
                            desc = desc[:77] + "…"
                        lines.append(f"  - [{s['step']}] {desc}")
                remaining = total - current_idx - 1 - len(upcoming)
                if remaining > 0:
                    lines.append(
                        f"  - … and {remaining} more step(s)"
                    )
            else:
                lines.append("All steps marked done. Call agent_plan_clear.")
            lines.append("")
            lines.append(
                "[Plan compacted to save context. Use agent_plan_status "
                "to see every step and their done/not-done state. Mark "
                "steps done with agent_step_done as you complete them.]"
            )
            parts.append("\n".join(lines))

    files = list_active(user_id)
    if files:
        parts.append(
            "\n\n## Active Workspace\n"
            f"The user has {len(files)} file(s) in their active workspace. "
            "Use workspace_list to see them, workspace_read to read one, "
            "workspace_send_file to deliver a file to the user (PDF, image, etc.), "
            "or workspace_archive when the conversation has moved on.\n"
            "Use workspace_search to find past documents in the archive "
            "and workspace_restore to bring them back."
        )

    # Proactive engagement: drain the pending-engagement queue so Prax
    # can offer to refine / expand notes the human just unlocked.  This
    # drains once — on the next turn the queue will be empty again so
    # Prax doesn't keep nagging.
    try:
        from prax.services.library_service import pop_pending_engagements
        engagements = pop_pending_engagements(user_id)
        if engagements:
            lines = [
                "\n\n## Proactive engagement — notes just unlocked for you",
                "The user flipped `prax_may_edit` to true on the following "
                "human-authored notes since your last turn. Read each one "
                "and proactively offer to refine, expand, fact-check, or "
                "add to it. Don't wait to be asked — the unlock is the ask. "
                "Use library_note_read to pull the full content, then respond "
                "conversationally.",
                "",
            ]
            for e in engagements[:5]:
                lines.append(
                    f"- **{e.get('title', e.get('slug'))}** "
                    f"(`{e.get('project')}/{e.get('notebook')}/{e.get('slug')}`) "
                    f"— unlocked at {e.get('queued_at', '?')}"
                )
            if len(engagements) > 5:
                lines.append(f"- … and {len(engagements) - 5} more")
            parts.append("\n".join(lines))
    except Exception:
        pass

    return "".join(parts)


# ---------------------------------------------------------------------------
# SSH key + git remote push
# ---------------------------------------------------------------------------

_ssh_key_file: str | None = None
_ssh_key_lock = threading.Lock()


def _write_ssh_key() -> str | None:
    """Decode PRAX_SSH_KEY_B64 to a temp file. Returns path, or None if not configured."""
    global _ssh_key_file
    with _ssh_key_lock:
        if _ssh_key_file and os.path.exists(_ssh_key_file):
            return _ssh_key_file
        key_b64 = settings.ssh_key_b64
        if not key_b64:
            return None
        key_bytes = base64.b64decode(key_b64)
        fd, path = tempfile.mkstemp(prefix="prax_ssh_key_", suffix=".pem")
        os.write(fd, key_bytes)
        os.close(fd)
        os.chmod(path, 0o600)
        _ssh_key_file = path
        return path


def _git_ssh_env() -> dict[str, str] | None:
    """Return env dict with GIT_SSH_COMMAND set, or None if no SSH key configured."""
    key_path = _write_ssh_key()
    if not key_path:
        return None
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {key_path} -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null"
    )
    return env


def _run_git_ssh(
    *args: str, cwd: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run a git command with SSH key configured."""
    if env is None:
        env = _git_ssh_env()
    cmd = ["git"] + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd,
        env=env, timeout=60,
    )


def _verify_remote_is_private(remote_url: str) -> bool:
    """Check that a remote URL points to a private repo.

    Uses the GitHub/GitLab public API. Returns True if private (or can't determine
    for unknown hosts). Returns False if confirmed public.
    """
    import urllib.error
    import urllib.request as _req

    # Parse the URL to extract host/owner/repo.
    m = re.match(r"^git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$", remote_url.strip())
    if not m:
        m = re.match(
            r"^(?:https?|ssh)://(?:git@)?([^/]+)/([^/]+)/([^/]+?)(?:\.git)?$",
            remote_url.strip(),
        )
    if not m:
        logger.warning("Cannot parse remote URL %s — refusing push to be safe", remote_url)
        return False

    host, owner, name = m.group(1), m.group(2), m.group(3)

    if "github.com" in host:
        api_url = f"https://api.github.com/repos/{owner}/{name}"
    elif "gitlab.com" in host:
        api_url = (
            f"https://gitlab.com/api/v4/projects/"
            f"{_req.quote(f'{owner}/{name}', safe='')}"
        )
    else:
        logger.info("Unknown host %s — cannot verify visibility, refusing push", host)
        return False

    try:
        req = _req.Request(api_url, headers={"User-Agent": "prax-workspace"})
        with _req.urlopen(req, timeout=10) as resp:
            import json as _json
            data = _json.loads(resp.read())
            if "github.com" in host:
                is_private = data.get("private", False)
            else:
                is_private = data.get("visibility") != "public"
            if not is_private:
                logger.error("Remote %s/%s on %s is PUBLIC — refusing push", owner, name, host)
            return is_private
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True  # Not publicly visible = private.
        logger.warning("Visibility check failed (HTTP %d) — refusing push", e.code)
        return False
    except Exception:
        logger.warning("Could not verify remote visibility — refusing push", exc_info=True)
        return False


def set_remote(user_id: str, remote_url: str) -> dict:
    """Set the git remote 'origin' for a user's workspace.

    Verifies the remote is a private repo before setting it.
    """
    if not _verify_remote_is_private(remote_url):
        return {"error": "Remote repo is public (or visibility could not be verified). "
                "Only private repos are allowed for workspace push."}

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        # Check if origin already exists.
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            # Remote exists — update it.
            subprocess.run(
                ["git", "remote", "set-url", "origin", remote_url],
                cwd=root, check=True, capture_output=True,
            )
        else:
            subprocess.run(
                ["git", "remote", "add", "origin", remote_url],
                cwd=root, check=True, capture_output=True,
            )
    return {"status": "remote_set", "url": remote_url}


def push(user_id: str) -> dict:
    """Push the workspace to its remote using the configured SSH key.

    Requires: PRAX_SSH_KEY_B64 set in .env and a remote configured via set_remote().
    """
    env = _git_ssh_env()
    if not env:
        return {"error": "No SSH key configured. Set PRAX_SSH_KEY_B64 in .env."}

    with get_lock(user_id):
        root = ensure_workspace(user_id)

        # Check remote is configured.
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {"error": "No remote configured. Use workspace_set_remote first."}

        remote_url = result.stdout.strip()
        if not _verify_remote_is_private(remote_url):
            return {"error": "Remote repo is public — refusing to push."}

        # Commit any pending changes.
        git_commit(root, "Workspace sync")

        # Get current branch name.
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root, capture_output=True, text=True,
        )
        branch = branch_result.stdout.strip() or "main"

        # Push.
        result = _run_git_ssh("push", "-u", "origin", branch, cwd=root, env=env)
        if result.returncode != 0:
            return {"error": f"Push failed: {result.stderr[:300]}"}

    return {"status": "pushed", "branch": branch}


# ---------------------------------------------------------------------------
# Plugin import via git submodule
# ---------------------------------------------------------------------------

# Patterns that warrant a security warning when found in plugin code.
_SECURITY_PATTERNS: list[tuple[str, str]] = [
    (r"\bsubprocess\b", "subprocess calls — may execute arbitrary shell commands"),
    (r"\bos\.system\b", "os.system — executes shell commands"),
    (r"\bos\.popen\b", "os.popen — executes shell commands"),
    (r"\beval\s*\(", "eval() — executes arbitrary Python code"),
    (r"\bexec\s*\(", "exec() — executes arbitrary Python code"),
    (r"\b__import__\s*\(", "dynamic import — may load arbitrary modules"),
    (r"\brequests\.(get|post|put|delete|patch)\b", "HTTP requests to external services"),
    (r"\burllib\.request\b", "HTTP requests to external services"),
    (r"\bhttpx\b", "HTTP requests to external services"),
    (r"\bsocket\b", "raw socket access — may open network connections"),
    (r"\bos\.environ\b", "reads environment variables — may access secrets"),
    (r"\bopen\s*\(.*/etc/", "reads system files outside workspace"),
    (r"\bos\.remove\b|\bos\.unlink\b|\bshutil\.rmtree\b", "file deletion operations"),
    (r"\bbase64\.b64decode\b", "base64 decoding — may hide obfuscated code"),
    (r"\\x[0-9a-fA-F]{2}", "hex-escaped strings — possibly obfuscated code"),
    # --- evasion patterns (Phase 1 sandbox hardening) ---
    (r"getattr\s*\([^,]*,\s*['\"]environ['\"]", "getattr(…, 'environ') — env access evasion"),
    (r"\bvars\s*\(\s*os\s*\)", "vars(os) — env access evasion via vars()"),
    (r"\bos\.__dict__\b", "os.__dict__ — env access evasion via __dict__"),
    (r"\bimportlib\.import_module\b", "importlib.import_module — dynamic import evasion"),
    (r"\bsys\.modules\[", "sys.modules[ — module injection / access evasion"),
    (r"\b__globals__\b", "access to function __globals__ — may leak secrets"),
    (r"\b__subclasses__\b", "__subclasses__() — sandbox escape via class hierarchy"),
    (r"\b__bases__\b", "__bases__ — sandbox escape via class hierarchy"),
    (r"\bctypes\b", "ctypes — native code execution / memory access"),
    (r"\bpickle\b", "pickle — arbitrary code execution via deserialization"),
    (r"\bmarshal\b", "marshal — bytecode manipulation / code execution"),
    (r"\bopen\s*\(\s*['\"]/?proc/self/environ['\"]", "open('/proc/self/environ') — env access evasion via procfs"),
    (r"\bcodecs\.decode\b", "codecs.decode — potential ROT13/obfuscation evasion"),
]


def _ast_scan(source: str, rel_path: str = "<unknown>") -> list[dict]:
    """Parse *source* with the ``ast`` module and look for dangerous patterns.

    Returns findings in the same format as :func:`scan_plugin_security`:
    ``[{"file", "line", "pattern", "code"}, ...]``
    """
    import ast as _ast

    findings: list[dict] = []
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return findings

    source_lines = source.splitlines()

    def _code_at(lineno: int) -> str:
        if 1 <= lineno <= len(source_lines):
            return source_lines[lineno - 1].strip()[:120]
        return ""

    # Dangerous built-in function names.
    _DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}

    # Dangerous module imports (AST).
    _DANGEROUS_IMPORTS = {
        "subprocess", "socket", "ctypes", "pickle", "marshal",
    }

    for node in _ast.walk(tree):
        # 1. import <dangerous> / from <dangerous> import ...
        if isinstance(node, _ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if base in _DANGEROUS_IMPORTS:
                    findings.append({
                        "file": rel_path,
                        "line": node.lineno,
                        "pattern": f"AST: import {alias.name} — dangerous module",
                        "code": _code_at(node.lineno),
                    })
        elif isinstance(node, _ast.ImportFrom):
            if node.module:
                base = node.module.split(".")[0]
                if base in _DANGEROUS_IMPORTS:
                    findings.append({
                        "file": rel_path,
                        "line": node.lineno,
                        "pattern": f"AST: from {node.module} import — dangerous module",
                        "code": _code_at(node.lineno),
                    })

        # 2. Calls to eval/exec/compile/__import__, os.system, os.popen
        if isinstance(node, _ast.Call):
            func = node.func
            # Plain name calls: eval(...), exec(...), etc.
            if isinstance(func, _ast.Name) and func.id in _DANGEROUS_CALLS:
                findings.append({
                    "file": rel_path,
                    "line": node.lineno,
                    "pattern": f"AST: {func.id}() — executes arbitrary code",
                    "code": _code_at(node.lineno),
                })
            # Attribute calls: os.system(...), os.popen(...)
            elif isinstance(func, _ast.Attribute):
                if (
                    isinstance(func.value, _ast.Name)
                    and func.value.id == "os"
                    and func.attr in ("system", "popen")
                ):
                    findings.append({
                        "file": rel_path,
                        "line": node.lineno,
                        "pattern": f"AST: os.{func.attr}() — executes shell commands",
                        "code": _code_at(node.lineno),
                    })

                # importlib.import_module(...)
                if (
                    isinstance(func.value, _ast.Name)
                    and func.value.id == "importlib"
                    and func.attr == "import_module"
                ):
                    findings.append({
                        "file": rel_path,
                        "line": node.lineno,
                        "pattern": "AST: importlib.import_module() — dynamic import evasion",
                        "code": _code_at(node.lineno),
                    })

            # getattr(__builtins__, ...) or getattr(os, 'environ')
            if isinstance(func, _ast.Name) and func.id == "getattr":
                if node.args:
                    first_arg = node.args[0]
                    if isinstance(first_arg, _ast.Name) and first_arg.id == "__builtins__":
                        findings.append({
                            "file": rel_path,
                            "line": node.lineno,
                            "pattern": "AST: getattr(__builtins__) — may bypass restrictions",
                            "code": _code_at(node.lineno),
                        })
                    # getattr(os, 'environ') or getattr(x, 'environ')
                    if len(node.args) >= 2 and isinstance(node.args[1], _ast.Constant):
                        attr_name = node.args[1].value
                        if isinstance(attr_name, str) and attr_name == "environ":
                            findings.append({
                                "file": rel_path,
                                "line": node.lineno,
                                "pattern": "AST: getattr(…, 'environ') — env access evasion",
                                "code": _code_at(node.lineno),
                            })

            # vars(os) — access evasion
            if isinstance(func, _ast.Name) and func.id == "vars":
                if node.args and isinstance(node.args[0], _ast.Name) and node.args[0].id == "os":
                    findings.append({
                        "file": rel_path,
                        "line": node.lineno,
                        "pattern": "AST: vars(os) — env access evasion via vars()",
                        "code": _code_at(node.lineno),
                    })

        # 3. Attribute access patterns
        if isinstance(node, _ast.Attribute):
            # os.environ
            if (
                isinstance(node.value, _ast.Name)
                and node.value.id == "os"
                and node.attr == "environ"
            ):
                findings.append({
                    "file": rel_path,
                    "line": node.lineno,
                    "pattern": "AST: os.environ — reads environment variables / secrets",
                    "code": _code_at(node.lineno),
                })

            # os.__dict__
            if (
                isinstance(node.value, _ast.Name)
                and node.value.id == "os"
                and node.attr == "__dict__"
            ):
                findings.append({
                    "file": rel_path,
                    "line": node.lineno,
                    "pattern": "AST: os.__dict__ — env access evasion via __dict__",
                    "code": _code_at(node.lineno),
                })

            # __globals__, __subclasses__, __bases__
            if node.attr in ("__globals__", "__subclasses__", "__bases__"):
                findings.append({
                    "file": rel_path,
                    "line": node.lineno,
                    "pattern": f"AST: {node.attr} — sandbox escape via introspection",
                    "code": _code_at(node.lineno),
                })

        # 4. Subscript: sys.modules[...]
        if isinstance(node, _ast.Subscript):
            if (
                isinstance(node.value, _ast.Attribute)
                and isinstance(node.value.value, _ast.Name)
                and node.value.value.id == "sys"
                and node.value.attr == "modules"
            ):
                findings.append({
                    "file": rel_path,
                    "line": node.lineno,
                    "pattern": "AST: sys.modules[] — module injection / access evasion",
                    "code": _code_at(node.lineno),
                })

    return findings


def scan_plugin_security(plugin_dir: str, subfolder: str | None = None) -> list[dict]:
    """Scan plugin Python files for potentially risky patterns.

    Returns a list of findings, each with 'file', 'line', 'pattern', and 'code'.
    An empty list means no concerns were found.

    Performs two passes:
      1. Regex-based line scanning (original patterns).
      2. AST-based tree walking (catches patterns regex may miss).
    """
    scan_root = plugin_dir
    if subfolder:
        scan_root = os.path.join(plugin_dir, subfolder)

    findings: list[dict] = []
    if not os.path.isdir(scan_root):
        return findings

    for dirpath, _dirs, files in os.walk(scan_root):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(fpath, plugin_dir)
            try:
                with open(fpath) as f:
                    source = f.read()
            except Exception:
                continue

            # Pass 1: regex scan.
            lines = source.splitlines(keepends=True)
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip comments.
                if stripped.startswith("#"):
                    continue
                for pattern, description in _SECURITY_PATTERNS:
                    if re.search(pattern, line):
                        findings.append({
                            "file": rel_path,
                            "line": i,
                            "pattern": description,
                            "code": stripped[:120],
                        })

            # Pass 2: AST scan.
            findings.extend(_ast_scan(source, rel_path))

    return findings


def _parse_plugin_url(raw_url: str) -> tuple[str, str | None]:
    """Parse a plugin URL into (git_repo_url, subfolder | None).

    Supports:
      - ``https://github.com/owner/repo``                          → (url, None)
      - ``https://github.com/owner/repo/tree/branch/subfolder``    → (url, subfolder)
      - ``https://github.com/owner/repo.git``                      → (url, None)
      - ``git@github.com:owner/repo.git``                          → (url, None)

    The ``/tree/<branch>/<path>`` pattern is how GitHub represents subfolder
    links.  We strip it to get the bare repo URL and extract the path.
    """
    url = raw_url.strip().rstrip("/")

    # GitHub / GitLab subfolder link: .../tree/<branch>/<path>
    m = re.match(
        r"(https?://[^/]+/[^/]+/[^/]+?)"   # repo root
        r"(?:\.git)?"
        r"/tree/[^/]+/"                       # /tree/<branch>/
        r"(.+)",                              # subfolder path
        url,
    )
    if m:
        return m.group(1), m.group(2).strip("/")

    # Plain HTTPS URL — anything beyond owner/repo is a subfolder hint
    m = re.match(
        r"(https?://[^/]+/[^/]+/[^/]+?)"    # repo root
        r"(?:\.git)?$",                       # optional .git suffix, end of string
        url,
    )
    if m:
        return m.group(1), None

    # SSH shorthand: git@host:owner/repo.git
    if url.startswith("git@"):
        return url, None

    return url, None


def import_plugin_repo(
    user_id: str,
    repo_url: str,
    name: str | None = None,
    plugin_subfolder: str | None = None,
) -> dict:
    """Import a shared plugin repository as a git submodule.

    The repo is cloned into ``plugins/shared/<name>/`` within the workspace.
    Public repos are fine here (read-only import), unlike workspace push which
    requires a private remote.

    Multi-plugin repos are supported: if the repo contains multiple plugin
    subfolders (each with its own ``plugin.py``), the caller can specify
    *plugin_subfolder* to load only that one.  If omitted, the plugin loader
    will discover all ``plugin.py`` files within the cloned repo automatically.

    Args:
        user_id: The user whose workspace to import into.
        repo_url: Git URL of the plugin repo (HTTPS, SSH, or a GitHub subfolder link).
        name: Optional name for the submodule directory. Auto-derived from URL if omitted.
        plugin_subfolder: Optional subfolder within the repo to treat as the active plugin.
                          Stored in a ``.prax_plugin_filter`` file so the loader knows
                          which subfolder(s) to use.
    """
    # Parse the URL — it might contain a subfolder hint (GitHub /tree/ links).
    git_url, url_subfolder = _parse_plugin_url(repo_url)
    subfolder = plugin_subfolder or url_subfolder  # explicit arg wins

    # Derive name from the repo URL (not the subfolder).
    if not name:
        m = re.search(r"/([^/]+?)(?:\.git)?$", git_url.strip())
        if m:
            name = m.group(1)
        else:
            return {"error": f"Could not derive plugin name from URL: {repo_url}"}

    # Sanitize name — alphanumeric, hyphens, underscores only.
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    if not safe_name:
        return {"error": f"Invalid plugin name: {name}"}

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        submodule_path = os.path.join("plugins", "shared", safe_name)
        abs_submodule_path = os.path.join(root, submodule_path)

        if os.path.isdir(abs_submodule_path):
            return {"error": f"Plugin '{safe_name}' already exists. Remove it first to re-import."}

        # GIT_TERMINAL_PROMPT=0 prevents git from hanging on auth prompts
        # (e.g. if the repo doesn't exist or is private).
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        result = subprocess.run(
            ["git", "submodule", "add", git_url, submodule_path],
            cwd=root, capture_output=True, text=True, timeout=120, env=env,
        )
        if result.returncode != 0:
            return {"error": f"Submodule add failed: {result.stderr[:300]}"}

        # --- Security scan ---
        # Scan the cloned code BEFORE committing.  Return warnings so the
        # calling tool / LLM can decide whether to proceed.
        security_warnings = scan_plugin_security(abs_submodule_path, subfolder)

        # If a specific subfolder was requested, write a filter file so the
        # plugin loader only activates that subfolder.  If no subfolder, the
        # loader scans the whole repo for plugin.py files.
        # NOTE: The filter file lives NEXT TO the submodule (not inside it)
        # to avoid modifying the submodule's working tree, which breaks git add.
        if subfolder:
            filter_path = os.path.join(
                root, "plugins", "shared", f".{safe_name}_plugin_filter"
            )
            with open(filter_path, "w") as f:
                f.write(subfolder.strip("/") + "\n")

        msg = f"Import shared plugin: {safe_name}"
        if subfolder:
            msg += f" (subfolder: {subfolder})"
        git_commit(root, msg)

    result = {
        "status": "imported",
        "name": safe_name,
        "path": submodule_path,
        "url": git_url,
        "subfolder": subfolder,
        "security_warnings": security_warnings,
    }

    # If there are security warnings, the plugin is NOT activated until
    # the user explicitly acknowledges them.
    if security_warnings:
        result["requires_acknowledgement"] = True
        result["message"] = (
            f"Plugin '{safe_name}' imported but NOT activated — "
            f"{len(security_warnings)} security warning(s) found. "
            "Call acknowledge_warnings() to activate."
        )
    return result


def remove_plugin_repo(user_id: str, name: str) -> dict:
    """Remove a shared plugin submodule from the workspace."""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        submodule_path = os.path.join("plugins", "shared", safe_name)
        abs_submodule_path = os.path.join(root, submodule_path)

        if not os.path.isdir(abs_submodule_path):
            return {"error": f"Plugin '{safe_name}' not found."}

        # Remove submodule.
        subprocess.run(
            ["git", "submodule", "deinit", "-f", submodule_path],
            cwd=root, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "rm", "-f", submodule_path],
            cwd=root, capture_output=True, text=True,
        )
        # Clean up .git/modules entry.
        git_modules = os.path.join(root, ".git", "modules", submodule_path)
        if os.path.isdir(git_modules):
            shutil.rmtree(git_modules)

        git_commit(root, f"Remove shared plugin: {safe_name}")

    return {"status": "removed", "name": safe_name}


def update_plugin_repo(user_id: str, name: str) -> dict:
    """Pull the latest version of an imported shared plugin.

    Runs ``git submodule update --remote`` to fetch the newest commit from
    the plugin's upstream branch, then re-runs the security scan.

    Returns a dict with status, changed files, and any security warnings.
    """
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        submodule_path = os.path.join("plugins", "shared", safe_name)
        abs_submodule_path = os.path.join(root, submodule_path)

        if not os.path.isdir(abs_submodule_path):
            return {"error": f"Plugin '{safe_name}' not found."}

        # Capture the old commit hash.
        old_hash = subprocess.run(
            ["git", "-C", abs_submodule_path, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        # Pull latest from remote.
        result = subprocess.run(
            ["git", "submodule", "update", "--remote", "--merge", submodule_path],
            cwd=root, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {"error": f"Submodule update failed: {result.stderr[:300]}"}

        new_hash = subprocess.run(
            ["git", "-C", abs_submodule_path, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        if old_hash == new_hash:
            return {"status": "up_to_date", "name": safe_name, "commit": old_hash[:12]}

        # Re-scan for security concerns.
        # Read existing subfolder filter if present.
        filter_path = os.path.join(
            root, "plugins", "shared", f".{safe_name}_plugin_filter"
        )
        subfolder = None
        if os.path.isfile(filter_path):
            subfolder = open(filter_path).read().strip() or None

        security_warnings = scan_plugin_security(abs_submodule_path, subfolder)

        git_commit(root, f"Update shared plugin: {safe_name} ({old_hash[:8]}→{new_hash[:8]})")

    result = {
        "status": "updated",
        "name": safe_name,
        "old_commit": old_hash[:12],
        "new_commit": new_hash[:12],
        "security_warnings": security_warnings,
    }

    if security_warnings:
        result["requires_acknowledgement"] = True
        result["message"] = (
            f"Plugin '{safe_name}' updated but NOT re-activated — "
            f"{len(security_warnings)} security warning(s) found. "
            "Call acknowledge_warnings() to re-activate."
        )
    return result


def check_plugin_updates(user_id: str, name: str) -> dict:
    """Check if a shared plugin has upstream updates without pulling them.

    Does a ``git fetch`` on the submodule and compares the local HEAD to the
    remote tracking branch.  Returns commit hashes and whether an update is
    available.
    """
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    root = workspace_root(user_id)
    abs_submodule = os.path.join(root, "plugins", "shared", safe_name)

    if not os.path.isdir(abs_submodule):
        return {"error": f"Plugin '{safe_name}' not found."}

    # Local HEAD.
    local = subprocess.run(
        ["git", "-C", abs_submodule, "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()

    # Fetch from remote (fast, no merge).
    subprocess.run(
        ["git", "-C", abs_submodule, "fetch", "--quiet"],
        capture_output=True, text=True, timeout=30,
    )

    # Remote tracking branch HEAD (typically origin/main or origin/master).
    remote = ""
    for branch in ("origin/main", "origin/master"):
        r = subprocess.run(
            ["git", "-C", abs_submodule, "rev-parse", branch],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            remote = r.stdout.strip()
            break

    if not remote:
        # Fallback: FETCH_HEAD after the fetch.
        r = subprocess.run(
            ["git", "-C", abs_submodule, "rev-parse", "FETCH_HEAD"],
            capture_output=True, text=True,
        )
        remote = r.stdout.strip() if r.returncode == 0 else ""

    update_available = bool(remote) and local != remote

    # Count commits behind if there's a diff.
    commits_behind = 0
    if update_available:
        r = subprocess.run(
            ["git", "-C", abs_submodule, "rev-list", "--count", f"HEAD..{remote}"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            commits_behind = int(r.stdout.strip())

    return {
        "name": safe_name,
        "local_commit": local[:12],
        "remote_commit": remote[:12] if remote else None,
        "update_available": update_available,
        "commits_behind": commits_behind,
    }


def list_shared_plugins(user_id: str) -> list[dict]:
    """List imported shared plugin repos."""
    root = workspace_root(user_id)
    shared_dir = os.path.join(root, "plugins", "shared")
    if not os.path.isdir(shared_dir):
        return []
    results = []
    for entry in sorted(os.listdir(shared_dir)):
        entry_path = os.path.join(shared_dir, entry)
        if os.path.isdir(entry_path) and not entry.startswith("."):
            # Try to get the remote URL.
            url = ""
            try:
                r = subprocess.run(
                    ["git", "config", f"submodule.plugins/shared/{entry}.url"],
                    cwd=root, capture_output=True, text=True,
                )
                url = r.stdout.strip()
            except Exception:
                pass
            # Check for sibling subfolder filter.
            subfolder = None
            filter_path = os.path.join(shared_dir, f".{entry}_plugin_filter")
            if os.path.isfile(filter_path):
                with open(filter_path) as f:
                    subfolder = f.read().strip()
            # List plugin.py files found.
            plugins_found = []
            for dirpath, _dirs, files in os.walk(entry_path):
                if "plugin.py" in files:
                    rel = os.path.relpath(dirpath, entry_path)
                    plugins_found.append(rel if rel != "." else "(root)")
            results.append({
                "name": entry,
                "url": url,
                "subfolder_filter": subfolder,
                "plugins_found": plugins_found,
            })
    return results


def get_workspace_plugins_dir(user_id: str) -> str | None:
    """Return the path to a user's workspace plugins directory, if it exists.

    If the plugins directory contains git submodules (shared plugins), they
    are re-initialized on first access so that plugin files survive container
    restarts.
    """
    root = workspace_root(user_id)
    plugins_dir = os.path.join(root, "plugins")
    if not os.path.isdir(plugins_dir):
        return None

    # Ensure git submodules are checked out.  After a container restart the
    # workspace volume persists but submodule working trees may need init.
    shared_dir = os.path.join(plugins_dir, "shared")
    if os.path.isdir(shared_dir):
        try:
            subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=root, capture_output=True, text=True, timeout=60,
            )
        except Exception:
            logger.debug("git submodule update failed for %s", user_id, exc_info=True)

    return plugins_dir


# ---------------------------------------------------------------------------
# File sharing (publish / unpublish) — backed by share_registry
# ---------------------------------------------------------------------------


def publish_file(user_id: str, relative_path: str, *,
                 channel: str | None = None) -> dict:
    """Publish a workspace file so it can be accessed via a public URL.

    Only the specific file is shared — not the whole workspace.  Both the
    path token and the served filename are randomized UUIDs so the URL
    reveals nothing about the file's real name or contents.  The share
    is persisted in the per-user registry at ``{workspace}/.shares.json``,
    so it survives restarts and shows up in workspace_list_shares.

    Returns dict with 'url' and 'token', or 'error'.
    """
    from prax.services import share_registry

    root = workspace_root(user_id)
    abs_path = os.path.abspath(os.path.join(root, relative_path))
    # Safety: ensure path stays within workspace.
    if not abs_path.startswith(os.path.abspath(root) + os.sep):
        return {"error": "Path escapes workspace."}
    if not os.path.isfile(abs_path):
        return {"error": f"File not found: {relative_path}"}

    entry = share_registry.register_file(user_id, abs_path, channel=channel)
    url = share_registry.public_url_for(entry)
    if not url:
        # Registered, but no public URL available — caller decides how to
        # surface this (probably "saved, but ngrok isn't up — link will
        # work once it is").
        return {
            "token": entry["token"],
            "file": relative_path,
            "warning": "Share registered but NGROK_URL is not configured — link is not yet reachable.",
        }
    return {"url": url, "token": entry["token"], "file": relative_path}


def unpublish_file(user_id: str, token: str) -> dict:
    """Remove a previously published file share."""
    from prax.services import share_registry
    if share_registry.revoke(user_id, token):
        return {"status": "unpublished", "token": token}
    return {"error": f"Token not found: {token}"}


def get_published_file(token: str, filename: str | None = None) -> str | None:
    """Look up a published file by its share token (cross-user).

    The /shared/<token>/<filename> route has no user context, so this
    scans every workspace's registry.  If *filename* is provided, it
    must match the randomized public name minted when the file was
    published — protects against the (theoretical) token collision.

    Returns the absolute path to the real file, or None.
    """
    from prax.services import share_registry
    entry = share_registry.find_file_share_globally(token, public_name=filename)
    return entry.get("abs_path") if entry else None


# ---------------------------------------------------------------------------
# Conversation & agent trace log
# ---------------------------------------------------------------------------

_TRACE_FILENAME = "trace.log"
_TRACE_MAX_BYTES = 512 * 1024  # 0.5 MB — rotate when exceeded
_TRACE_KEEP_ROTATED = 3  # keep last 3 rotated files


def _rotate_trace(trace_path: str) -> None:
    """Rotate trace.log when it exceeds the size limit.

    Moves trace.log → archive/trace_logs/trace.<timestamp>.log (plain text
    for grep-ability) and prunes old rotated files beyond _TRACE_KEEP_ROTATED.
    """
    try:
        if not os.path.isfile(trace_path):
            return
        if os.path.getsize(trace_path) < _TRACE_MAX_BYTES:
            return

        root = os.path.dirname(trace_path)
        archive_dir = os.path.join(root, "archive", "trace_logs")
        os.makedirs(archive_dir, exist_ok=True)

        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        rotated = os.path.join(archive_dir, f"trace.{ts}.log")
        shutil.move(trace_path, rotated)
        # Create a fresh trace file with a pointer to archives.
        with open(trace_path, "w", encoding="utf-8") as f:
            f.write(f"=== Log rotated at {ts} — previous entries in archive/trace_logs/ ===\n")
        git_commit(root, f"Rotate trace log ({ts})")

        # Prune old rotated files.
        rotated_files = sorted(
            [f for f in os.listdir(archive_dir) if f.endswith(".log")],
            reverse=True,
        )
        for old in rotated_files[_TRACE_KEEP_ROTATED:]:
            os.remove(os.path.join(archive_dir, old))
    except OSError:
        logger.debug("Trace rotation failed for %s", trace_path, exc_info=True)


def append_trace(user_id: str, entries: list[dict]) -> None:
    """Append structured trace entries to the user's workspace trace log.

    Each entry is a dict with at least ``type`` and ``content`` keys.
    See :class:`prax.trace_events.TraceEvent` for the canonical list of
    supported types.

    The trace file is append-only, committed to git, and searchable via
    conversation_search / conversation_history tools.  Rotated to plain-text
    archive when it exceeds 0.5 MB.
    """
    if not entries:
        return
    root = workspace_root(user_id)
    if not os.path.isdir(root):
        return  # workspace not initialised yet
    trace_path = os.path.join(root, _TRACE_FILENAME)

    _rotate_trace(trace_path)

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = [f"\n=== {ts} ===\n"]
    for entry in entries:
        tag = entry.get("type", "unknown").upper()
        content = entry.get("content", "")
        # Truncate very long content to keep the file manageable.
        if len(content) > 5000:
            content = content[:5000] + "\n... [truncated]"
        lines.append(f"[{tag}] {content}\n")
    try:
        with open(trace_path, "a", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError:
        logger.debug("Failed to write trace log for %s", user_id, exc_info=True)


def read_trace_tail(user_id: str, lines: int = 200) -> str:
    """Return the last *lines* lines of the user's trace log."""
    root = workspace_root(user_id)
    trace_path = os.path.join(root, _TRACE_FILENAME)
    if not os.path.isfile(trace_path):
        return ""
    with open(trace_path, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])


def _search_trace_file(path: str, query_lower: str, results: list[dict],
                       max_results: int,
                       type_filter: str | None = None) -> None:
    """Search a single trace file for blocks matching *query_lower*.

    If *type_filter* is given (e.g. ``"audit"``, ``"tool_call"``), only blocks
    that contain at least one line with the corresponding ``[TAG]`` prefix are
    returned, and the excerpt only includes lines matching that tag.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    tag_prefix: str | None = None
    if type_filter is not None:
        tag_prefix = f"[{type_filter.upper()}]"

    import re as _re
    blocks = _re.split(r"\n(?==== \d{4}-)", content)
    for block in reversed(blocks):
        if len(results) >= max_results:
            return
        if query_lower not in block.lower():
            continue
        ts_match = _re.match(r"=== (\S+) ===", block.strip())
        ts = ts_match.group(1) if ts_match else "unknown"
        excerpt_lines = []
        if tag_prefix is not None:
            # Only include lines that match both the type tag and the query.
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith(tag_prefix) and query_lower in line.lower():
                    excerpt_lines.append(stripped)
            # Skip block entirely if no lines match the type filter.
            if not excerpt_lines:
                continue
        else:
            for line in block.splitlines():
                if query_lower in line.lower():
                    excerpt_lines.append(line.strip())
        excerpt = "\n".join(excerpt_lines[:5])
        if len(excerpt) > 500:
            excerpt = excerpt[:500] + "..."
        results.append({"timestamp": ts, "excerpt": excerpt})


def search_trace(user_id: str, query: str, max_results: int = 20,
                 type_filter: str | None = None) -> list[dict]:
    """Search the trace log for blocks containing *query*.

    Searches both the current trace.log and any rotated plain-text
    archives.  Returns a list of dicts with ``timestamp`` and ``excerpt``
    keys, most recent first.

    If *type_filter* is given (e.g. ``"audit"``, ``"tool_call"``), only
    blocks containing at least one line with the corresponding ``[TAG]``
    prefix are returned, and excerpts only include matching-type lines.
    """
    root = workspace_root(user_id)
    query_lower = query.lower()
    results: list[dict] = []

    # Search current trace first (most recent).
    trace_path = os.path.join(root, _TRACE_FILENAME)
    _search_trace_file(trace_path, query_lower, results, max_results,
                       type_filter=type_filter)

    # Then search archived files newest-first.
    archive_dir = os.path.join(root, "archive", "trace_logs")
    if os.path.isdir(archive_dir):
        for fname in sorted(os.listdir(archive_dir), reverse=True):
            if len(results) >= max_results:
                break
            if fname.endswith(".log"):
                _search_trace_file(
                    os.path.join(archive_dir, fname),
                    query_lower, results, max_results,
                    type_filter=type_filter,
                )

    return results


# ---------------------------------------------------------------------------
# Deprecated aliases — old underscore-prefixed names kept for backward compat.
# New code should import the public names above.
# ---------------------------------------------------------------------------
_workspace_root = workspace_root
_safe_join = safe_join
_ensure_workspace = ensure_workspace
_get_lock = get_lock
_git_commit = git_commit
