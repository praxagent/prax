"""Steadying counsel — self-regulation when the agent loop starts to spiral.

Root cause (GPQA/MMLU-Pro audit): on hard problems the loop keeps calling tools or
re-deriving the same result without converging, context balloons, and it runs out
of budget having committed *nothing*. Nothing watches it in flight.

This is the structural rescue: a middleware detects the spiral signature and injects
a calm, reframing intervention into the next model call — the "hey, let's pause and
regroup" a good counsellor gives, which stops an LLM doubling down far better than a
cold "LOOP DETECTED". The arc is deliberate: **de-escalate → diagnose (data-driven,
what's actually going wrong) → redirect (try a genuinely different route) → stay
honest** (committing an honest "I don't know" is a valid, good answer — never
fabricate one to escape the loop).

Pure detection + message here (keyless-testable); the middleware that calls it lives
in ``loop_middleware.py`` behind ``SPIRAL_RECOVERY_ENABLED``.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import Any

# Thresholds — deliberately lenient so the counsel only fires on a *real* spiral,
# not on normal multi-step work.
REPEAT_THRESHOLD = 3          # same tool+args this many times = going in circles
STEP_THRESHOLD = 14           # this many tool calls with no answer = not converging
BUDGET_FRACTION = 0.85        # this share of the tool-call budget spent = wrap up
MODEL_CALL_THRESHOLD = 10     # this many model calls (a REASONING spiral — no tools)
CONTEXT_CHAR_THRESHOLD = 120_000  # ballooned context with no answer


def _tool_call_keys(messages: list[Any]) -> list[tuple[str, str]]:
    """(tool_name, canonical-args) for every tool call in the message history."""
    keys: list[tuple[str, str]] = []
    for m in messages or []:
        tcs = getattr(m, "tool_calls", None) or (
            m.get("tool_calls") if isinstance(m, dict) else None) or []
        for tc in tcs:
            name = (tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)) or "?"
            args = (tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})) or {}
            try:
                canon = json.dumps(args, sort_keys=True, default=str)
            except Exception:  # noqa: BLE001
                canon = str(args)
            keys.append((str(name), canon))
    return keys


def diagnose_spiral(messages: list[Any], *, budget_used: int | None = None,
                    budget_total: int | None = None,
                    model_calls: int | None = None) -> str | None:
    """Return a plain-language diagnosis of the spiral, or None if not spiralling.

    Catches BOTH tool spirals (repeated/too-many tool calls) and **reasoning
    spirals** (many model calls or a ballooning context with no answer — the
    closed-book failure mode where the agent over-thinks without ever tooling).
    Data-driven so the counsel can say *what* is going wrong, not a generic scold.
    """
    keys = _tool_call_keys(messages)
    if keys:
        (name, _), count = Counter(keys).most_common(1)[0]
        if count >= REPEAT_THRESHOLD:
            return (f"you've made the same `{name}` call {count} times now — "
                    f"the result isn't going to change on another try")
    if budget_used and budget_total and budget_used >= BUDGET_FRACTION * budget_total:
        return (f"you've used {budget_used} of your {budget_total} tool calls and "
                f"haven't landed on an answer yet")
    if len(keys) >= STEP_THRESHOLD:
        return (f"you've made {len(keys)} tool calls without reaching an answer — "
                f"you're circling, not closing in")
    # Reasoning spiral (no/few tools): being *inside* a model call already means the
    # loop hasn't ended (no final answer yet), so a high round-count or ballooned
    # context is itself the signal — the closed-book over-thinking failure mode.
    if model_calls and model_calls >= MODEL_CALL_THRESHOLD:
        return (f"you've gone {model_calls} rounds without committing an answer — "
                f"you're re-deriving, not converging")
    ctx_chars = sum(
        len(str(getattr(m, "content", "") or (m.get("content") if isinstance(m, dict) else "")))
        for m in (messages or []))
    if ctx_chars >= CONTEXT_CHAR_THRESHOLD:
        return ("your working context has ballooned while you keep going without an "
                "answer — you're accumulating, not resolving")
    return None


def _trajectory_digest(messages: list[Any], max_chars: int = 4000) -> str:
    """A compact readable digest of what the agent has been doing (for the counsel)."""
    lines: list[str] = []
    for m in (messages or [])[-24:]:
        role = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else "?")
        content = str(getattr(m, "content", "") or (m.get("content") if isinstance(m, dict) else ""))
        tcs = _tool_call_keys([m])
        if tcs:
            lines.append(f"[{role}] called {', '.join(f'{n}({a[:80]})' for n, a in tcs)}")
        elif content.strip():
            lines.append(f"[{role}] {content.strip()[:300]}")
    digest = "\n".join(lines)
    return digest[-max_chars:]


_ESCALATION_PROMPT = (
    "You are a calm, sharp senior colleague. A teammate (an AI agent) is stuck on a "
    "task — they keep going without converging. Here's the recent trace of what "
    "they've been doing:\n\n{digest}\n\nThe stall pattern: {diagnosis}.\n\n"
    "In 3–5 short sentences, help them regroup: (1) name the most likely WRONG TURN "
    "or false assumption in their approach, specifically; (2) suggest ONE concrete, "
    "genuinely DIFFERENT next step (not a repeat of what they've tried); (3) remind "
    "them that if the answer honestly can't be determined from what they have, saying "
    "\"I don't know\" is a valid, good answer — they must never fabricate one to "
    "escape the loop. Be warm, direct, and specific — not generic. Speak to them "
    "('you...'), starting with a brief steadying note."
)


def escalated_counsel(messages: list[Any], diagnosis: str,
                      complete_fn: Callable[[str], str]) -> str | None:
    """Escalate to a smarter model: it reviews the trajectory and returns specific,
    diagnostic guidance. Returns None on failure (caller falls back to the static
    :func:`steadying_message`)."""
    try:
        prompt = _ESCALATION_PROMPT.format(
            digest=_trajectory_digest(messages), diagnosis=diagnosis)
        out = (complete_fn(prompt) or "").strip()
        return out or None
    except Exception:  # noqa: BLE001 — never break the loop on a counsel failure
        return None


def steadying_message(diagnosis: str) -> str:
    """The calm counsellor: de-escalate → diagnose → redirect → stay honest."""
    return (
        "A note to yourself — pause here for a second. This is a hard one, and that's "
        "okay; there's no need to force it.\n\n"
        f"Here's the pattern to notice: {diagnosis}. Doing the same thing again won't "
        "change the outcome, so let's regroup and try a genuinely different route:\n"
        "- Step back from the loop. What's the ONE thing you actually know for sure so "
        "far? Start there.\n"
        "- Either commit your honest best conclusion now, or take a *different* "
        "approach — not a repeat of what hasn't worked.\n"
        "- And this matters: if you honestly can't determine the answer from what you "
        "have, saying \"I don't know\" plainly is a real, good answer — better than "
        "forcing one or making something up. Never fabricate an answer just to escape "
        "this loop.\n\n"
        "Take a breath. What's your next — *different* — step, or your honest final "
        "answer?"
    )
