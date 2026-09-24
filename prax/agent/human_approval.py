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
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_POLL_SECONDS = 2.0
_TERMINAL = ("rejected", "consumed")


@dataclass(frozen=True)
class Decision:
    approved: bool
    message: str
    approval_id: str | None = None


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
    """Ask a person, wait for the answer, and spend it. Never raises."""
    from prax.services.teamwork_service import get_teamwork_client
    from prax.settings import settings

    client = get_teamwork_client()
    if not client.enabled:
        return Decision(False, (
            "Refused: this action needs a person's approval in TeamWork, and TeamWork "
            "is not configured. Tell the user it was not done."))

    capability = f"{capability_prefix}{tool_name}"[:64]
    payload = action_payload(tool_name, kwargs, kind, summary)
    project_id = client.project_id
    try:
        asked = client.ask_approval(capability, payload, reason=reason, project_id=project_id)
    except Exception as exc:
        logger.warning("Could not create approval request for %s: %s", tool_name, exc)
        return Decision(False, (
            "Refused: the approval service could not be reached, so this action was "
            "not done. Tell the user."))

    approval_id = asked.get("approval_id")
    status = asked.get("status")
    wait = max(5, int(getattr(settings, "approval_wait_seconds", 300)))
    deadline = time.monotonic() + wait
    logger.info("Waiting up to %ss for approval %s of %s", wait, approval_id, tool_name)
    while status == "pending" and not asked.get("expired") and time.monotonic() < deadline:
        _heartbeat(f"waiting for approval of {tool_name}")
        time.sleep(_POLL_SECONDS)
        try:
            asked = client.approval_status(approval_id)
            status = asked.get("status")
        except Exception as exc:
            logger.warning("Approval status check failed for %s: %s", approval_id, exc)

    if status == "pending":
        why = "expired" if asked.get("expired") else f"no answer within {wait}s"
        return Decision(False, (
            f"Not approved ({why}): the user did not approve {tool_name} in TeamWork. "
            "Do not retry it on your own; tell the user it is waiting on their approval."),
            approval_id)
    if status in _TERMINAL:
        return Decision(False, (
            f"The user DENIED {tool_name} in TeamWork. Do not retry it or look for "
            "another way to do the same thing; tell the user it was not done."),
            approval_id)
    if status != "approved":
        return Decision(False, f"Refused: unexpected approval state {status!r}.", approval_id)

    try:
        client.consume_approval(approval_id, capability, payload, project_id=project_id)
    except Exception as exc:
        logger.warning("Approval %s could not be spent: %s", approval_id, exc)
        return Decision(False, "Refused: the approval could not be used for this exact action.",
                        approval_id)
    logger.info("Approval %s granted and spent for %s", approval_id, tool_name)
    return Decision(True, "", approval_id)
