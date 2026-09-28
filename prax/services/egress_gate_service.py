"""Prax's side of the egress gates: taint, and nothing else.

Two gates judge outbound traffic — prax-sandbox's egress gate (the sandbox's
only exit) and the secrets proxy's egress policy (Prax's own traffic). When
their policy says "ask", **TeamWork** relays the question to a person and the
answer back (teamwork ``EGRESS_GATES``). Prax deliberately holds no gate admin
token: whoever holds it can approve anything, and Prax is the party being
judged.

What Prax does hold is each gate's **raise-only taint token**. A turn taints
the gates once it has read private data (the lethal-trifecta private leg) or is
about to run code in the sandbox — whose ``/workspace`` *is* the user's data.
While any such turn is live, Prax re-asserts taint well inside the gates' TTL;
when none is, it stops, and taint lapses at the gates on its own (a raise-only
token cannot clear it — so a compromised Prax cannot un-taint either). Each
tainted turn holds a lease, so a turn that never reaches its cleanup cannot
pin the state; updates are sent synchronously and one at a time.

Taint is per container / per process group, not per process, and for Prax's
own traffic it does not count the conversation Prax always holds.
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

_lock = threading.Lock()
_send_lock = threading.Lock()                     # one taint update at a time
_tainted_turns: dict[int, tuple[str, float]] = {}  # turn -> (reason, lease expiry)
_last_sent: tuple[bool, float] | None = None       # (state, when)
_poller: threading.Thread | None = None


def _lease_seconds() -> float:
    s = _settings()
    longest_run = max(int(getattr(s, "agent_run_max_timeout", 1800) or 1800),
                      int(getattr(s, "agent_run_timeout", 300) or 300))
    return longest_run + 300


def _settings():
    from prax.settings import settings
    return settings


def _gates() -> list[tuple[str, str, str]]:
    """``(name, url, token)`` for each configured gate.

    ``sandbox`` — prax-sandbox's egress gate (the sandbox's only exit);
    ``prax`` — the forward proxy's egress policy (Prax's own traffic). Both
    speak the same admin API.
    """
    s = _settings()
    gates = []
    for name, url_f, tok_f in (("sandbox", "egress_gate_url", "egress_gate_taint_token"),
                               ("prax", "prax_egress_gate_url", "prax_egress_gate_taint_token")):
        url, tok = getattr(s, url_f, "") or "", getattr(s, tok_f, "") or ""
        if url and tok:
            gates.append((name, url.rstrip("/"), tok))
    return gates


def configured() -> bool:
    return bool(_gates())


def _call(method: str, path: str, body: dict | None = None, gate: str = "sandbox") -> dict:
    match = [g for g in _gates() if g[0] == gate]
    if not match:
        raise LookupError(f"egress gate {gate!r} is not configured")
    _, url, token = match[0]
    resp = requests.request(
        method, url + path,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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
    """The turn is over. Taint is not cleared — the raise-only token cannot —
    it simply stops being re-asserted and lapses at the gates within their TTL."""
    if not configured():
        return
    with _lock:
        _tainted_turns.pop(turn_key, None)


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
        if not tainted:
            _last_sent = None  # nothing to assert; the gates' TTL clears it
            return
        now = time.monotonic()
        due = _last_sent is None or now - _last_sent[1] >= _TAINT_REFRESH
        if not (force or due):
            return
        ok = True
        for name, _, _ in _gates():
            try:
                _call("POST", "/taint", {"tainted": tainted, "reason": reason[:200], "ttl": _TAINT_TTL},
                      gate=name)
            except Exception as exc:
                ok = False
                logger.warning("Could not update %s egress-gate taint (%s): %s", name, tainted, exc)
        _last_sent = (tainted, now) if ok else None  # unknown: try again next tick


def _loop() -> None:
    while True:
        try:
            _sync()  # re-assert taint while tainted; expire abandoned leases
        except Exception as exc:
            logger.debug("egress taint sync failed: %s", exc)
        time.sleep(_POLL_SECONDS)


def start() -> bool:
    """Start the taint refresher (idempotent). ``True`` if running."""
    global _poller
    if not configured():
        return False
    with _lock:
        if _poller is None or not _poller.is_alive():
            _poller = threading.Thread(target=_loop, name="egress-taint", daemon=True)
            _poller.start()
            logger.info("Egress gates: keeping taint on %s", ", ".join(g[0] for g in _gates()))
    return True
