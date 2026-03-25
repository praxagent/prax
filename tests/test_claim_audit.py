"""Tests for prax.agent.claim_audit — deterministic claim grounding checks."""
from __future__ import annotations

from prax.agent.claim_audit import audit_claims, format_audit_warning

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
