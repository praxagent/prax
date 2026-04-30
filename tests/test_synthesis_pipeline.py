"""Tests for the reusable SynthesisPipeline."""
from __future__ import annotations

from unittest.mock import MagicMock

from prax.agent.pipelines import PipelineResult, SynthesisPipeline


def _make_pipeline(**overrides) -> SynthesisPipeline:
    """Build a pipeline with sensible defaults and stubbed phases."""
    defaults = dict(
        researcher=MagicMock(return_value="Research findings"),
        writer=MagicMock(return_value="# Draft\n\n## Section\n\nThe key insight is content."),
        publisher=MagicMock(return_value={"slug": "test-note", "url": "http://example.com/notes/test-note/"}),
        reviewer=MagicMock(return_value="APPROVED\n\nLooks good."),
        max_revisions=3,
        item_kind="Note",
    )
    defaults.update(overrides)
    return SynthesisPipeline(**defaults)


class TestApprovalDetection:
    def test_approved_uppercase(self):
        assert SynthesisPipeline._is_approved("APPROVED\n\nGreat.")

    def test_approved_mixed_case(self):
        assert SynthesisPipeline._is_approved("Approved — looks good")

    def test_approved_with_bold(self):
        assert SynthesisPipeline._is_approved("**APPROVED**\n\nFine.")

    def test_approved_with_underscore(self):
        assert SynthesisPipeline._is_approved("__APPROVED__\n\nOK.")

    def test_revise_not_approved(self):
        assert not SynthesisPipeline._is_approved("REVISE\n\nNeeds work.")

    def test_inline_approved_not_enough(self):
        """The word 'approved' in the middle of text doesn't count."""
        assert not SynthesisPipeline._is_approved("The doc is approved in part...")


class TestTitleDerivation:
    def test_simple_title(self):
        assert SynthesisPipeline._derive_title("Test Title") == "Test Title"

    def test_first_line_only(self):
        assert SynthesisPipeline._derive_title("Title\nmore text") == "Title"

    def test_strips_whitespace(self):
        assert SynthesisPipeline._derive_title("  \n  Title  \n\nbody") == "Title"

    def test_truncates_at_120_chars(self):
        long = "x" * 200
        assert len(SynthesisPipeline._derive_title(long)) == 120

    def test_empty_fallback(self):
        assert SynthesisPipeline._derive_title("") == "Untitled"


class TestHappyPath:
    def test_approved_first_pass(self):
        pipeline = _make_pipeline()
        result = pipeline.run("Test Topic")
        assert result.status == "approved"
        assert result.passes == 1
        assert result.url == "http://example.com/notes/test-note/"
        assert result.slug == "test-note"

    def test_calls_phases_in_order(self):
        pipeline = _make_pipeline()
        result = pipeline.run("Topic", notes="notes", tags=["tag1"])
        assert result.status == "approved"

        # Verify call order: researcher → writer → publisher → reviewer
        pipeline.researcher.assert_called_once_with("Topic", "notes")
        pipeline.writer.assert_called_once_with("Topic", "Research findings", None, None)
        pipeline.publisher.assert_called_once()
        pipeline.reviewer.assert_called_once()

    def test_publisher_receives_tags_on_first_pub(self):
        pipeline = _make_pipeline()
        pipeline.run("Topic", tags=["a", "b"])
        # First publisher call: (title, content, tags, None)
        first_call = pipeline.publisher.call_args_list[0]
        assert first_call[0][2] == ["a", "b"]
        assert first_call[0][3] is None  # no slug → new publication


class TestRevisionLoop:
    def test_revise_then_approve(self):
        writer = MagicMock(side_effect=["# Draft 1", "# Draft 2 (revised)"])
        reviewer = MagicMock(side_effect=[
            "REVISE\n\nNeeds more detail.",
            "APPROVED\n\nMuch better.",
        ])
        pipeline = _make_pipeline(writer=writer, reviewer=reviewer)
        result = pipeline.run("Topic")
        assert result.status == "approved"
        assert result.passes == 2
        assert writer.call_count == 2
        assert reviewer.call_count == 2
        # Second writer call should include feedback and previous draft
        second = writer.call_args_list[1]
        assert second[0][2] is not None  # feedback
        assert second[0][3] == "# Draft 1"  # previous draft

    def test_exhausted_revisions(self):
        reviewer = MagicMock(return_value="REVISE\n\nStill not good.")
        pipeline = _make_pipeline(reviewer=reviewer, max_revisions=3)
        result = pipeline.run("Topic")
        assert result.status == "exhausted"
        assert result.passes == 3
        assert reviewer.call_count == 3

    def test_publish_updates_existing_slug_on_revision(self):
        reviewer = MagicMock(side_effect=[
            "REVISE\n\nFix this.",
            "APPROVED\n\nOK.",
        ])
        pipeline = _make_pipeline(reviewer=reviewer)
        pipeline.run("Topic")
        # Publisher should be called twice: first with slug=None, then slug="test-note"
        assert pipeline.publisher.call_count == 2
        first = pipeline.publisher.call_args_list[0]
        second = pipeline.publisher.call_args_list[1]
        assert first[0][3] is None
        assert second[0][3] == "test-note"


class TestFailureHandling:
    def test_research_failure(self):
        researcher = MagicMock(return_value="Research failed: API down")
        pipeline = _make_pipeline(researcher=researcher)
        result = pipeline.run("Topic")
        assert result.status == "research_failed"
        # Writer should NOT have been called
        pipeline.writer.assert_not_called()

    def test_write_failure(self):
        writer = MagicMock(return_value="Error: unable to write")
        pipeline = _make_pipeline(writer=writer)
        result = pipeline.run("Topic")
        assert result.status == "write_failed"
        pipeline.publisher.assert_not_called()

    def test_publish_failure(self):
        publisher = MagicMock(return_value={"error": "Hugo build failed"})
        pipeline = _make_pipeline(publisher=publisher)
        result = pipeline.run("Topic")
        assert result.status == "publish_failed"
        assert "Hugo build failed" in result.error
        pipeline.reviewer.assert_not_called()


class TestSkipResearch:
    def test_uses_pre_fetched_research(self):
        researcher = MagicMock()
        pipeline = _make_pipeline(
            researcher=researcher,
            skip_research=True,
            pre_fetched_research="Pre-fetched article content",
        )
        pipeline.run("Topic")
        # Researcher should NOT be called
        researcher.assert_not_called()
        # Writer should get the pre-fetched content
        pipeline.writer.assert_called_once()
        call = pipeline.writer.call_args
        assert call[0][1] == "Pre-fetched article content"


class TestStatusCallback:
    def test_status_callback_called(self):
        status = MagicMock()
        pipeline = _make_pipeline(status_callback=status)
        pipeline.run("Topic")
        # Should have been called at least 4 times: start, research, write, publish, review
        assert status.call_count >= 4

    def test_status_callback_failure_ignored(self):
        status = MagicMock(side_effect=RuntimeError("boom"))
        pipeline = _make_pipeline(status_callback=status)
        # Should not raise
        result = pipeline.run("Topic")
        assert result.status == "approved"

    def test_status_updates_touch_trace_heartbeat(self, monkeypatch):
        touches = []

        def fake_touch(source, message):
            touches.append((source, message))

        monkeypatch.setattr("prax.agent.trace.touch_current_trace", fake_touch)
        pipeline = _make_pipeline()

        pipeline.run("Topic")

        assert touches
        assert any("Starting note pipeline" in message for _source, message in touches)
        assert any("Writing first draft" in message for _source, message in touches)


class TestPipelineResultSummary:
    def test_approved_summary(self):
        r = PipelineResult(
            status="approved",
            title="Test",
            slug="test",
            url="http://example.com/test/",
            passes=1,
            last_review="APPROVED — great work",
        )
        s = r.summary(item_kind="Note")
        assert "approved" in s.lower()
        assert "Test" in s
        assert "http://example.com/test/" in s

    def test_exhausted_summary(self):
        r = PipelineResult(
            status="exhausted",
            title="Test",
            slug="test",
            url="http://example.com/test/",
            passes=3,
            last_review="Still needs work",
        )
        s = r.summary(item_kind="Note")
        assert "3 revision" in s
        assert "did not fully approve" in s

    def test_research_failed_summary(self):
        r = PipelineResult(status="research_failed", title="Test", error="timeout")
        s = r.summary(item_kind="Note")
        assert "Research phase failed" in s
        assert "timeout" in s
