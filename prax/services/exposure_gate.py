"""The universal public-exposure gate: nothing goes on a public link unless a
person decided it, for that exact thing.

Two surfaces, two trust levels (TJ, 2026-10-03):

- **TeamWork is trusted.** It sits behind its own login and is reached on
  loopback, the tailnet or an SSH tunnel. That is a deployment duty: whoever
  runs it must keep it that way (docs/security/public-exposure.md,
  docs/security/network-exposure.md).
- **A public link is not.** An ngrok URL has no password: anyone who gets the
  link can open it. So every way of putting something there needs an explicit
  decision by a person.

Enforced twice, so a new code path cannot skip it:

1. **At the tool.** Every publishing tool is an always-on hard floor
   (``hard_floors._EXPOSURE``). Governance runs it only on a person's approval
   in TeamWork (a timed grant doesn't count) or the user's own words naming
   the thing to share, and then runs the tool body inside
   :func:`person_decided`.
2. **At the registry.** ``share_registry`` — the single source of truth for
   what is publicly reachable — calls :func:`require_decision` before it
   registers anything, and refuses (:class:`ExposureNotApproved`) outside such
   a decision. A future feature that tries to publish without going through
   the gate fails closed instead of leaking.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_decision: ContextVar[str | None] = ContextVar("prax_exposure_decision", default=None)


class ExposureNotApproved(PermissionError):
    """Something tried to make a thing public without a person's decision."""


@contextmanager
def person_decided(label: str) -> Iterator[None]:
    """Run the enclosed code as approved by a person — *label* says how
    (``person:<approval id>``, ``user_message``) and is recorded on the share."""
    if not label:
        raise ValueError("a person's decision needs a label")
    token = _decision.set(label)
    try:
        yield
    finally:
        _decision.reset(token)


def current_decision() -> str | None:
    return _decision.get()


def require_decision(what: str) -> str:
    """The decision label for making *what* public, or raise."""
    label = _decision.get()
    if not label:
        raise ExposureNotApproved(
            f"Not made public: {what} would be reachable by anyone with the link, "
            "and no person has approved that. Only a publishing tool approved by "
            "the user can make something public.")
    return label
