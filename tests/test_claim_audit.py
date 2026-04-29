"""Tests for prax.agent.claim_audit — deterministic claim grounding checks."""
from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from prax.agent.claim_audit import (
    audit_claims,
    audit_narrative_grounding,
    audit_plan_completion,
    audit_scheduled_task_grounding,
    format_audit_warning,
)

# ---------------------------------------------------------------------------
# Basic claim detection
# ---------------------------------------------------------------------------

class TestClaimDetection:
    def test_detects_dollar_amount(self):
        tool_results = ["Some tool returned general context."]
        findings = audit_claims("The cheapest is $83 one-way.", tool_results)
        assert len(findings) == 1
        assert findings[0]["claim"] == "$83"
        assert findings[0]["grounded"] is False

    def test_detects_dollar_with_cents(self):
        tool_results = ["Some tool returned general context."]
        findings = audit_claims("It costs $1,234.56.", tool_results)
        claims = [f["claim"] for f in findings]
        assert "$1,234.56" in claims

    def test_detects_euro_and_pound(self):
        tool_results = ["Some tool returned general context."]
        findings = audit_claims("€50 in Paris, £100 in London.", tool_results)
        claims = [f["claim"] for f in findings]
        assert "€50" in claims
        assert "£100" in claims

    def test_detects_percentage(self):
        tool_results = ["Some tool returned general context."]
        findings = audit_claims("Performance improved by 42.5%.", tool_results)
        claims = [f["claim"] for f in findings]
        assert "42.5%" in claims

    def test_detects_ranking(self):
        tool_results = ["Some tool returned general context."]
        findings = audit_claims("It's ranked #3 globally.", tool_results)
        claims = [f["claim"] for f in findings]
        assert any("#3" in c for c in claims)

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
