"""Out-of-band human approval for Prax's own risky actions.

Without this, the HIGH-risk and lethal-trifecta gates in ``governed_tool`` block
the first call and tell the model to "confirm with the user, then call again
with the same arguments" — and the second call runs. Nothing checks that a
person was ever asked, so the model can confirm itself (July review #8). An
injected model is exactly the party that would.

With ``OUT_OF_BAND_APPROVALS_ENABLED`` the gate instead:

1. creates an approval request in TeamWork for this exact action (tool name +
   a hash of its full arguments, plus a readable summary for the person);
2. **stops** — the tool call blocks, heartbeating so the run is not abandoned
   as idle, until a person answers in the TeamWork UI's approval dialog, the
   request expires, or ``APPROVAL_WAIT_SECONDS`` pass;
3. on approval, spends it (single use, bound to this exact action) and lets
   the call run; on anything else, refuses.

The decision never passes through the model. Prax reads it from TeamWork with
its own credential, and TeamWork only accepts decisions on its human route,
which refuses agent credentials. An approval is therefore as strong as access
to the TeamWork UI itself — see ``docs/security/out-of-band-approvals.md``.

Fail closed: if TeamWork cannot be reached, the action is refused, never
waved through.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    approved: bool
    message: str
    approval_id: str | None = None
    decided_by: str = ""


def enabled() -> bool:
    from prax.settings import settings
    return bool(getattr(settings, "out_of_band_approvals_enabled", False))


def action_payload(tool_name: str, kwargs: dict, kind: str, summary: str) -> dict:
    """What the approval is bound to, and what the person is shown.

    The hash covers the FULL arguments, so an approval for one set of
    arguments cannot be spent on another; the summary is only for reading.
    """
    canonical = json.dumps(kwargs, sort_keys=True, separators=(",", ":"), default=str)
    return {
        "tool": tool_name,
        "kind": kind,
        "args": summary,
        "args_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _heartbeat(message: str) -> None:
    try:
        from prax.agent.loop_middleware import current_heartbeat
        hb = current_heartbeat.get()
        if hb is not None:
            hb.touch("approval", message)
    except Exception:
        pass


def request(tool_name: str, kwargs: dict, *, kind: str, reason: str, summary: str,
            capability_prefix: str = "prax.tool.") -> Decision:
    """Ask a person, wait for the answer, and spend it. Never raises.

    In an unattended run with PARKED_APPROVALS_ENABLED, doesn't wait: parks the
    request and returns a refusal saying so (prax.services.parked_approvals).
    A re-run started by a person's approval spends that approval here.
    """
    from prax.services import parked_approvals
    from prax.services.approval_service import ask_and_wait, spend
    from prax.settings import settings

    capability = f"{capability_prefix}{tool_name}"
    payload = action_payload(tool_name, kwargs, kind, summary)

    given = (parked_approvals.current_preapproved.get() or {}).get(
        parked_approvals.action_key(capability, payload))
    if given and spend(given["approval_id"], capability, payload):
        logger.info("Parked approval %s spent for %s on the re-run", given["approval_id"], tool_name)
        return Decision(True, "", given["approval_id"], given.get("decided_by", ""))

    parked = _park_if_unattended(tool_name, capability, payload, reason)
    if parked is not None:
        return parked

    wait = max(5, int(getattr(settings, "approval_wait_seconds", 300)))
    outcome = ask_and_wait(
        capability, payload,
        reason=reason, wait_seconds=wait,
        on_wait=lambda: _heartbeat(f"waiting for approval of {tool_name}"))

    if outcome.approved:
        logger.info("Approval %s granted and spent for %s", outcome.approval_id, tool_name)
        return Decision(True, "", outcome.approval_id, outcome.decided_by)
    if outcome.status == "unavailable":
        return Decision(False, (
            "Refused: this action needs a person's approval in TeamWork, and the approval "
            "service is not available, so it was not done. Tell the user."))
    if outcome.status == "pending":
        why = "expired" if outcome.detail == "expired" else f"no answer within {wait}s"
        return Decision(False, (
            f"Not approved ({why}): the user did not approve {tool_name} in TeamWork. "
            "Do not retry it on your own; tell the user it is waiting on their approval."),
            outcome.approval_id)
    if outcome.status == "rejected":
        return Decision(False, (
            f"The user DENIED {tool_name} in TeamWork. Do not retry it or look for "
            "another way to do the same thing; tell the user it was not done."),
            outcome.approval_id)
    return Decision(False, f"Refused: {outcome.detail or 'the approval could not be used'}.",
                    outcome.approval_id)


def _park_if_unattended(tool_name: str, capability: str, payload: dict,
                        reason: str) -> Decision | None:
    """Park the request when nobody is there to answer it; None = ask normally."""
    import time
    from datetime import UTC, datetime

    from prax.agent.user_context import current_channel_id, current_turn_source, current_user_id
    from prax.services import parked_approvals
    from prax.services.approval_service import ask_and_wait
    from prax.settings import settings

    recipe = parked_approvals.current_recipe.get()
    if (not parked_approvals.enabled() or not recipe
            or current_turn_source.get() not in ("scheduler", "task_runner")):
        return None
    if int(recipe.get("resumes", 0)) >= int(getattr(settings, "parked_max_resumes", 3)):
        return Decision(False, (
            f"Not done: this unattended task has already been resumed "
            f"{recipe.get('resumes')} times for approvals, and {tool_name} needs another. "
            "Stop here and tell the user what still needs their approval."))
    hours = float(getattr(settings, "parked_approval_hours", 24))
    outcome = ask_and_wait(capability, payload, reason=reason, wait_seconds=1,
                           expires_in_seconds=int(hours * 3600))
    if outcome.approved:  # a standing grant answered on arrival
        return Decision(True, "", outcome.approval_id, outcome.decided_by)
    if outcome.status != "pending" or not outcome.approval_id:
        return Decision(False, (
            "Refused: this action needs a person's approval in TeamWork, and it could not "
            "be requested, so it was not done. Tell the user."), outcome.approval_id)
    try:
        when = datetime.fromisoformat(outcome.expires_at) if outcome.expires_at else None
        if when is not None and when.tzinfo is None:  # TeamWork stores naive UTC
            when = when.replace(tzinfo=UTC)
        expires = when.timestamp() if when else 0.0
    except ValueError:
        expires = 0.0
    parked_approvals.park(
        user_id=current_user_id.get() or "", approval_id=outcome.approval_id,
        tool_name=tool_name, capability=capability, payload=payload, recipe=recipe,
        channel=(recipe.get("args") or {}).get("channel") or current_channel_id.get(),
        expires_at=expires or time.time() + hours * 3600)
    return Decision(False, parked_approvals.parked_message(tool_name, hours), outcome.approval_id)
