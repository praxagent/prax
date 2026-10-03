"""Secret values handed out this turn, so no observability sink repeats them.

Under the default ``BROWSER_SECRETS_OUT_OF_CONTEXT=false``, ``browser_login``
returns ``username=…\\npassword=…`` and tells the model to type the password
with ``browser_fill(selector, text)``. The credential tool's own output is
withheld from the sinks (``message_text.preview_for_tool``), but
``browser_fill`` is an ordinary tool, so the password came back as its ``text``
argument and was copied, unmasked, into the OTel input preview, the trace's
running node, the spoke tool log, governance's INFO log and the audit entry the
orchestrator writes to the workspace ``trace.log``.

So a credential tool's secret values are remembered for the rest of the turn,
and every sink passes its text through :func:`scrub`, which replaces them with
``***``. The model's own context is never scrubbed: it needs the value.

What is registered, exactly:

- from the output of a ``hard_floors.CREDENTIAL_TOOLS`` call (governance calls
  :func:`register_from_output` when one returns): every line ``key=value`` or
  ``key: value`` whose key names a secret (``_SECRET_KEY``: password,
  passphrase, secret, token, otp, code, …), value to the end of the line;
- values the browser credential tools register at the source
  (:func:`register`): the stored password that ``browser_login``,
  ``browser_credentials`` and ``browser_fill_login`` read, and the
  secret-named fields ``browser_credentials`` prints on one comma-joined line
  that a line parser cannot split reliably.

Each value is registered as written and as ``repr()``/``json.dumps()`` render
it inside a dict or JSON document (escaped quotes, backslashes, non-ASCII).
Values shorter than :data:`MIN_LENGTH` characters are not registered: they
would blank out ordinary words. Matching is exact: a secret the model splits,
re-encodes or transforms is not caught.

Lifetime: the values live on the turn's ``TurnGovernanceState`` — empty at
``begin_turn``, cleared when the turn's audit log is drained, shared with the
contexts the turn copies (tools, spokes, parallel workers) and with no other
turn or user. (Outside any turn, a registration creates a state visible only
in the context that made it — ``governed_tool.current_turn_state``.) In
memory only: never written, logged or hashed by this module,
and no regex is compiled from them (``re`` caches compiled patterns
process-wide, which would keep them past the turn).
"""
from __future__ import annotations

import json
import re

from prax.agent.hard_floors import CREDENTIAL_TOOLS

__all__ = ["MIN_LENGTH", "MASK", "register", "register_from_output", "scrub", "scrubber",
           "is_secret_key"]

#: Shortest value that is registered.
MIN_LENGTH = 4
#: What a registered value is replaced with.
MASK = "***"

# Field names whose values are secrets. Substring match, so "totp_secret",
# "api_token" and "backup_code" count; over-matching costs only a masked value.
_SECRET_KEY = re.compile(
    r"pass(?:word|phrase|wd|code)|pwd|secret|token|otp|code|api_?key", re.IGNORECASE)
# One "key=value" / "key: value" line. The key may hold spaces ("Recovery code");
# the value runs to the end of the line, so a password containing "=", ":" or
# "," is kept whole.
_FIELD_LINE = re.compile(r"^[ \t]*([A-Za-z][\w .-]{0,40}?)[ \t]*[=:][ \t]*(\S.*?)[ \t]*$",
                         re.MULTILINE)


def is_secret_key(key: str) -> bool:
    """Whether a field named *key* holds a secret."""
    return bool(_SECRET_KEY.search(key or ""))


def _forms(value: str) -> set[str]:
    """*value* as written and as a dict repr / JSON document renders it."""
    forms = {value, repr(value)[1:-1], json.dumps(value)[1:-1],
             json.dumps(value, ensure_ascii=False)[1:-1]}
    return {f for f in forms if len(f) >= MIN_LENGTH}


def register(*values) -> None:
    """Remember *values* as secrets for the rest of this turn. Never raises."""
    try:
        forms: set[str] = set()
        for value in values:
            if value is None or isinstance(value, (dict, list, tuple, set)):
                continue
            text = str(value).strip()
            if len(text) >= MIN_LENGTH:
                forms |= _forms(text)
        if forms:
            # Lazy: governed_tool imports this module's users at import time.
            from prax.agent.governed_tool import current_turn_state
            current_turn_state().secrets.update(forms)
    except Exception:  # noqa: BLE001 - redaction must never break the tool path
        pass


def register_from_output(tool_name: str | None, text: str | None) -> None:
    """Register the secret-named fields of a credential tool's output.

    Does nothing for a tool outside ``hard_floors.CREDENTIAL_TOOLS``: an
    ordinary tool's ``password: …`` line is page content, not a stored secret.
    """
    if tool_name not in CREDENTIAL_TOOLS or not isinstance(text, str):
        return
    register(*(m.group(2) for m in _FIELD_LINE.finditer(text) if is_secret_key(m.group(1))))


def _registered() -> list[str]:
    """This turn's values, longest first ([] outside a turn). Never raises."""
    try:
        from prax.agent.governed_tool import peek_turn_state
        state = peek_turn_state()  # never creates one: a sink is not a turn
        if state is None or not state.secrets:
            return []
        return sorted(tuple(state.secrets), key=len, reverse=True)
    except Exception:  # noqa: BLE001
        return []


def _mask(text, secrets: list[str]):
    if not isinstance(text, str) or not text:
        return text
    for secret in secrets:  # longest first: a secret containing another goes whole
        if secret in text:
            text = text.replace(secret, MASK)
    return text


def scrub(text):
    """*text* with every value registered this turn replaced by ``***``.

    Returns *text* unchanged when it is not a string or nothing is registered
    (the common case: one dict lookup). Never raises.
    """
    if not isinstance(text, str) or not text:
        return text
    return _mask(text, _registered())


def scrubber():
    """A :func:`scrub` bound to the values registered so far.

    For a sink written while the turn is ending, after draining its audit log
    has cleared the turn's state (the orchestrator's ``trace.log``). It holds
    the values: keep it local to that one write.
    """
    secrets = _registered()
    return lambda text: _mask(text, secrets)
