"""Symbolic consistency checking for graph-memory writes.

The extraction LLM is asked (in the consolidation prompt) to notice when a new
relation contradicts an existing one and volunteer a ``supersedes`` marker. The
bi-temporal machinery to *record* supersession exists in ``graph_store``, but it
fires only when the model volunteers — the graph itself is never consulted. A
missed contradiction therefore leaves both edges current forever.

This module puts the detection on the symbolic side, where it is cheap and
reliable: for a relation type declared **single-valued** (a source can have at
most one current target), "does a current edge with a different target already
exist?" is one graph query at write time. Detection is symbolic; what to do
about it stays configurable:

``MEMORY_CONSISTENCY_MODE`` selects one of three behaviours:

* ``off`` (default) — prior behaviour: supersession happens only when the
  extractor LLM volunteers it.
* ``log`` — conflicts are counted on the ``ConsolidationResult`` and logged,
  nothing is changed. This mode exists because the single-valued declaration is
  itself authored: a wrong entry in ``SINGLE_VALUED_TYPES`` would turn
  supersession into a fact-deleter, so the allowlist must earn trust from logs
  before it is allowed to act.
* ``enforce`` — the older edge is marked ``valid_until = now`` before the new
  one lands: the same operation the LLM-volunteered path performs, minus the
  reliance on the LLM noticing.

Deliberately NOT here: any notion of semantic conflict between targets of a
multi-valued relation ("prefers dark_mode" vs "prefers light_mode" — both
``prefers`` edges, only contradictory because themes are exclusive). That needs
world knowledge the graph does not hold; it stays with the LLM's ``supersedes``
path. This module only enforces what the schema can actually know.

Rationale and provenance: ``docs/research/neurosymbolic-lens.md``.
"""
from __future__ import annotations

import logging

from prax.settings import settings

logger = logging.getLogger(__name__)

# Relation types where one CURRENT target per source is structurally implied.
# Conservative by design: none of the extraction prompt's default vocabulary
# (works_on, interested_in, prefers, related_to, part_of, caused_by,
# mentioned_with) qualifies — a person legitimately works_on many things and
# prefers many unrelated things. Only add a type here if two simultaneous
# current targets are a contradiction BY DEFINITION, not merely unusual.
SINGLE_VALUED_TYPES: frozenset[str] = frozenset({
    "lives_in",
    "based_in",
    "works_at",
    "employed_by",
    "married_to",
    "reports_to",
    "named",       # canonical name / current handle of a thing
    "default_for", # the single current default (model, branch, environment)
})

# One line appended to the extraction prompt when the flag is on, so the
# extractor knows these types exist and carry single-valued semantics.
PROMPT_ADDENDUM = (
    "- Additional relation types you may use when they fit: "
    + ", ".join(sorted(SINGLE_VALUED_TYPES))
    + ". These are single-valued: a source has at most one current target, "
    "and a new target means the old edge is no longer current."
)


def _mode() -> str:
    return str(getattr(settings, "memory_consistency_mode", "off") or "off").lower()


def enabled() -> bool:
    """True when the pass should run at all (log or enforce)."""
    return _mode() in {"log", "enforce"}


def enforcing() -> bool:
    """True when detected conflicts should actually close the stale edge."""
    return _mode() == "enforce"


def find_conflicts(user_id: str, source: str, relation_type: str,
                   target: str) -> list[str]:
    """Return current targets that would conflict with ``(source, type, target)``.

    Empty list when the type is not single-valued, when there is no conflict,
    or when the graph is unavailable (fail-open: an unreachable graph must not
    block consolidation — the write path already degrades the same way).
    """
    if relation_type not in SINGLE_VALUED_TYPES:
        return []
    try:
        from prax.services.memory import graph_store

        current = graph_store.current_targets(
            user_id=user_id, source_name=source, relation_type=relation_type)
    except Exception:
        logger.debug("consistency: graph unavailable for %s -[%s]->",
                     source, relation_type, exc_info=True)
        return []
    tgt = target.strip().lower()
    return [t for t in current if t != tgt]


def enforce(user_id: str, rel: dict, result) -> None:
    """Check one extracted relation before it is written; act per settings.

    Mutates ``result`` counters (``conflicts_detected`` /
    ``conflicts_superseded``). In log-only mode nothing else happens; in
    auto-supersede mode the stale edges are closed so the incoming write
    becomes the single current edge.
    """
    source = rel.get("source", "")
    rtype = rel.get("type", "")
    target = rel.get("target", "")
    conflicts = find_conflicts(user_id, source, rtype, target)
    if not conflicts:
        return

    result.conflicts_detected += len(conflicts)
    auto = enforcing()
    logger.warning(
        "memory consistency: %s -[%s]-> %s conflicts with current target(s) %s (%s)",
        source, rtype, target, conflicts,
        "superseding" if auto else "log-only",
    )
    if not auto:
        return

    from prax.services.memory import graph_store

    for old_target in conflicts:
        try:
            if graph_store.supersede_relation(
                user_id=user_id, source_name=source,
                relation_type=rtype, target_name=old_target,
            ):
                result.conflicts_superseded += 1
        except Exception:
            logger.debug("consistency: supersede failed for %s -[%s]-> %s",
                         source, rtype, old_target, exc_info=True)
