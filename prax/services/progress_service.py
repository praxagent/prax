"""Per-space session progress — bounded rolling log.

Solves the "declare victory too early" and "what did we do last time?"
failure modes documented in Anthropic's long-running harness post, but
keeps context pollution impossible by construction:

1. Scope is per Library space, not global.
2. The public file (`.progress.md`) is hard-capped at ~6000 chars
   (~1500 tokens); exceeding the cap triggers compaction before the
   write lands.
3. Three sections: `## Archive` (single paragraph summary of older
   work), `## Recent sessions` (<=10 bullets), `## Open threads`.
4. Per-session detail lives in `.progress/YYYY-MM-DD-{id}.md` and is
   never auto-loaded — `read_session_detail()` fetches on demand.

When `Recent sessions` exceeds the cap or the file exceeds MAX_CHARS,
the 5 oldest bullets are folded into `Archive` via a low-tier LLM
summarisation call.  The full-detail files are never re-read during
compaction — the summary loop only re-summarises text.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from prax.services.library_service import _space_path, ensure_library

logger = logging.getLogger(__name__)

MAX_FILE_CHARS = 6000
MAX_RECENT_ENTRIES = 10
COMPACT_KEEP_RECENT = 5
PROGRESS_FILE = ".progress.md"
DETAIL_DIR = ".progress"

_lock = threading.Lock()


@dataclass
class ProgressSections:
    archive: str
    recent: list[str]
    open_threads: list[str]


def _progress_path(user_id: str, slug: str) -> Path:
    return _space_path(user_id, slug) / PROGRESS_FILE


def _detail_dir(user_id: str, slug: str) -> Path:
    return _space_path(user_id, slug) / DETAIL_DIR


def _parse(content: str) -> ProgressSections:
    archive = ""
    recent: list[str] = []
    open_threads: list[str] = []
    current: str | None = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip().lower()
            continue
        if current == "archive":
            if stripped:
                archive = (archive + "\n" + stripped).strip() if archive else stripped
        elif current == "recent sessions":
            if stripped.startswith("- "):
                recent.append(stripped[2:])
        elif current == "open threads":
            if stripped.startswith("- "):
                open_threads.append(stripped[2:])
    return ProgressSections(archive=archive, recent=recent, open_threads=open_threads)


def _render(slug: str, sections: ProgressSections) -> str:
    lines = [f"# Progress: {slug}", ""]
    lines.append("## Archive")
    lines.append("")
    lines.append(sections.archive if sections.archive else "_(empty — compaction has not run yet)_")
    lines.append("")
    lines.append("## Recent sessions")
    lines.append("")
    if sections.recent:
        lines.extend(f"- {entry}" for entry in sections.recent)
    else:
        lines.append("_(no sessions recorded yet)_")
    lines.append("")
    lines.append("## Open threads")
    lines.append("")
    if sections.open_threads:
        lines.extend(f"- {entry}" for entry in sections.open_threads)
    else:
        lines.append("_(none)_")
    lines.append("")
    return "\n".join(lines)


def _space_exists(user_id: str, slug: str) -> bool:
    return _space_path(user_id, slug).is_dir()


def read_progress(user_id: str, slug: str) -> str:
    """Return the rendered progress file for a space.

    Returns a short placeholder if the space has no progress file yet.
    Never returns unbounded content — the file is capped by construction.
    """
    path = _progress_path(user_id, slug)
    if not path.is_file():
        if not _space_exists(user_id, slug):
            return f"Space '{slug}' does not exist."
        return (
            f"# Progress: {slug}\n\n_No progress recorded yet for this space. "
            f"Use progress_append to log session outcomes._"
        )
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to read progress for %s/%s: %s", user_id, slug, e)
        return f"Failed to read progress for {slug}: {e}"


def append_progress(
    user_id: str,
    slug: str,
    outcome: str,
    open_threads: list[str] | None = None,
    detail: str | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
    compactor=None,
) -> str:
    """Append one session entry to a space's progress file.

    Call at most once per turn — this is the end-of-session log, not a
    running commentary. `outcome` should be a one-line summary; put
    detail (if any) into the `detail` arg, which is written to a
    per-session detail file.

    `open_threads` overwrites the Open threads section (pass an empty
    list to clear it, or omit to leave it unchanged).

    Triggers compaction if recent entries exceed MAX_RECENT_ENTRIES or
    the file would exceed MAX_FILE_CHARS.  Compaction uses the given
    `compactor` callable (defaults to a LOW-tier LLM summariser) — in
    tests, pass a deterministic stub.
    """
    if not _space_exists(user_id, slug):
        return f"Space '{slug}' does not exist — create it first via library_new_space."
    ensure_library(user_id)
    now = now or datetime.now(UTC)
    date_str = now.strftime("%Y-%m-%d")
    short_id = (session_id or now.strftime("%H%M%S"))[:8]

    outcome_clean = _sanitize_outcome(outcome)
    entry = f"{date_str} · {outcome_clean} · {short_id}"

    with _lock:
        path = _progress_path(user_id, slug)
        if path.is_file():
            sections = _parse(path.read_text(encoding="utf-8"))
        else:
            sections = ProgressSections(archive="", recent=[], open_threads=[])

        sections.recent.append(entry)
        if open_threads is not None:
            sections.open_threads = [t.strip() for t in open_threads if t and t.strip()]

        rendered = _render(slug, sections)
        if (
            len(sections.recent) > MAX_RECENT_ENTRIES
            or len(rendered) > MAX_FILE_CHARS
        ):
            sections = _compact(sections, compactor=compactor)
            rendered = _render(slug, sections)

        path.write_text(rendered, encoding="utf-8")

        if detail:
            _write_detail(user_id, slug, date_str, short_id, detail, outcome_clean)

    return f"Appended progress entry to {slug}: {outcome_clean}"


def read_session_detail(user_id: str, slug: str, date: str) -> str:
    """Read per-session detail files for a date (YYYY-MM-DD).

    Progressive disclosure: details are not auto-loaded into context;
    the agent asks for them by date when it needs them.  Returns
    concatenated content from every detail file matching the date.
    """
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return "Date must be YYYY-MM-DD."
    if not _space_exists(user_id, slug):
        return f"Space '{slug}' does not exist."
    details_dir = _detail_dir(user_id, slug)
    if not details_dir.is_dir():
        return f"No session details for {slug} on {date}."
    matches = sorted(details_dir.glob(f"{date}-*.md"))
    if not matches:
        return f"No session details for {slug} on {date}."
    parts = []
    for m in matches:
        try:
            parts.append(f"### {m.name}\n\n{m.read_text(encoding='utf-8')}")
        except Exception as e:
            logger.warning("Failed to read detail file %s: %s", m, e)
    return "\n\n---\n\n".join(parts)


def _write_detail(
    user_id: str,
    slug: str,
    date: str,
    short_id: str,
    detail: str,
    outcome: str,
) -> None:
    details_dir = _detail_dir(user_id, slug)
    details_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{date}-{short_id}.md"
    body = f"# {date} · {outcome}\n\n{detail}\n"
    (details_dir / fname).write_text(body, encoding="utf-8")


def _sanitize_outcome(outcome: str) -> str:
    single_line = " ".join(outcome.split())
    if len(single_line) > 200:
        single_line = single_line[:197] + "..."
    return single_line


def _compact(
    sections: ProgressSections,
    compactor=None,
) -> ProgressSections:
    """Fold the oldest recent entries into the archive paragraph."""
    if len(sections.recent) <= COMPACT_KEEP_RECENT:
        return sections
    to_fold = sections.recent[:-COMPACT_KEEP_RECENT]
    kept = sections.recent[-COMPACT_KEEP_RECENT:]
    compactor = compactor or _default_compactor
    try:
        new_archive = compactor(sections.archive, to_fold)
    except Exception as e:
        logger.warning("Compactor failed, falling back to truncated archive: %s", e)
        new_archive = _fallback_archive(sections.archive, to_fold)
    return ProgressSections(
        archive=new_archive.strip(),
        recent=kept,
        open_threads=sections.open_threads,
    )


def _fallback_archive(current_archive: str, folded: list[str]) -> str:
    joined = " ".join(folded)
    if current_archive:
        combined = f"{current_archive} {joined}"
    else:
        combined = joined
    if len(combined) > 1200:
        combined = combined[:1197] + "..."
    return combined


def _default_compactor(current_archive: str, folded: list[str]) -> str:
    """LOW-tier LLM compaction.

    Summarises the folded entries plus the current archive into a
    single short paragraph.  Falls back to concatenation on any LLM
    error so writes never fail just because the summariser is offline.
    """
    try:
        from prax.agent.llm_factory import build_llm
        llm = build_llm(tier="low", temperature=0.2)
        prompt = (
            "Rewrite the following as one short paragraph (<=400 chars) "
            "capturing the key outcomes and open questions. No bullet "
            "points. No preamble. Just the paragraph.\n\n"
        )
        if current_archive:
            prompt += f"Current archive:\n{current_archive}\n\n"
        prompt += "New entries to fold in:\n" + "\n".join(f"- {e}" for e in folded)
        result = llm.invoke(prompt)
        text = getattr(result, "content", None) or str(result)
        text = str(text).strip()
        if not text:
            return _fallback_archive(current_archive, folded)
        if len(text) > 1200:
            text = text[:1197] + "..."
        return text
    except Exception as e:
        logger.info("LLM compactor unavailable, using fallback: %s", e)
        return _fallback_archive(current_archive, folded)
