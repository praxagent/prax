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

# Session ids kept on the archive so summarised work stays addressable
# (see _preserve_refs). Bounded so the trailer can't outgrow the file cap.
MAX_ARCHIVE_REFS = 40
_REFS_PREFIX = "Sessions: "
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
    """Read per-session detail files for a date, or one session by its id.

    Progressive disclosure: details are not auto-loaded into context; the agent
    asks for them when it needs them. Accepts either:

    * ``YYYY-MM-DD`` — every session from that day, concatenated; or
    * ``YYYY-MM-DD-{short_id}`` — the single session that reference names.

    The second form is the point: entry bullets and the compacted archive both
    carry ``{date}-{short_id}`` refs, so an abstraction in context always
    dereferences to exactly the evidence it came from rather than to a whole
    day's worth (docs/research/tencentdb-agent-memory.md).
    """
    if re.match(r"^\d{4}-\d{2}-\d{2}-\S+$", date):
        details_dir = _detail_dir(user_id, slug)
        target = details_dir / f"{date}.md"
        if not _space_exists(user_id, slug):
            return f"Space '{slug}' does not exist."
        if not target.is_file():
            return f"No session detail {date} for {slug}."
        try:
            return f"### {target.name}\n\n{target.read_text(encoding='utf-8')}"
        except Exception as e:
            logger.warning("Failed to read detail file %s: %s", target, e)
            return f"Could not read session detail {date}."
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return "Date must be YYYY-MM-DD (or YYYY-MM-DD-{session_id})."
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


def search_session_details(
    user_id: str,
    slug: str,
    query: str,
    limit: int = 12,
    context_chars: int = 240,
) -> str:
    """Keyword-search the per-session detail files of one space.

    The detail files are the COMPLETE record; the compacted progress summary is
    the lossy abstraction over them. Until now they were addressable only by
    date (``progress_detail(slug, "2026-08-01")``), which means the record was
    only reachable by someone who already knew when the thing happened — the
    one situation in which you do not need to search.

    This is the "keep the complete log and grep it with code" half of
    ``docs/research/prolong-programmatic-memory.md``: compact the CONTEXT, never
    the RECORD. Deterministic substring matching, no model call and no
    embedding, so it works in a lite deployment and cannot hallucinate a hit.

    Every result carries its ``{date}-{short_id}`` session ref, so a hit
    dereferences to exactly the evidence it came from — the invariant that
    ``_preserve_refs`` exists to protect through compaction
    (``docs/research/tencentdb-agent-memory.md``). A search that returned
    matching prose without its ref would reintroduce the defect from the other
    end.
    """
    if not _space_exists(user_id, slug):
        return f"Space '{slug}' does not exist."
    terms = [t for t in (query or "").lower().split() if t]
    if not terms:
        return "Give at least one search term."
    details_dir = _detail_dir(user_id, slug)
    if not details_dir.is_dir():
        return f"No session details recorded for {slug} yet."

    hits: list[tuple[str, str, str]] = []  # (ref, line, context)
    # Newest first: recent sessions are far likelier to be what is wanted, and
    # the cap below would otherwise be spent on the oldest.
    for path in sorted(details_dir.glob("*.md"), reverse=True):
        ref = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to read detail file %s: %s", path, e)
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            low = line.lower()
            if all(t in low for t in terms):
                snippet = line[:context_chars]
                if len(line) > context_chars:
                    snippet += "…"
                hits.append((ref, snippet, path.name))
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break

    if not hits:
        return (f"No session detail in {slug} matches {query!r}. "
                f"The record is complete, so this is evidence of absence rather "
                f"than a retrieval failure.")
    lines = [f"{len(hits)} match(es) for {query!r} in {slug}:"]
    for ref, snippet, _fname in hits:
        lines.append(f"- [{ref}] {snippet}")
    lines.append("")
    lines.append(f"Read any of these in full with progress_detail('{slug}', '<ref>').")
    if len(hits) >= limit:
        lines.append(f"(capped at {limit} matches — narrow the query for more)")
    return "\n".join(lines)


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
        archive=_preserve_refs(new_archive.strip(), to_fold),
        recent=kept,
        open_threads=sections.open_threads,
    )


def _preserve_refs(archive: str, folded: list[str]) -> str:
    """Re-attach the session ids the summariser dropped.

    # INVARIANT: every folded entry's ``{date}-{short_id}`` ref survives
    # compaction, so any abstraction left in context still dereferences to the
    # detail file it came from. Re-attached by CODE, never trusted to the
    # model's output.

    Compaction is a lossy LLM rewrite, and the thing it loses that matters most
    is not prose — it is the **pointer**. Each recent bullet is
    ``{date} · {outcome} · {short_id}`` and names a detail file
    ``.progress/{date}-{short_id}.md`` that compaction deliberately never
    touches. Without the id in the archive, those files survive on disk and
    become unreachable at exactly the moment the summary is the only thing left
    in context.

    So the ids are appended verbatim, outside the summarised prose: an
    abstraction must keep a deterministic path back to its evidence. Ids
    already present in the archive are not duplicated, and the trailer is
    bounded so the archive cannot grow without limit — oldest refs are dropped
    first (their detail files may well have been pruned anyway) and the
    truncation is marked rather than silent.

    Rationale: docs/research/tencentdb-agent-memory.md.
    """
    refs = [r for r in (_entry_ref(e) for e in folded) if r]
    if not refs:
        return archive

    body, existing = _split_refs(archive)
    merged = existing + [r for r in refs if r not in existing]
    dropped = 0
    if len(merged) > MAX_ARCHIVE_REFS:
        dropped = len(merged) - MAX_ARCHIVE_REFS
        merged = merged[-MAX_ARCHIVE_REFS:]
    trailer = f"{_REFS_PREFIX}{', '.join(merged)}"
    if dropped:
        trailer += f" (+{dropped} older)"
    return f"{body}\n\n{trailer}".strip() if body else trailer


def _entry_ref(entry: str) -> str | None:
    """``2026-08-07 · did a thing · a1b2c3`` → ``2026-08-07-a1b2c3``.

    Returns None for anything that isn't a well-formed bullet, so a malformed
    line degrades to "no pointer" rather than a bogus one.
    """
    parts = [p.strip() for p in entry.split("·")]
    if len(parts) < 3:
        return None
    date, short_id = parts[0], parts[-1]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) or not short_id:
        return None
    return f"{date}-{short_id}"


def _split_refs(archive: str) -> tuple[str, list[str]]:
    """Split an archive into (prose, existing refs)."""
    idx = archive.rfind(_REFS_PREFIX)
    if idx == -1:
        return archive, []
    body = archive[:idx].strip()
    tail = archive[idx + len(_REFS_PREFIX):]
    tail = re.sub(r"\s*\(\+\d+ older\)\s*$", "", tail).strip()
    return body, [r.strip() for r in tail.split(",") if r.strip()]


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
