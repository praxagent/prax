"""Ask a person in TeamWork and wait for the answer — the shared core.

Two callers put questions to a person out of band:

* ``prax.agent.human_approval`` — a HIGH-risk or trifecta-closing tool call;
* ``prax.services.egress_gate_service`` — the sandbox wanting to reach a
  destination its egress policy says to ask about.

Both need the same thing: create an approval for an exact action, wait for a
decision that only a person can make (TeamWork refuses agent credentials on
its decision route), then spend it once. Nothing here trusts anything the
model says; the decision is read from TeamWork with Prax's own credential.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0


@dataclass(frozen=True)
class Outcome:
    """``status`` is approved | rejected | pending (timed out) | unavailable | error."""
    status: str
    approval_id: str | None = None
    detail: str = ""

    @property
    def approved(self) -> bool:
        return self.status == "approved"


def ask_and_wait(capability: str, payload: dict, *, reason: str, wait_seconds: float,
                 on_wait: Callable[[], None] | None = None, spend: bool = True) -> Outcome:
    """Create an approval request, block until decided or *wait_seconds*, spend it.

    Never raises. Fails closed: anything other than a decided-and-spent
    approval comes back not approved.
    """
    from prax.services.teamwork_service import get_teamwork_client

    client = get_teamwork_client()
    if not client.enabled:
        return Outcome("unavailable", detail="TeamWork is not configured")
    capability = capability[:64]
    project_id = client.project_id
    try:
        asked = client.ask_approval(capability, payload, reason=reason, project_id=project_id)
    except Exception as exc:
        logger.warning("Could not create approval request for %s: %s", capability, exc)
        return Outcome("unavailable", detail=str(exc)[:200])

    approval_id = asked.get("approval_id")
    status = asked.get("status")
    deadline = time.monotonic() + max(1.0, float(wait_seconds))
    while status == "pending" and not asked.get("expired") and time.monotonic() < deadline:
        if on_wait:
            on_wait()
        time.sleep(POLL_SECONDS)
        try:
            asked = client.approval_status(approval_id)
            status = asked.get("status")
        except Exception as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code is not None and 400 <= code < 500:
                # The request is gone or not ours: waiting cannot fix that.
                logger.warning("Approval %s cannot be checked (%s); refusing", approval_id, code)
                return Outcome("error", approval_id, f"approval status returned {code}")
            logger.warning("Approval status check failed for %s: %s", approval_id, exc)

    if status == "pending":
        return Outcome("pending", approval_id, "expired" if asked.get("expired") else "no answer")
    if status in ("rejected", "consumed"):
        return Outcome("rejected", approval_id)
    if status != "approved":
        return Outcome("error", approval_id, f"unexpected state {status!r}")
    if spend:
        try:
            client.consume_approval(approval_id, capability, payload, project_id=project_id)
        except Exception as exc:
            logger.warning("Approval %s could not be spent: %s", approval_id, exc)
            return Outcome("error", approval_id, "could not be spent for this exact action")
    return Outcome("approved", approval_id)
