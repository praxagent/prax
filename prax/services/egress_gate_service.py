"""Prax's side of the sandbox egress gate (prax-sandbox ``docker-compose.egress.yml``).

The gate is the sandbox's only way out. When its policy says "ask" about a
destination, the request waits in the gate; this service puts the question to a
person through TeamWork's approval dialog and posts the answer back. It also
keeps the gate's taint flag, so the policy's ``clean_only`` destinations stop
being automatic while work that has read private data is in flight.

Taint is coarse on purpose: a turn taints the sandbox once it has read private
data (the lethal-trifecta private leg) or run code in the sandbox — whose
``/workspace`` *is* the user's data — and the flag stays set until the last
such turn ends. It is per container, not per process: the gate cannot tell
which process inside the sandbox made a request.

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
_TAINT_TTL = 1800  # the gate forgets taint if Prax stops refreshing it

_lock = threading.Lock()
_tainted_turns: dict[int, str] = {}
_handling: set[str] = set()
_poller: threading.Thread | None = None


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
    """This turn has read private data (or run code over the workspace)."""
    if not configured():
        return
    with _lock:
        first = not _tainted_turns
        _tainted_turns[turn_key] = reason
    if first:
        _send_taint(True, reason)


def release(turn_key: int) -> None:
    """The turn is over; clear taint once no tainted turn remains."""
    if not configured():
        return
    with _lock:
        if _tainted_turns.pop(turn_key, None) is None:
            return
        last = not _tainted_turns
    if last:
        _send_taint(False, "")


def _send_taint(tainted: bool, reason: str) -> None:
    def send():
        try:
            _call("POST", "/taint", {"tainted": tainted, "reason": reason[:200], "ttl": _TAINT_TTL})
        except Exception as exc:
            logger.warning("Could not update egress-gate taint (%s): %s", tainted, exc)
    threading.Thread(target=send, name="egress-taint", daemon=True).start()


# --- answering the gate's questions -----------------------------------------------

def answer(item: dict) -> bool:
    """Ask a person about one pending destination; tell the gate. Returns allow."""
    from prax.services.approval_service import ask_and_wait

    host, port = item.get("host", "?"), item.get("port", 0)
    tainted = bool(item.get("tainted"))
    reason = (f"The sandbox wants to connect to {host}:{port}"
              + (" while the current work has read private data." if tainted else "."))
    outcome = ask_and_wait(
        f"prax.egress.{host}",
        {"host": host, "port": port, "method": item.get("method"), "path": item.get("path"),
         "tainted": tainted},
        reason=reason, wait_seconds=float(getattr(_settings(), "approval_wait_seconds", 300)))
    allow = outcome.approved
    try:
        _call("POST", f"/pending/{item['id']}",
              {"allow": allow, "by": "a person in TeamWork" if allow else f"no approval ({outcome.status})"})
    except Exception as exc:
        logger.warning("Could not answer egress question %s: %s", item.get("id"), exc)
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
