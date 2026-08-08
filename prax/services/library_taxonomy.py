"""Deterministic checks on the SHAPE of a filesystem memory store.

Implements the subset of the taxonomy contract from
[filesystem-based agent memory](../../docs/research/filesystem-agent-memory.md)
(arXiv 2607.26637) that is computable **without a model call**, from names and
one-line descriptions alone.

Their five principles:

- **P1 sibling distinction** — siblings are distinguishable by label alone.
- **P2 sibling relatedness** — siblings belong together.
- **P3 parent-child coverage** — a parent covers its children.
- **P4 tree-wide proximity** — distance mirrors relatedness.
- **P5 structural economy** — depth is added only where it improves routing to
  a fact; a level that does not help routing is overhead.

**Only P1 and P5 are implemented here, and that is deliberate.** P2, P3 and P4
are claims about *meaning* — whether two labels are related, whether a parent's
name covers a child's content. Approximating them with token overlap would
produce a number that looks like a measurement and is a guess, and a store
would then be "improved" to satisfy the proxy. Under-reporting is honest;
reporting badly is not. If those three are ever wanted, they need a judge, and
a judge belongs behind the existing LLM layer of the health check where its
cost and unreliability are already accounted for.

Why this exists at all: the paper's own finding is that organisation buys
**search cost**, not answer quality, and that taxonomy adherence **erodes as a
store grows for every agent but the strongest**. So this is a drift detector
for a known-degrading property, not a quality metric. Nothing here should be
read as "a tidier library gives better answers" — the paper says the opposite.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Words that carry no routing information, so two siblings differing only by
# these are not actually distinguishable by label.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with",
    "notes", "note", "misc", "other", "stuff", "general", "new", "old",
    "temp", "tmp", "draft", "drafts", "untitled", "copy", "final", "v", "v1",
    "v2", "v3", "part", "section",
})

_SEP = re.compile(r"[^a-z0-9]+")


def normalise_label(label: str) -> str:
    """Fold a label to its routing content: lowercase, unaccented, stopword-free.

    ``"Q2 Forecast (final copy)"`` and ``"q2-forecast-FINAL"`` normalise to the
    same thing, which is the point — a reader choosing between them from a
    directory listing cannot tell them apart either.
    """
    folded = unicodedata.normalize("NFKD", label or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    tokens = [t for t in _SEP.split(folded.lower()) if t]
    kept = [t for t in tokens if t not in _STOPWORDS and not t.isdigit()]
    return " ".join(kept or tokens)


@dataclass
class TaxonomyFinding:
    principle: str          # "P1" | "P5"
    path: str               # where in the tree
    detail: str             # human-readable, specific
    items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"principle": self.principle, "path": self.path,
                "detail": self.detail, "items": list(self.items)}


def check_sibling_distinction(
    parent_path: str,
    siblings: list[tuple[str, str]],
) -> list[TaxonomyFinding]:
    """P1 — siblings must be tellable apart from label (plus description).

    *siblings* is ``[(name, one_line_description), ...]``. Two siblings whose
    normalised labels collide are reported ONLY if their descriptions also
    collide: the contract is "by name, at worst name plus description", so a
    distinct description rescues a duplicate-looking name.
    """
    by_key: dict[str, list[tuple[str, str]]] = {}
    for name, desc in siblings:
        by_key.setdefault(normalise_label(name), []).append((name, desc))

    findings: list[TaxonomyFinding] = []
    for key, group in sorted(by_key.items()):
        if len(group) < 2 or not key:
            continue
        descs = {normalise_label(d) for _n, d in group}
        # Distinct, non-empty descriptions for every member => distinguishable.
        if len(descs) == len(group) and all(descs):
            continue
        findings.append(TaxonomyFinding(
            principle="P1",
            path=parent_path,
            detail=(f"{len(group)} siblings are not distinguishable by label: "
                    f"they normalise to {key!r} and their descriptions do not "
                    f"separate them"),
            items=sorted(n for n, _d in group),
        ))
    return findings


def check_structural_economy(
    path: str,
    child_count: int,
    grandchild_counts: list[int],
) -> list[TaxonomyFinding]:
    """P5 — a level that does not narrow the search is overhead.

    Two deterministic shapes:

    * a **chain node**: exactly one child, so descending narrows nothing;
    * an **empty level**: no children at all, which is pure signage.

    Deliberately does not flag wide nodes. "Too many siblings" is a judgement
    about relatedness (P2), not economy, and guessing at it here would push
    stores toward arbitrary sharding — the paper observes that agents already
    consolidate rather than shard when given more material.
    """
    findings: list[TaxonomyFinding] = []
    if child_count == 0:
        findings.append(TaxonomyFinding(
            principle="P5", path=path,
            detail="level contains nothing — pure signage, adds depth without routing"))
    elif child_count == 1:
        findings.append(TaxonomyFinding(
            principle="P5", path=path,
            detail=("level has a single child, so descending narrows nothing; "
                    "collapse it into its parent unless it is about to grow")))
    return findings


def taxonomy_report(tree: dict) -> dict:
    """Run every implemented check over a nested store description.

    *tree* is ``{"path": str, "name": str, "description": str,
    "kind": "folder"|"note", "children": [tree, ...]}``. Returns findings plus
    the counts needed to see drift over time — the paper's point is that
    adherence *erodes as the store grows*, so a single snapshot is much less
    useful than a series.

    ``kind`` is load-bearing: P5 asks whether a LEVEL earns its depth, and a
    note with no children is content, not an empty level. Without the
    distinction every leaf note is reported as "pure signage" — which is what
    the first version of this function did. Nodes with children are treated as
    folders when ``kind`` is absent, so a caller that omits it degrades to
    structure-only rather than to nonsense.
    """
    findings: list[TaxonomyFinding] = []
    nodes = 0
    max_depth = 0

    def walk(node: dict, depth: int) -> None:
        nonlocal nodes, max_depth
        nodes += 1
        max_depth = max(max_depth, depth)
        children = node.get("children") or []
        path = node.get("path") or node.get("name") or ""
        kind = node.get("kind") or ("folder" if children else "note")

        if children:
            findings.extend(check_sibling_distinction(
                path, [(c.get("name", ""), c.get("description", "")) for c in children]))
        # P5 judges LEVELS. A note is content and can never be overhead, however
        # deep it sits; only a folder can fail to earn its depth.
        if kind == "folder":
            findings.extend(check_structural_economy(
                path, len(children), [len(c.get("children") or []) for c in children]))
        for child in children:
            walk(child, depth + 1)

    walk(tree, 0)
    by_principle: dict[str, int] = {}
    for f in findings:
        by_principle[f.principle] = by_principle.get(f.principle, 0) + 1
    return {
        "findings": [f.as_dict() for f in findings],
        "counts": by_principle,
        "nodes": nodes,
        "max_depth": max_depth,
        # Named explicitly so a reader never mistakes silence for a pass.
        "not_checked": {
            "P2": "sibling relatedness — requires meaning, not computable here",
            "P3": "parent-child coverage — requires meaning",
            "P4": "tree-wide proximity — requires meaning",
        },
    }
