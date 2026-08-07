"""Three accounting/audit gaps found in one live Discord trace (2026-08-06).

The trace: a spoke crashed and the reply claimed clean success; two follow-up
turns answered "link please" with a bare filename and the auditor said "No
claims flagged"; and the orchestrator span reported 159 in / 24 out across a
12-tool-call turn with every ``cost_estimate_usd`` null.

Each is a distinct defect:

1. ``on_llm_end`` never read ``AIMessage.usage_metadata`` — the modern,
   provider-normalised shape — so real usage was recorded as zero.
2. ``_guard_types`` omitted three checks, so their firings never reached the
   metric or the attended-quarantine notice.
3. No check covered "promised a link, delivered a filename".
"""

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from prax.agent.claim_audit import audit_undelivered_artifact
from prax.observability.callbacks import _usage_from_message


def _result(*, usage_metadata=None, llm_output=None, generation_info=None):
    msg = AIMessage(content="hi", usage_metadata=usage_metadata) if usage_metadata \
        else AIMessage(content="hi")
    gen = ChatGeneration(message=msg, generation_info=generation_info)
    return LLMResult(generations=[[gen]], llm_output=llm_output)


class TestUsageMetadataIsRead:
    """The bug: `llm_output` is commonly a truthy {"model_name": ...} with no
    `token_usage`, so the old first branch matched, yielded {}, and the elif
    never ran — usage silently zero while the call still counted."""

    def test_the_exact_live_shape_is_no_longer_zero(self):
        r = _result(usage_metadata={"input_tokens": 1200, "output_tokens": 40,
                                    "total_tokens": 1240},
                    llm_output={"model_name": "gpt-5.4-nano"})
        assert _usage_from_message(r) == (1200, 40)

    def test_legacy_token_usage_still_works(self):
        """Providers that only send the old shape must not regress."""
        r = _result(llm_output={"token_usage": {"prompt_tokens": 10,
                                                "completion_tokens": 3},
                                "model_name": "m"})
        assert _usage_from_message(r) == (0, 0)   # nothing on the message...
        handler = _handler()
        with patch("prax.observability.callbacks._record_metrics") as rec:
            handler.on_llm_end(r, run_id=_uuid())
        assert rec.call_args.args[1:3] == (10, 3)  # ...and the fallback caught it

    def test_end_to_end_modern_shape_reaches_metrics_and_trace(self):
        r = _result(usage_metadata={"input_tokens": 900, "output_tokens": 55,
                                    "total_tokens": 955},
                    llm_output={"model_name": "gpt-5.4-nano"})
        handler = _handler()
        graph = MagicMock()
        ctx = MagicMock(graph=graph, span_id="s1")
        with patch("prax.observability.callbacks._record_metrics") as rec, \
             patch("prax.agent.trace.get_current_trace", return_value=ctx):
            handler.on_llm_end(r, run_id=_uuid())
        assert rec.call_args.args[1:3] == (900, 55)
        graph.add_llm_usage.assert_called_once()
        assert graph.add_llm_usage.call_args.args[2:4] == (900, 55)

    def test_no_usage_anywhere_degrades_to_zero_not_a_crash(self):
        handler = _handler()
        with patch("prax.observability.callbacks._record_metrics") as rec:
            handler.on_llm_end(_result(llm_output={"model_name": "m"}), run_id=_uuid())
        assert rec.call_args.args[1:3] == (0, 0)

    def test_a_null_token_usage_still_does_not_abort_the_callback(self):
        """The earlier regression this file also guards: an explicit
        `"token_usage": null` must not raise past the metrics call."""
        handler = _handler()
        with patch("prax.observability.callbacks._record_metrics") as rec:
            handler.on_llm_end(_result(llm_output={"token_usage": None}),
                               run_id=_uuid())
        assert rec.called


def _handler():
    from prax.observability.callbacks import OTelLLMCallback

    return OTelLLMCallback()


def _uuid():
    import uuid

    return uuid.uuid4()


class TestUndeliveredArtifact:
    """The gap between two existing checks: audit_fabricated_links only fires
    when a URL is PRESENT; audit_artifact_location passed because the locator
    WAS called. "Here's the link: `foo.mp3`" fell straight through."""

    def test_the_exact_live_reply_is_flagged(self):
        out = audit_undelivered_artifact(
            "Here's the link: `polydao_narration.mp3`\n\n"
            "And the script: `narration_script.txt`", [])
        assert out is not None
        assert out["delivered"] is False

    def test_a_real_url_is_not_flagged(self):
        assert audit_undelivered_artifact(
            "Here's the link: https://example.com/a.mp3", []) is None

    def test_a_successful_send_is_not_flagged(self):
        msgs = [ToolMessage(content="Sent narration.mp3 to the user via Discord (1.1 MB).",
                            name="workspace_send_file", tool_call_id="1")]
        assert audit_undelivered_artifact("Here's the link: `narration.mp3`", msgs) is None

    def test_a_FAILED_send_is_still_flagged(self):
        """The nastiest shape: the tool ran, said 'not found', and the reply
        promised a link anyway."""
        msgs = [ToolMessage(content="File narration.mp3 not found in workspace "
                                    "(checked active/ and workspace root).",
                            name="workspace_send_file", tool_call_id="1")]
        out = audit_undelivered_artifact("Here's the link: `narration.mp3`", msgs)
        assert out is not None

    def test_merely_naming_a_path_is_not_a_promise(self):
        """Conservative by design — no flag without an explicit promise."""
        assert audit_undelivered_artifact(
            "I saved it to active/report.pdf if you want to look.", []) is None

    def test_empty_response_is_not_flagged(self):
        assert audit_undelivered_artifact("", []) is None

    def test_attachment_phrasing_is_covered(self):
        assert audit_undelivered_artifact("I've attached the PDF.", []) is not None


class TestGuardTypesCoverEveryCheck:
    def test_every_flag_label_has_a_user_facing_phrase(self):
        """The attended-quarantine notice looks up each guard type; a missing
        entry silently degrades to the generic fallback."""
        import re
        from pathlib import Path

        src = Path("prax/agent/orchestrator.py").read_text(encoding="utf-8")
        appended = set(re.findall(r'_guard_types\.append\("([a-z_]+)"\)', src))
        labelled = set(re.findall(r'^\s+"([a-z_]+)": "', src, re.MULTILINE))
        assert appended, "guard types not found — did the block move?"
        missing = appended - labelled
        assert not missing, f"guard types with no user-facing phrase: {missing}"

    def test_the_three_previously_missing_checks_are_present(self):
        import re
        from pathlib import Path

        src = Path("prax/agent/orchestrator.py").read_text(encoding="utf-8")
        appended = set(re.findall(r'_guard_types\.append\("([a-z_]+)"\)', src))
        assert {"tool_failure", "fabricated_link", "lethal_trifecta"} <= appended
