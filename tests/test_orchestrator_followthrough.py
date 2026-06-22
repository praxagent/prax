"""Default-on follow-through enforcement (AUTONOMY_FOLLOWTHROUGH_ENABLED):

- the 'produced an artifact, then only OFFERED to use it' continuation, and
- the plan-housekeeping-ack-is-never-the-reply guard regex.

Generalized from the transcript where Prax saved a screenshot then said "I can
take the next step and inspect it" instead of inspecting it, and where
"download it" surfaced "Done — the plan is cleared." as the answer.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from prax.agent.orchestrator import _PLAN_ACK_RE, ConversationAgent


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "autonomy_followthrough_enabled", True, raising=False)


def _msgs(ai_text, tool_text=None, tool_name="sandbox_browser_read"):
    out = []
    if tool_text is not None:
        out.append(ToolMessage(content=tool_text, name=tool_name, tool_call_id="t1"))
    out.append(AIMessage(content=ai_text))
    return out


# --------------------------------------------------------------------------- #
# offer-continuation detection
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("msgs_args, query, expected", [
    # fires: an artifact was produced AND the AI only offered to use it
    pytest.param(
        (
            "I saved a browser screenshot locally. I can take the next step and inspect it.",
            "Screenshot saved to /tmp/cdp_screenshot_1.jpg. Call analyze_image on it.",
        ),
        "describe the image",
        True,
        id="fires_on_produced_artifact_plus_offer",
    ),
    # skips: offer present but nothing was produced — could be a genuine optional
    # offer; don't force it (avoid acting on unwanted extras).
    pytest.param(
        ("If you want, I can also schedule this weekly.",),
        "do x",
        False,
        id="skips_when_no_artifact_produced",
    ),
    # skips: artifact produced but no offer to continue
    pytest.param(
        ("Here's what's on the page: a cat on a mat.", "Screenshot saved to /tmp/x.jpg"),
        "describe",
        False,
        id="skips_when_no_offer",
    ),
])
def test_should_continue_after_offer(msgs_args, query, expected):
    msgs = _msgs(*msgs_args)
    assert ConversationAgent._should_continue_after_offer(query, msgs) is expected


def test_off_when_flag_disabled(monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "autonomy_followthrough_enabled", False, raising=False)
    msgs = _msgs("I can take the next step and inspect it.", "Screenshot saved to /tmp/x.jpg")
    assert ConversationAgent._should_continue_after_offer("describe", msgs) is False


# --------------------------------------------------------------------------- #
# plan-ack guard regex — must catch housekeeping, never a real answer
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", [
    "Done — the plan is cleared.",
    "Plan cleared.",
    "the plan is now cleared",
    "Done. Plan cleared",
    "  Plan is cleared.  ",
])
def test_plan_ack_regex_matches_housekeeping(text):
    assert _PLAN_ACK_RE.match(text.strip())


@pytest.mark.parametrize("text", [
    "It's showing a single image file: IMG_2826.jpg.",
    "Done — here's the summary of the article.",
    "I cleared my calendar for Tuesday.",          # 'cleared' but not a plan ack
    "The plan is to download the file and inspect it.",
])
def test_plan_ack_regex_ignores_real_answers(text):
    assert not _PLAN_ACK_RE.match(text.strip())
