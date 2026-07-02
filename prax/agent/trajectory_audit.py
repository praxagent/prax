"""Post-hoc trajectory audit (LlamaFirewall-style) — did a completed turn actually
walk the lethal trifecta?

The per-turn ``trifecta`` guard *enforces* at the moment a risky sink is about to
fire (and only when ``LETHAL_TRIFECTA_GUARD`` is on). This module *observes* the
whole finished trajectory and flags the exact chain — **untrusted-content ingest →
private-data read → external sink** — after the fact. It runs regardless of the
enforcement flag, so a deployment can watch for the exfiltration pattern (and grade
it in evals) before committing to hard gating.

Deterministic, no LLM: it reuses the same trust classifier as the live guard, so
the audit and the gate can never drift apart.
"""
from __future__ import annotations

from collections.abc import Iterable

from prax.agent.trifecta import (
    is_external_sink,
    is_private_data,
    is_untrusted_source,
)


def audit_trajectory(tool_names: Iterable[str]) -> dict | None:
    """Scan an ORDERED sequence of tool-call names for a completed lethal trifecta.

    Returns a finding at the first external SINK that fires *after* both an
    untrusted-source leg AND a private-data leg have already occurred earlier in
    the trajectory — the untrusted→private→sink exfiltration chain. Legs from the
    *same* call as the sink don't count (the data must have been ingested/read
    before it could leak). Returns ``None`` when no such chain exists.
    """
    untrusted_seen = private_seen = False
    untrusted_by: str | None = None
    private_by: str | None = None
    for name in tool_names:
        if not name:
            continue
        # Check BEFORE recording this call's own legs: the sink can only exfiltrate
        # data that a PRIOR call ingested/read.
        if is_external_sink(name) and untrusted_seen and private_seen:
            return {
                "sink": name,
                "untrusted_source": untrusted_by,
                "private_data": private_by,
                "reason": (
                    "external sink fired AFTER untrusted-content ingest AND "
                    "private-data read — completed lethal trifecta (exfiltration "
                    "path). Confirm the sink was user-intended, not injection-driven."
                ),
            }
        if is_untrusted_source(name):
            untrusted_seen = True
            untrusted_by = untrusted_by or name
        if is_private_data(name):
            private_seen = True
            private_by = private_by or name
    return None


def tool_names_in_order(messages: list) -> list[str]:
    """Extract the tool-call names from a message list IN CALL ORDER.

    Reads ``AIMessage.tool_calls`` across the conversation so the trajectory audit
    sees the true temporal sequence of actions.  Degrades to ``[]`` if the message
    shape is unexpected.
    """
    names: list[str] = []
    for msg in messages or []:
        calls = getattr(msg, "tool_calls", None)
        if not calls:
            continue
        for tc in calls:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name:
                names.append(name)
    return names


def audit_trajectory_messages(messages: list) -> dict | None:
    """Convenience: run :func:`audit_trajectory` over a LangChain message list."""
    return audit_trajectory(tool_names_in_order(messages))
