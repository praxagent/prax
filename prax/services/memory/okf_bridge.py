"""Open Knowledge Format (OKF) bridge — read/write knowledge as plain files.

OKF (Google Cloud, 2026) represents knowledge as a directory of markdown files with YAML
frontmatter, one file per "concept", cross-linked with markdown links, plus optional
``index.md`` (progressive disclosure) and ``log.md`` (history).  See
``docs/research/open-knowledge-format.md``.

This module is the **format layer only** — pure filesystem in/out, no Neo4j.  It turns
concept/relation records into an OKF bundle and parses a bundle back into records.  The graph
side (pulling concepts from Neo4j on export, writing them back on import) lives in
``knowledge_graph`` so this stays trivially testable with a temp directory.

OKF is used purely as an *interchange* format: Prax keeps its richer internal vector-hybrid
graph and exposes OKF only at the boundary for portability and interop.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Files that are bundle scaffolding, not concepts.
_RESERVED = {"index.md", "log.md"}
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
# Markdown links to sibling concept files: [text](target.md) or (./target.md)
_LINK_RE = re.compile(r"\[[^\]]*\]\(\.?/?([^)]+?\.md)\)")


def slugify(name: str) -> str:
    """Filesystem-safe slug for a concept name."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "concept"


def _assign_slugs(concepts: list[dict]) -> dict[str, str]:
    """Map concept id → unique ``.md``-less slug."""
    slugs: dict[str, str] = {}
    used: set[str] = set()
    for c in concepts:
        base = slugify(c.get("display_name") or c.get("name") or c.get("id", ""))
        slug = base
        n = 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        slugs[c["id"]] = slug
    return slugs


def write_bundle(concepts: list[dict], relations: list[dict], dest_dir: str, namespace: str) -> dict:
    """Write an OKF bundle to *dest_dir*.

    ``concepts``: dicts with id, name, display_name, description, importance, source,
    source_type, created_at, updated_at.  ``relations``: dicts with from_id, to_id, type.
    Returns a summary ``{namespace, concepts, relations, path}``.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    slugs = _assign_slugs(concepts)

    # Outgoing relations per concept id.
    out: dict[str, list[tuple[str, str]]] = {}
    rel_count = 0
    for r in relations:
        fid, tid = r.get("from_id"), r.get("to_id")
        if fid in slugs and tid in slugs:
            out.setdefault(fid, []).append((r.get("type") or "related_to", tid))
            rel_count += 1

    for c in concepts:
        cid = c["id"]
        slug = slugs[cid]
        fm = {
            "type": c.get("source_type") or "concept",
            "title": c.get("display_name") or c.get("name") or slug,
        }
        if c.get("description"):
            fm["description"] = c["description"]
        if c.get("source"):
            fm["resource"] = c["source"]
        ts = c.get("updated_at") or c.get("created_at")
        if ts:
            fm["timestamp"] = ts
        if c.get("tags"):
            fm["tags"] = c["tags"]

        body_parts = [f"# {fm['title']}\n", (c.get("description") or "").strip()]
        edges = out.get(cid, [])
        if edges:
            rel_lines = ["\n# Relations\n"]
            for rtype, tid in edges:
                tslug = slugs[tid]
                ttitle = next(
                    (x.get("display_name") or x.get("name") or tslug for x in concepts if x["id"] == tid),
                    tslug,
                )
                rel_lines.append(f"- {rtype}: [{ttitle}](./{tslug}.md)")
            body_parts.append("\n".join(rel_lines))
        body = "\n".join(p for p in body_parts if p).rstrip() + "\n"

        text = f"---\n{yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)}---\n\n{body}"
        (dest / f"{slug}.md").write_text(text, encoding="utf-8")

    _write_index(dest, namespace, concepts, slugs)
    _write_log(dest, namespace, concepts)
    return {"namespace": namespace, "concepts": len(concepts), "relations": rel_count, "path": str(dest)}


def _write_index(dest: Path, namespace: str, concepts: list[dict], slugs: dict[str, str]) -> None:
    lines = [f"---\ntype: index\ntitle: {namespace}\n---\n", f"# {namespace}\n"]
    for c in sorted(concepts, key=lambda x: (x.get("display_name") or x.get("name") or "").lower()):
        title = c.get("display_name") or c.get("name") or slugs[c["id"]]
        desc = (c.get("description") or "").strip().replace("\n", " ")
        preview = f" — {desc[:120]}" if desc else ""
        lines.append(f"- [{title}](./{slugs[c['id']]}.md){preview}")
    (dest / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_log(dest: Path, namespace: str, concepts: list[dict]) -> None:
    rows = []
    for c in concepts:
        ts = c.get("created_at") or c.get("updated_at") or ""
        title = c.get("display_name") or c.get("name") or c.get("id", "")
        rows.append((ts, title))
    rows.sort()
    lines = [f"# {namespace} — history\n"]
    lines += [f"- {ts or '(undated)'}: {title}" for ts, title in rows]
    (dest / "log.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter dict, body).  Tolerant of no frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        fm = {}
    return (fm if isinstance(fm, dict) else {}), m.group(2)


def read_bundle(src_dir: str) -> tuple[list[dict], list[tuple[str, str, str]]]:
    """Parse an OKF bundle directory into (concept records, relation edges).

    Each record: ``{name, description, source, source_type, tags, timestamp, slug}``.
    Each edge: ``(source_name, relation_type, target_name)`` resolved via link targets.
    Unparseable/reserved files are skipped.
    """
    src = Path(src_dir)
    records: list[dict] = []
    slug_to_name: dict[str, str] = {}
    raw: list[tuple[str, str]] = []  # (slug, body) for link extraction

    for path in sorted(src.rglob("*.md")):
        if path.name in _RESERVED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, body = parse_frontmatter(text)
        slug = path.stem
        name = (fm.get("title") or slug).strip()
        slug_to_name[slug] = name
        records.append({
            "name": name,
            "description": (fm.get("description") or "").strip(),
            "source": fm.get("resource") or "",
            "source_type": fm.get("type") or "concept",
            "tags": fm.get("tags") or [],
            "timestamp": fm.get("timestamp") or "",
            "slug": slug,
        })
        raw.append((slug, body))

    edges: list[tuple[str, str, str]] = []
    for slug, body in raw:
        src_name = slug_to_name.get(slug)
        if not src_name:
            continue
        for target_file in _LINK_RE.findall(body):
            tslug = Path(target_file).stem
            tname = slug_to_name.get(tslug)
            if not tname or tname == src_name:
                continue
            rtype = _relation_type_near_link(body, target_file)
            edges.append((src_name, rtype, tname))
    return records, edges


def _relation_type_near_link(body: str, target_file: str) -> str:
    """Best-effort relation type from a 'Relations' bullet like '- defines: [X](x.md)'."""
    for line in body.splitlines():
        if target_file in line:
            m = re.match(r"\s*-\s*([a-zA-Z_][\w ]*?):\s*\[", line)
            if m:
                return m.group(1).strip().replace(" ", "_")
    return "related_to"
