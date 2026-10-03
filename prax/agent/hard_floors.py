"""Hard floors: actions that always need a person, whatever else is configured.

Pattern credit: OpenWorker (Andrew Ng and contributors,
https://github.com/andrewyng/openworker) — "dangerous operations are
human-only, always", as the first of its governance tiers.

Why Prax needs its own: every other path past the HIGH-risk gate can be
lowered or skipped —

- earned trust downgrades some HIGH browser tools to MEDIUM after enough
  self-reported successes (``earned_trust._DOWNGRADE_ELIGIBLE``), and a tool's
  own report of success is exactly what the September LeetCode incident showed
  can be false;
- the gate's fallback lets the MODEL confirm by calling the tool a second time;
- a confirmed HIGH action can unlock every other HIGH action for the turn;
- inside spokes the gate is not enforced unless ``SPOKE_GOVERNANCE_ENABLED``;
- a timed "allow for 1 hour" grant in TeamWork approves matching requests on
  arrival;
- two tools that hand the model a stored password (``browser_login``,
  ``browser_credentials``) were not classified at all, so they ran as MEDIUM.

A floor action is checked before all of that and none of it applies. It runs
only on a person's decision about that exact call: an out-of-band approval in
TeamWork that a timed grant didn't satisfy, or — when approvals aren't set up —
the user's own message naming the action and its target. Never the model's
say-so, and never page content (only the user's message is consulted).
"""
from __future__ import annotations

import re

# Built-in floors. Settings can ADD tools (HARD_FLOOR_EXTRA_TOOLS); nothing can
# remove these — a floor that can be configured away is not a floor.
#
# CREDENTIAL_TOOLS is public and holds whether or not HARD_FLOORS_ENABLED is on:
# the observability sinks (trace summaries, OTel previews, TeamWork live output,
# spoke logs) use it to withhold what these tools return, because
# browser_login hands back "username=…\npassword=…" in plain text under the
# default BROWSER_SECRETS_OUT_OF_CONTEXT=false.
CREDENTIAL_TOOLS: frozenset[str] = frozenset({
    "browser_login",          # returns a stored password to the model
    "browser_credentials",    # returns stored username and password
    "browser_fill_login",     # types stored credentials into a page
    "browser_request_login",  # starts a login the user completes
    "browser_finish_login",   # completes a login
})
_CREDENTIALS = CREDENTIAL_TOOLS
_AUTHORITY = {
    # code that then runs with Prax's own authority
    "plugin_import",
    "plugin_import_activate",
    "plugin_activate",
    "plugin_write",
    "self_improve_deploy",
}
_MONEY = {
    "gpu_power_on",           # starts a billable cloud GPU
}
BUILT_IN: frozenset[str] = frozenset(_CREDENTIALS | _AUTHORITY | _MONEY)


def enabled() -> bool:
    try:
        from prax.settings import settings
        return bool(getattr(settings, "hard_floors_enabled", False))
    except Exception:
        return False


def floor_tools() -> frozenset[str]:
    try:
        from prax.settings import settings
        extra = {t.strip() for t in str(getattr(settings, "hard_floor_extra_tools", "") or "").split(",")}
    except Exception:
        extra = set()
    return BUILT_IN | {t for t in extra if t}


def is_floor(tool_name: str) -> bool:
    return enabled() and tool_name in floor_tools()


# --- the chat fallback: the user's own message names the action and target ---

_LOGIN_VERB = re.compile(r"\b(log\s*-?\s*in|sign\s*-?\s*in|login|signin|authenticate)\b", re.I)
_INSTALL_VERB = re.compile(r"\b(install|import|activate|enable|add|write|deploy)\b", re.I)
_POWER_ON = re.compile(r"\b(start|turn\s+on|power\s+on|spin\s+up|launch|boot)\b", re.I)


def _target(kwargs: dict) -> str:
    for key in ("domain", "url", "name", "plugin_name", "plugin", "repo", "source", "instance"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            value = value.strip().lower()
            value = re.sub(r"^https?://", "", value)
            value = re.sub(r"^www\.", "", value)
            return value.split("/")[0] if key in ("domain", "url") else value
    return ""


def user_named_it(tool_name: str, kwargs: dict, message: str) -> bool:
    """True when the user's own *message* asks for this action on this target.

    Deliberately narrow: a verb for the kind of action AND the specific target
    (the site's domain, the plugin's name). "Go ahead" is not enough — that is
    how a confirmation meant for one thing unlocks another.
    """
    msg = (message or "").lower()
    if not msg:
        return False
    target = _target(kwargs)
    if tool_name in _CREDENTIALS:
        if not (_LOGIN_VERB.search(msg) and target):
            return False
        site = target.split(":")[0]
        stem = site.rsplit(".", 1)[0] if "." in site else site  # "leetcode.com" -> "leetcode"
        return site in msg or bool(stem and re.search(rf"\b{re.escape(stem)}\b", msg))
    if tool_name in _AUTHORITY:
        return bool(_INSTALL_VERB.search(msg) and target and target in msg)
    if tool_name in _MONEY:
        return bool(_POWER_ON.search(msg) and "gpu" in msg)
    return False  # an operator-added floor: approval in TeamWork only


def refusal(tool_name: str, target: str, *, approvals: bool) -> str:
    what = f"{tool_name}" + (f" for {target}" if target else "")
    how = ("approve it in TeamWork when asked" if approvals else
           f"say so in your own words, naming it (for example: \"yes, {_example(tool_name, target)}\")")
    return (
        f"⛔ Not done: {what} is a hard-floor action — it always needs a person's "
        f"decision for this exact call, and none has been given. Do not retry it "
        f"or look for another way to do it. Tell the user what you need and why; "
        f"to allow it they should {how}."
    )


def _example(tool_name: str, target: str) -> str:
    if tool_name in _CREDENTIALS:
        return f"log in to {target or 'the site'}"
    if tool_name in _AUTHORITY:
        return f"install {target or 'the plugin'}"
    if tool_name in _MONEY:
        return "start the GPU"
    return f"run {tool_name}"
