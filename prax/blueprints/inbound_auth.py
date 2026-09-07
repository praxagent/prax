"""Inbound API-key check for Prax's own HTTP routes (default-off).

Gated on ``PRAX_API_KEY`` (``settings.prax_api_key``).  Empty — the default —
means **no check**: prior behaviour, where the network perimeter is the only
control on ``/teamwork/*``, ``/plugins/*`` and ``/api/users/*``.  Set, every
request to a blueprint this hook is attached to must present the same value
in ``X-API-Key`` (or ``Authorization: Bearer <key>``) or it is answered
``401 {"error": "unauthorized"}`` before the view runs.

TeamWork sends the header on every upstream call when its own ``PRAX_API_KEY``
is set to the same value (``teamwork/routers/prax.py::prax_headers``).

Not covered on purpose: the Twilio routes (signature validation in
``twilio_auth.py``), ``/mcp`` (its own bearer), and the liveness probes
(``/health``, ``/healthz/*``) — none of those live on the guarded blueprints.
"""
from __future__ import annotations

import hmac

from flask import jsonify, request


def _presented_key() -> str:
    """The credential on the request, or ``""`` when there is none."""
    key = request.headers.get("X-API-Key")
    if key:
        return key
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        return token.strip()
    return ""


def require_prax_api_key():
    """``before_request`` hook: 401 unless the request carries ``PRAX_API_KEY``.

    Returns ``None`` (let the view run) when the setting is empty or the
    presented key matches; otherwise a JSON 401 with no detail.  The presented
    value is never logged or echoed.
    """
    # Read at request time, not import time: tests reload ``prax.settings``,
    # and a name bound at import would keep checking the stale instance.
    from prax.settings import settings

    expected = settings.prax_api_key
    if not expected:
        return None

    presented = _presented_key()
    # Bytes on both sides: ``compare_digest`` refuses non-ASCII ``str`` with a
    # TypeError, which a hostile header could otherwise trigger.
    if presented and hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        return None
    return jsonify({"error": "unauthorized"}), 401
