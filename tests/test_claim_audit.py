"""Tests for prax.agent.claim_audit — deterministic claim grounding checks."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from prax.agent.claim_audit import (
    audit_artifact_location,
    audit_claims,
    audit_narrative_grounding,
    audit_plan_completion,
    audit_scheduled_task_grounding,
    decide_scheduled_briefing_action,
    format_audit_warning,
)

# ---------------------------------------------------------------------------
# Basic claim detection
# ---------------------------------------------------------------------------

class TestClaimDetection:
    @pytest.mark.parametrize(
        ("text", "expected_claims", "expect_single_ungrounded"),
        [
            # test_detects_dollar_amount: also asserts a single ungrounded finding.
            ("The cheapest is $83 one-way.", ["$83"], True),
            # test_detects_dollar_with_cents
            ("It costs $1,234.56.", ["$1,234.56"], False),
            # test_detects_euro_and_pound: two distinct claims must appear.
            ("€50 in Paris, £100 in London.", ["€50", "£100"], False),
            # test_detects_percentage
            ("Performance improved by 42.5%.", ["42.5%"], False),
            # test_detects_ranking: substring match (claim text may carry context).
            ("It's ranked #3 globally.", ["#3"], False),
        ],
    )
    def test_detects_claim(self, text, expected_claims, expect_single_ungrounded):
        tool_results = ["Some tool returned general context."]
        findings = audit_claims(text, tool_results)
        claims = [f["claim"] for f in findings]
        for expected in expected_claims:
            assert any(expected in c for c in claims)
        if expect_single_ungrounded:
            assert len(findings) == 1
            assert findings[0]["claim"] == expected_claims[0]
            assert findings[0]["grounded"] is False

    def test_no_claims_returns_empty(self):
        findings = audit_claims("Hello, how can I help you?", ["tool result"])
        assert findings == []

    def test_empty_response_returns_empty(self):
        assert audit_claims("", ["some tool result"]) == []

    def test_no_tool_results_skips_audit(self):
        # No tool results means pure conversation — no audit needed.
        assert audit_claims("The price is $50.", []) == []


# ---------------------------------------------------------------------------
# Grounding checks
# ---------------------------------------------------------------------------

class TestGroundingVerification:
    def test_grounded_in_verified_source(self):
        response = "The flight costs $283."
        tool_result = (
            "[VERIFIED SOURCE — structured data from a purpose-built API. "
            "Values can be cited directly.]\n\n"
            "USD $283 — JFK to CDG"
        )
        findings = audit_claims(response, [tool_result])
        # $283 is grounded in a VERIFIED source — no findings.
        assert findings == []

    def test_ungrounded_claim(self):
        response = "The cheapest flight is $83 one-way."
        tool_result = (
            "[INFORMATIONAL SOURCE — general web content.]\n\n"
            "Search results about flights from PHL to SNA..."
        )
        findings = audit_claims(response, [tool_result])
        assert len(findings) == 1
        assert findings[0]["claim"] == "$83"
        assert findings[0]["grounded"] is False

    def test_informational_sourced_claim(self):
        """Value exists in tool result but only in an INFORMATIONAL source."""
        response = "The cheapest flight is $83."
        tool_result = (
            "[INFORMATIONAL SOURCE — general web content, not structured data. "
            "Do NOT state specific numbers.]\n\n"
            "Some search snippet mentioning $83 fare..."
        )
        findings = audit_claims(response, [tool_result])
        assert len(findings) == 1
        assert findings[0]["claim"] == "$83"
        assert findings[0]["grounded"] is True
        assert findings[0]["informational_only"] is True

    def test_grounded_in_non_informational_clears(self):
        """Value in a non-INFORMATIONAL source should not be flagged."""
        response = "The fare is $283."
        tool_results = [
            "[VERIFIED SOURCE — structured data.]\n\nFare: $283",
            "[INFORMATIONAL SOURCE — web.]\n\nSome search about $283",
        ]
        findings = audit_claims(response, tool_results)
        # Present in VERIFIED source, so informational_only is False.
        assert findings == []

    def test_multiple_claims_mixed(self):
        response = "Flight: $283. Hotel: $150/night. 95% on-time."
        tool_results = [
            "[VERIFIED SOURCE]\n\nFare: $283",
        ]
        findings = audit_claims(response, tool_results)
        # $283 grounded in VERIFIED — fine.
        # $150 not grounded anywhere.
        # 95% not grounded anywhere.
        ungrounded = [f["claim"] for f in findings if not f["grounded"]]
        assert "$150" in ungrounded
        assert "95%" in ungrounded
        assert "$283" not in ungrounded

    def test_deduplicates_claims(self):
        response = "The price is $83. I repeat: $83."
        findings = audit_claims(response, ["Some tool output without that number."])
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# Warning formatting
# ---------------------------------------------------------------------------

class TestFormatAuditWarning:
    def test_empty_findings(self):
        assert format_audit_warning([]) == ""

    def test_ungrounded_warning(self):
        findings = [{"claim": "$83", "grounded": False, "informational_only": False}]
        result = format_audit_warning(findings)
        assert "UNGROUNDED" in result
        assert "$83" in result

    def test_informational_sourced_warning(self):
        findings = [{"claim": "$83", "grounded": True, "informational_only": True}]
        result = format_audit_warning(findings)
        assert "INFORMATIONAL-SOURCED" in result
        assert "$83" in result

    def test_mixed_warnings(self):
        findings = [
            {"claim": "$83", "grounded": False, "informational_only": False},
            {"claim": "42%", "grounded": True, "informational_only": True},
        ]
        result = format_audit_warning(findings)
        assert "UNGROUNDED" in result
        assert "INFORMATIONAL-SOURCED" in result


# ---------------------------------------------------------------------------
# Narrative grounding audit — the daily-briefing hallucination check
# ---------------------------------------------------------------------------

class TestNarrativeGrounding:
    def _msgs(self, tool_names: list[str]) -> list:
        """Helper: build a fake message list with ToolMessages for each name."""
        return [
            AIMessage(content=""),
            *[ToolMessage(content="result", name=n, tool_call_id=f"c{i}")
              for i, n in enumerate(tool_names)],
        ]

    def test_flags_fake_briefing_with_no_research_tools(self):
        """The exact failure case: top news items without any research call."""
        response = (
            "Good morning — today's quick briefing:\n"
            "- Top news item: supply-chain attacks remain the big cautionary tale.\n"
            "- Top news item: agent systems are trending toward stronger orchestration.\n"
            "- Weather: it's a sunny 72 degrees in LA today."
        )
        messages = self._msgs(["user_notes_read", "get_current_datetime"])
        finding = audit_narrative_grounding(response, messages)
        assert finding is not None
        assert finding["missing_grounding"] is True
        assert any("news" in p.lower() for p in finding["phrases"])

    def test_accepts_briefing_when_news_plugin_called(self):
        response = "Today's top news item: ..."
        messages = self._msgs(["news", "get_current_datetime"])
        assert audit_narrative_grounding(response, messages) is None

    def test_accepts_briefing_when_research_spoke_called(self):
        response = "Here are the headlines for today..."
        messages = self._msgs(["delegate_research"])
        assert audit_narrative_grounding(response, messages) is None

    def test_accepts_briefing_when_browser_spoke_called(self):
        response = "Morning briefing: key highlights..."
        messages = self._msgs(["delegate_browser"])
        assert audit_narrative_grounding(response, messages) is None

    def test_passes_plain_conversational_response(self):
        response = "Sure, I can help with that. What would you like me to do?"
        messages = self._msgs([])
        assert audit_narrative_grounding(response, messages) is None

    def test_passes_empty_response(self):
        assert audit_narrative_grounding("", self._msgs([])) is None

    def test_detects_reports_say_pattern(self):
        response = "Reports say the market dropped sharply this morning."
        messages = self._msgs(["user_notes_read"])
        finding = audit_narrative_grounding(response, messages)
        assert finding is not None
        assert finding["missing_grounding"] is True

    def test_detects_market_claim_pattern(self):
        response = "The S&P 500 index closed at a new high today."
        messages = self._msgs(["get_current_datetime"])
        finding = audit_narrative_grounding(response, messages)
        assert finding is not None

    def test_failed_tool_call_does_not_count_as_grounding(self):
        """A tool that raised without a ToolMessage result doesn't ground claims."""
        # Only an AIMessage with tool_calls, no resulting ToolMessage =
        # the call failed validation, so grounding evidence is absent.
        messages = [
            AIMessage(content="", tool_calls=[
                {"name": "delegate_research", "args": {}, "id": "c1"}
            ]),
        ]
        response = "Top news item: something happened today."
        finding = audit_narrative_grounding(response, messages)
        assert finding is not None
        assert finding["missing_grounding"] is True
        assert "delegate_research" not in finding["called_tools"]


class TestScheduledTaskGrounding:
    def _msgs(self, tool_names: list[str]) -> list:
        return [
            AIMessage(content=""),
            *[ToolMessage(content="result", name=n, tool_call_id=f"c{i}")
              for i, n in enumerate(tool_names)],
        ]

    def test_background_search_alone_does_not_ground_scheduled_briefing(self):
        task = (
            "[SCHEDULED_TASK] Send a concise morning briefing: "
            "3 top news items and weather/time-saving tips."
        )
        response = "Good morning — today's quick briefing: I don't have reliable top news."
        messages = self._msgs(["background_search_tool", "get_current_datetime"])
        finding = audit_scheduled_task_grounding(task, response, messages)
        assert finding is not None
        assert finding["missing_grounding"] is True
        assert any("background_search_tool alone is not sufficient" in r for r in finding["requirements"])

    def test_news_tool_satisfies_scheduled_briefing(self):
        task = "[SCHEDULED_TASK] Send a concise morning briefing with 3 top news items."
        response = "Today's top news items are grounded in the digest."
        messages = self._msgs(["news", "get_current_datetime"])
        assert audit_scheduled_task_grounding(task, response, messages) is None

    def test_weather_request_requires_tool_or_unavailable_disclosure(self):
        task = "[SCHEDULED_TASK] Send weather and time-saving tips."
        response = "Weather should be sunny today."
        messages = self._msgs(["get_current_datetime"])
        finding = audit_scheduled_task_grounding(task, response, messages)
        assert finding is not None
        assert any("weather requested" in r for r in finding["requirements"])

    def test_authoritative_weather_fetch_satisfies_weather_request(self):
        task = "[SCHEDULED_TASK] Send weather and time-saving tips."
        response = "Weather: the NWS forecast says a high near 72."
        messages = [
            AIMessage(content=""),
            ToolMessage(
                content=(
                    "Fetched forecast.weather.gov for Los Angeles. "
                    "National Weather Service forecast: high near 72 degrees, "
                    "west wind 5 mph."
                ),
                name="delegate_research",
                tool_call_id="c_weather",
            ),
        ]
        assert audit_scheduled_task_grounding(task, response, messages) is None

    def test_environment_spoke_weather_satisfies_weather_request(self):
        task = "[SCHEDULED_TASK] Send weather and time-saving tips."
        response = "Weather: verified local conditions are available."
        messages = [
            AIMessage(content=""),
            ToolMessage(
                content=(
                    "VERIFIED_WEATHER\n"
                    "location: Los Angeles, California, United States\n"
                    "conditions: mainly clear\n"
                    "temperature: 72.1 °F\n"
                    "sources: https://api.open-meteo.com/v1/forecast"
                ),
                name="delegate_environment",
                tool_call_id="c_environment",
            ),
        ]
        assert audit_scheduled_task_grounding(task, response, messages) is None

    def test_preserved_environment_evidence_satisfies_weather_request(self):
        task = "[SCHEDULED_TASK] Send weather and time-saving tips."
        response = "Clear and mild in Los Angeles this morning."
        messages = [
            AIMessage(content=""),
            ToolMessage(
                content=(
                    "Clear and mild in Los Angeles this morning.\n\n"
                    "[Tool evidence preserved for audit]\n"
                    "VERIFIED_WEATHER\n"
                    "location: Los Angeles, California, United States\n"
                    "conditions: clear sky\n"
                    "temperature: 63.8 °F\n"
                    "humidity: 60 %\n"
                    "sources: https://api.open-meteo.com/v1/forecast"
                ),
                name="delegate_environment",
                tool_call_id="c_environment",
            ),
        ]
        assert audit_scheduled_task_grounding(task, response, messages) is None

    def test_background_search_does_not_satisfy_weather_request(self):
        task = "[SCHEDULED_TASK] Send weather and time-saving tips."
        response = "Weather: sunny and 72."
        messages = [
            AIMessage(content=""),
            ToolMessage(
                content="Search snippets mention weather in Los Angeles.",
                name="background_search_tool",
                tool_call_id="c_search",
            ),
        ]
        finding = audit_scheduled_task_grounding(task, response, messages)
        assert finding is not None
        assert any("authoritative weather fetch" in r for r in finding["requirements"])

    def test_weather_unavailable_disclosure_passes_without_weather_tool(self):
        task = "[SCHEDULED_TASK] Send weather and time-saving tips."
        response = "Weather unavailable: no weather tool is configured."
        messages = self._msgs(["get_current_datetime"])
        assert audit_scheduled_task_grounding(task, response, messages) is None

    def test_weather_is_unavailable_phrasing_passes(self):
        # "Weather is unavailable" / "Weather data unavailable" / "Weather
        # not available" must count as disclosure — the literal-substring
        # check missed all of these for a week of suppressed briefings.
        task = "[SCHEDULED_TASK] Send weather and time-saving tips."
        for phrasing in [
            "Weather is unavailable today — no saved location.",
            "Weather data unavailable; ask for location.",
            "Weather: not available right now.",
            "Weather can't be fetched without a city.",
            "Couldn't fetch weather data this morning.",
        ]:
            messages = self._msgs(["get_current_datetime"])
            finding = audit_scheduled_task_grounding(task, phrasing, messages)
            assert finding is None, f"phrasing should be a disclosure: {phrasing!r}"

    def test_location_uncertain_in_environment_reply_passes(self):
        # The actual production trace: delegate_environment returns
        # LOCATION_UNCERTAIN when it can't resolve a city.  The orchestrator's
        # response talked around weather without using the exact "weather
        # unavailable" substring, but the spoke's structured signal IS the
        # legitimate disclosure and must satisfy the audit.
        task = (
            "[SCHEDULED_TASK — CRITICAL RULES: 8) For weather/local "
            "conditions, use delegate_environment.] Send a concise morning "
            "briefing: 3 top news items and weather/time-saving tips."
        )
        response = (
            "Good morning — here are today's headlines.\n"
            "1. Story A.\n2. Story B.\n3. Story C.\n"
            "Local conditions: I don't have a saved location, so I'm "
            "skipping weather today."
        )
        messages = [
            AIMessage(content=""),
            ToolMessage(
                content=(
                    "**Morning Briefing — Story A / B / C** (3 items)\n"
                    "If you want, I can also turn this into a tech brief."
                ),
                name="delegate_research",
                tool_call_id="c_research",
            ),
            ToolMessage(
                content=(
                    "Weather is unavailable right now because I don't have "
                    "a concrete city/region to use.\n\n"
                    "[Tool evidence preserved for audit]\n"
                    "LOCATION_UNCERTAIN\n"
                    "reason: No saved location or explicit location was found.\n"
                    "ask_user: What city/region should I use for local conditions?"
                ),
                name="delegate_environment",
                tool_call_id="c_env",
            ),
        ]
        assert audit_scheduled_task_grounding(task, response, messages) is None

    def test_long_scheduled_preamble_does_not_self_match(self):
        # Regression: the production [SCHEDULED_TASK — CRITICAL RULES: ...]
        # preamble itself mentions "news/headlines/briefings" and "weather"
        # inside its rules section.  A trivial task ("say hi") wrapped in
        # that preamble must NOT be flagged as a news or weather request.
        task = (
            "[SCHEDULED_TASK — CRITICAL RULES: "
            "1) Do NOT ask follow-up questions — the user is not present. "
            "2) Do NOT use schedule_create, schedule_reminder, or any scheduling tools. "
            "3) Do NOT ask for confirmation or clarification. "
            "4) Just execute the task using your best judgment and respond with the result. "
            "5) If the task is ambiguous, take the most reasonable interpretation and do it. "
            "6) Keep your response concise — it will be delivered as a notification. "
            "7) For news/headlines/briefings, use delegate_research to run the news tool; "
            "background_search_tool snippets are not sufficient. "
            "8) For weather/local conditions, use delegate_environment. It must resolve "
            "a concrete city/region first; timezone alone is not enough. If no location "
            "or live source can be confirmed, ask for location or say weather is "
            "unavailable rather than inventing a forecast.] say hi"
        )
        response = "Hi!"
        messages = self._msgs(["agent_plan"])
        assert audit_scheduled_task_grounding(task, response, messages) is None

    def test_long_scheduled_preamble_still_flags_real_news_request(self):
        # Make sure stripping the preamble doesn't disable detection: when
        # the actual user task IS a news request, the audit must still fire.
        task = (
            "[SCHEDULED_TASK — CRITICAL RULES: "
            "7) For news/headlines/briefings, use delegate_research. "
            "8) For weather/local conditions, use delegate_environment.] "
            "Send today's top 3 headlines."
        )
        response = "Here are today's top headlines: ..."
        messages = self._msgs(["background_search_tool"])
        finding = audit_scheduled_task_grounding(task, response, messages)
        assert finding is not None
        assert any("background_search_tool alone is not sufficient" in r
                   for r in finding["requirements"])


# ---------------------------------------------------------------------------
# Artifact-location audit
# ---------------------------------------------------------------------------

class TestArtifactLocationAudit:
    def _msg(self, name: str, content: str) -> ToolMessage:
        return ToolMessage(content=content, name=name, tool_call_id=f"c_{name}")

    def test_flags_where_is_it_without_artifact_locator(self):
        task = "Where is it?"
        response = (
            "I found likely files in the workspace: "
            "`2510.02453.md`, `three_marks_presentation.tex`."
        )
        messages = [
            self._msg(
                "delegate_workspace",
                "I searched workspace_list and found several likely files.",
            ),
        ]

        finding = audit_artifact_location(task, response, messages)

        assert finding is not None
        assert finding["missing_grounding"] is True
        assert finding["called_tools"] == ["delegate_workspace"]

    def test_passes_when_artifact_locator_was_called_directly(self):
        task = "Where is it?"
        response = "It is here: http://localhost:8000/notes/x/"
        messages = [
            self._msg(
                "artifact_locator",
                "Most likely recent artifact locations:\n"
                "1. note URL: http://localhost:8000/notes/x/",
            ),
        ]

        assert audit_artifact_location(task, response, messages) is None

    def test_passes_when_workspace_delegate_returns_locator_output(self):
        task = "Link again please"
        response = "It is here: http://localhost:8000/notes/x/"
        messages = [
            self._msg(
                "delegate_workspace",
                "Most likely recent artifact locations:\n"
                "1. note URL: http://localhost:8000/notes/x/",
            ),
        ]

        assert audit_artifact_location(task, response, messages) is None


# ---------------------------------------------------------------------------
# Plan-completion alignment audit
# ---------------------------------------------------------------------------

class TestPlanCompletionAlignment:
    """The ROP note regression: orchestrator said "Done" while the knowledge
    spoke's reply explicitly said "it does not guarantee the synthesized
    summary/diagram format you asked for. If you want, I can..." — the old
    auditor missed it because there were no numeric or narrative claims to
    flag. ``audit_plan_completion`` is the new check.
    """

    def _delegation_msg(self, name: str, content: str) -> ToolMessage:
        return ToolMessage(content=content, name=name, tool_call_id=f"c_{name}")

    def test_flags_done_lie_when_delegation_caveats(self):
        caveat_reply = (
            "Saved and readable.\n\nNote URL: https://example/notes/x\n\n"
            "One caveat: I archived the page as a note from the URL, which "
            "preserves fetched page content, but it does not guarantee the "
            "synthesized summary/diagram format you asked for. If you want, "
            "I can now turn that fetched content into a proper explainer "
            "note with a concise summary and a diagram."
        )
        messages = [self._delegation_msg("delegate_knowledge", caveat_reply)]
        response = "Done — I created the note here: https://example/notes/x"
        finding = audit_plan_completion(response, messages)
        assert finding is not None
        assert finding["missing_grounding"] is True
        assert "done" in finding["completion_claim"].lower()
        assert finding["caveat_tool"] == "delegate_knowledge"
        # The marker matched should be a caveat marker from the reply.
        assert finding["caveat_marker"] in {
            "one caveat", "does not guarantee", "if you want, i can",
        }

    def test_flags_here_is_the_note_with_caveat(self):
        caveat_reply = (
            "Wrote and published it, however, I was unable to include the "
            "mermaid diagram you asked for — the source material didn't "
            "have enough structural information."
        )
        messages = [self._delegation_msg("delegate_knowledge", caveat_reply)]
        response = "Here's the note: https://example/notes/y"
        finding = audit_plan_completion(response, messages)
        assert finding is not None
        assert "here" in finding["completion_claim"].lower()

    def test_passes_clean_done_without_caveats(self):
        clean_reply = (
            "Saved and readable.\nNote URL: https://example/notes/x\n"
            "The note includes a full deep-dive summary and a mermaid diagram."
        )
        messages = [self._delegation_msg("delegate_knowledge", clean_reply)]
        response = "Done — I created the note here: https://example/notes/x"
        assert audit_plan_completion(response, messages) is None

    def test_passes_when_no_completion_language(self):
        caveat_reply = "Something partial happened, if you want, I can retry."
        messages = [self._delegation_msg("delegate_knowledge", caveat_reply)]
        response = "Working on it — still gathering context."
        assert audit_plan_completion(response, messages) is None

    def test_ignores_non_delegation_tool_caveats(self):
        # A caveat inside a non-delegation tool result (e.g., a sandbox
        # listing) shouldn't trip the check — only sub-agent replies do,
        # because the failure mode is specifically delegation hiding work.
        caveat_reply = "could not find file x.txt"
        messages = [
            ToolMessage(content=caveat_reply, name="sandbox_ls",
                        tool_call_id="c_sandbox"),
        ]
        response = "Done — the files are listed."
        assert audit_plan_completion(response, messages) is None

    def test_passes_empty_response(self):
        assert audit_plan_completion("", []) is None

    def test_extension_offer_alone_is_not_a_caveat(self):
        # The 7-day briefing regression: delegate_research returned a clean
        # successful briefing that ended with "If you want, I can also turn
        # this into a tech-focused brief".  That's an extension offer on top
        # of completed work, not a partial-work caveat — flagging "Done" was
        # a false positive that contributed to suppressed briefings.
        clean_with_offer = (
            "**Morning Briefing — May 1, 2026**\n"
            "1. Story one — source A.\n"
            "2. Story two — source B.\n"
            "3. Story three — source C.\n"
            "If you want, I can also turn this into an even tighter "
            "bullet-style executive brief or a tech-focused brief."
        )
        messages = [
            ToolMessage(
                content=clean_with_offer,
                name="delegate_research",
                tool_call_id="c_r",
            ),
        ]
        response = "Done — here's your briefing."
        assert audit_plan_completion(response, messages) is None

    def test_offer_plus_strong_caveat_still_flags(self):
        # Belt-and-suspenders: when an offer is paired with a strong
        # partial-work signal, the audit must still fire.
        caveat_reply = (
            "I tried, but I was unable to fetch the article.\n"
            "If you want, I can retry with a different reader."
        )
        messages = [
            ToolMessage(
                content=caveat_reply,
                name="delegate_research",
                tool_call_id="c_r",
            ),
        ]
        response = "Done — here's the summary."
        finding = audit_plan_completion(response, messages)
        assert finding is not None
        assert finding["caveat_marker"] == "unable to"


# ---------------------------------------------------------------------------
# Scheduled-briefing action policy
# ---------------------------------------------------------------------------

class TestScheduledBriefingAction:
    def test_pass_when_nothing_flagged(self):
        assert decide_scheduled_briefing_action(None, None) == "pass"

    def test_suppress_on_narrative_flag(self):
        narrative = {"phrases": ["this morning's headlines"], "called_tools": []}
        assert decide_scheduled_briefing_action(narrative, None) == "suppress"

    def test_suppress_when_news_missing(self):
        # News-floor failures mean the briefing's spine is unverified.
        sg = {
            "missing_grounding": True,
            "called_tools": ["background_search_tool"],
            "successful_tools": ["background_search_tool"],
            "requirements": [
                "news/briefing requested but no successful news, research, "
                "browser, summary, or URL-fetch tool result was available; "
                "background_search_tool alone is not sufficient",
            ],
        }
        assert decide_scheduled_briefing_action(None, sg) == "suppress"

    def test_weather_disclaimer_when_only_weather_missing(self):
        # The exact failure mode from the 2026-05-10 trace: news fetched,
        # weather skipped — verified content shouldn't be discarded.
        sg = {
            "missing_grounding": True,
            "called_tools": ["delegate_research"],
            "successful_tools": ["delegate_research"],
            "requirements": [
                "weather requested but no successful weather tool, "
                "authoritative weather fetch, or explicit weather-unavailable "
                "disclosure was present",
            ],
        }
        assert (
            decide_scheduled_briefing_action(None, sg) == "weather_disclaimer"
        )

    def test_suppress_when_both_news_and_weather_missing(self):
        sg = {
            "requirements": [
                "news/briefing requested but no successful news ...",
                "weather requested but no successful weather tool ...",
            ],
        }
        assert decide_scheduled_briefing_action(None, sg) == "suppress"

    def test_narrative_wins_over_weather_only(self):
        # Even if the grounding flag is weather-only, ungrounded narrative
        # claims still warrant suppression.
        narrative = {"phrases": ["this morning's headlines"], "called_tools": []}
        sg = {"requirements": ["weather requested but no successful weather tool"]}
        assert decide_scheduled_briefing_action(narrative, sg) == "suppress"

    def test_empty_requirements_treated_as_pass(self):
        # Defensive: a flagged dict with no requirements shouldn't suppress.
        assert decide_scheduled_briefing_action(None, {"requirements": []}) == "pass"
