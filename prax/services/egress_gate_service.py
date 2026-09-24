"""Prax's side of the sandbox egress gate (prax-sandbox ``docker-compose.egress.yml``).

The gate is the sandbox's only way out. When its policy says "ask" about a
destination, the request waits in the gate; this service puts the question to a
person through TeamWork's approval dialog and posts the answer back. It also
keeps the gate's taint flag, so the policy's ``clean_only`` destinations stop
being automatic while work that has read private data is in flight.

Taint is coarse on purpose: a turn taints the sandbox once it has read private
data (the lethal-trifecta private leg) or is about to run code in the sandbox
— whose ``/workspace`` *is* the user's data — and the flag stays set until the
last such turn ends. It is per container, not per process: the gate cannot
tell which process inside the sandbox made a request.

Taint must not fail open, so:

* every tainted turn holds a **lease** (the longest a run can last, plus
  margin): a turn that never reaches its cleanup cannot pin the set forever,
  and cannot leave the gate believing it is clean while it still is not;
* updates are sent **synchronously, one at a time** — taint reaches the gate
  before the sandbox code runs, and a "clean" can never overtake a later
  "tainted" in flight;
* while tainted, the poller **re-asserts** taint well inside the gate's TTL,
  so a long turn (or a failed send) does not lapse into "clean".

Configured by ``EGRESS_GATE_URL`` + ``EGRESS_GATE_TOKEN``; without them every
function here is a no-op.
"""
from __future__ import annotations

import json
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

_POLL_SECONDS = 2.0
_TAINT_TTL = 300        # the gate forgets taint if Prax stops re-asserting it
_TAINT_REFRESH = 60     # re-assert this often while tainted (well inside the TTL)
_ANSWER_MARGIN = 5      # stop asking a person this long before the gate gives up

_lock = threading.Lock()
_send_lock = threading.Lock()                     # one taint update at a time
_tainted_turns: dict[int, tuple[str, float]] = {}  # turn -> (reason, lease expiry)
_last_sent: tuple[bool, float] | None = None       # (state, when)
_handling: set[str] = set()
_poller: threading.Thread | None = None


def _lease_seconds() -> float:
    s = _settings()
    longest_run = max(int(getattr(s, "agent_run_max_timeout", 1800) or 1800),
                      int(getattr(s, "agent_run_timeout", 300) or 300))
    return longest_run + 300


def _settings():
    from prax.settings import settings
    return settings


def configured() -> bool:
    s = _settings()
    return bool(getattr(s, "egress_gate_url", "") and getattr(s, "egress_gate_token", ""))


def _call(method: str, path: str, body: dict | None = None) -> dict:
    s = _settings()
    resp = requests.request(
        method, s.egress_gate_url.rstrip("/") + path,
        headers={"Authorization": f"Bearer {s.egress_gate_token}",
                 "Content-Type": "application/json"},
        data=json.dumps(body) if body is not None else None, timeout=5)
    resp.raise_for_status()
    return resp.json()


# --- taint -------------------------------------------------------------------

def mark_tainted(turn_key: int, reason: str) -> None:
    """This turn has read private data, or is about to run code over the workspace.

    Synchronous: when this returns, the gate has been told (or the failure is
    logged) — so the sandbox code that follows runs under taint.
    """
    if not configured():
        return
    with _lock:
        _tainted_turns[turn_key] = (reason, time.monotonic() + _lease_seconds())
    _sync(force=True)


def release(turn_key: int) -> None:
    """The turn is over; clear taint once no tainted turn remains."""
    if not configured():
        return
    with _lock:
        if _tainted_turns.pop(turn_key, None) is None:
            return
    _sync(force=True)


def _desired() -> tuple[bool, str]:
    """Prune expired leases; is any turn still tainted, and why?"""
    now = time.monotonic()
    with _lock:
        for key in [k for k, (_, exp) in _tainted_turns.items() if exp <= now]:
            logger.warning("Egress taint lease for turn %s expired without release", key)
            _tainted_turns.pop(key, None)
        reasons = [r for r, _ in _tainted_turns.values()]
    return bool(reasons), (reasons[0] if reasons else "")


def _sync(force: bool = False) -> None:
    """Send the current taint state to the gate — serialized, never reordered.

    ``force`` sends even if unchanged (a new turn's reason, a release); the
    poller calls without it and sends only on change or when a re-assertion
    is due.
    """
    global _last_sent
    with _send_lock:
        tainted, reason = _desired()
        now = time.monotonic()
        due = _last_sent is None or _last_sent[0] != tainted or (
            tainted and now - _last_sent[1] >= _TAINT_REFRESH)
        if not (force or due):
            return
        try:
            _call("POST", "/taint", {"tainted": tainted, "reason": reason[:200], "ttl": _TAINT_TTL})
            _last_sent = (tainted, now)
        except Exception as exc:
            _last_sent = None  # unknown: try again on the next tick
            logger.warning("Could not update egress-gate taint (%s): %s", tainted, exc)


# --- answering the gate's questions -----------------------------------------------

def answer(item: dict) -> bool:
    """Ask a person about one pending destination; tell the gate. Returns allow."""
    from prax.services.approval_service import ask_and_wait
    from prax.services.teamwork_service import get_teamwork_client

    host, port = item.get("host", "?"), item.get("port", 0)
    tainted = bool(item.get("tainted"))
    reason = (f"The sandbox wants to connect to {host}:{port}"
              + (" while the current work has read private data." if tainted else "."))
    capability = f"prax.egress.{host}"
    payload = {"host": host, "port": port, "method": item.get("method"),
               "path": item.get("path"), "tainted": tainted}
    # Ask no longer than the gate will wait: an answer after it gives up would
    # be spent on a connection that was already refused.
    wait = float(getattr(_settings(), "approval_wait_seconds", 300))
    if item.get("expires_in_seconds") is not None:
        wait = min(wait, float(item["expires_in_seconds"]) - _ANSWER_MARGIN)
    if wait <= 0:
        return False
    outcome = ask_and_wait(capability, payload, reason=reason, wait_seconds=wait, spend=False)
    allow = outcome.approved
    try:
        _call("POST", f"/pending/{item['id']}",
              {"allow": allow, "by": "a person in TeamWork" if allow else f"no approval ({outcome.status})"})
    except Exception as exc:
        logger.warning("Could not answer egress question %s: %s", item.get("id"), exc)
        return False  # the approval is left unspent; it expires on its own
    if allow:
        # Spend it only once the gate has taken the answer.
        try:
            client = get_teamwork_client()
            client.consume_approval(outcome.approval_id, capability, payload,
                                    project_id=client.project_id)
        except Exception as exc:
            logger.warning("Egress approval %s could not be spent: %s", outcome.approval_id, exc)
    return allow


def _poll_once() -> None:
    for item in _call("GET", "/pending").get("pending", []):
        key = str(item.get("id"))
        with _lock:
            if key in _handling:
                continue
            _handling.add(key)

        def run(item=item, key=key):
            try:
                answer(item)
            finally:
                with _lock:
                    _handling.discard(key)
        threading.Thread(target=run, name=f"egress-ask-{key}", daemon=True).start()


def _loop() -> None:
    while True:
        try:
            _poll_once()
        except Exception as exc:
            logger.debug("egress gate poll failed: %s", exc)
        try:
            _sync()  # re-assert taint, expire abandoned leases
        except Exception as exc:
            logger.debug("egress taint sync failed: %s", exc)
        time.sleep(_POLL_SECONDS)


def start() -> bool:
    """Start answering the gate's questions (idempotent). ``True`` if running."""
    global _poller
    if not configured():
        return False
    with _lock:
        if _poller is None or not _poller.is_alive():
            _poller = threading.Thread(target=_loop, name="egress-gate-poller", daemon=True)
            _poller.start()
            logger.info("Egress gate: answering questions at %s", _settings().egress_gate_url)
    return True
