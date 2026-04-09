"""Tests for the content editor spoke agent."""
from unittest.mock import MagicMock, patch


def test_build_spoke_tools_returns_delegate():
    """The content spoke exports delegation + office tools."""
    from prax.agent.spokes.content import build_spoke_tools

    tools = build_spoke_tools()
    names = {t.name for t in tools}
    assert "delegate_content_editor" in names
    assert "create_presentation" in names
    assert "create_spreadsheet" in names
    assert "create_pdf" in names
    assert len(tools) == 4


def test_content_spoke_registered():
    """delegate_content_editor is in the orchestrator's tool set."""
    from prax.agent.tools import build_default_tools

    names = {t.name for t in build_default_tools()}
    assert "delegate_content_editor" in names


def test_is_approved_positive():
    from prax.agent.spokes.content.agent import _is_approved

    assert _is_approved("APPROVED\n\nGreat article!") is True
    assert _is_approved("**APPROVED**\nMinor nits only.") is True
    assert _is_approved("  APPROVED — looks good") is True


def test_is_approved_negative():
    from prax.agent.spokes.content.agent import _is_approved

    assert _is_approved("REVISE\n\nNeeds significant changes.") is False
    assert _is_approved("**REVISE**\n\nThe intro is weak.") is False
    assert _is_approved("The article is approved in some sections but...") is False


def test_reviewer_provider_diversity():
    """When multiple providers are available, the reviewer picks a different one."""
    from prax.agent.spokes.content.reviewer import _available_providers

    with patch("prax.agent.spokes.content.reviewer.settings") as mock_settings:
        mock_settings.openai_key = "sk-test"
        mock_settings.anthropic_key = "sk-ant-test"
        mock_settings.google_vertex_project = None
        mock_settings.google_vertex_location = None
        mock_settings.default_llm_provider = "openai"

        providers = _available_providers()
        assert "openai" in providers
        assert "anthropic" in providers


def test_reviewer_falls_back_to_same_provider():
    """When only one provider is available, reviewer uses same provider at higher tier."""
    with patch("prax.agent.spokes.content.reviewer._available_providers", return_value=["openai"]), \
         patch("prax.plugins.llm_config.get_component_config", return_value={}), \
         patch("prax.agent.spokes.content.reviewer.build_llm") as mock_build:
        mock_build.return_value = MagicMock()
        from prax.agent.spokes.content.reviewer import _pick_reviewer_llm

        _pick_reviewer_llm(writer_provider="openai")
        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["tier"] == "high"
        assert call_kwargs["provider"] == "openai"


def test_reviewer_respects_explicit_config():
    """User config in llm_routing.yaml takes precedence over auto-selection."""
    explicit_cfg = {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "temperature": 0.2}
    with patch("prax.plugins.llm_config.get_component_config", return_value=explicit_cfg), \
         patch("prax.agent.spokes.content.reviewer.build_llm") as mock_build:
        mock_build.return_value = MagicMock()
        from prax.agent.spokes.content.reviewer import _pick_reviewer_llm

        _pick_reviewer_llm(writer_provider="openai")
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["provider"] == "anthropic"
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"


def test_publisher_calls_note_service():
    """publish_draft delegates to note_service.save_and_publish."""
    with patch("prax.agent.spokes.content.publisher.current_user_id") as mock_ctx, \
         patch("prax.services.note_service.save_and_publish") as mock_pub:
        mock_ctx.get.return_value = "+15551234567"
        mock_pub.return_value = {"slug": "test-post", "url": "http://example.com/notes/test-post/", "title": "Test"}

        from prax.agent.spokes.content.publisher import publish_draft

        result = publish_draft("Test Post", "# Hello\nWorld", tags=["test"])
        assert result["slug"] == "test-post"
        mock_pub.assert_called_once_with("+15551234567", "Test Post", "# Hello\nWorld", tags=["test"])


def test_full_pipeline_approved_first_pass():
    """Happy path: research → write → publish → review (APPROVED)."""
    with patch("prax.agent.spokes.content.agent._research", return_value="Research findings here"), \
         patch("prax.agent.spokes.content.agent._write", return_value="# Great Article\nContent here"), \
         patch("prax.agent.spokes.content.agent._publish", return_value={"slug": "great-article", "url": "http://example.com/notes/great-article/"}), \
         patch("prax.agent.spokes.content.agent._review", return_value="APPROVED\n\nExcellent work!"), \
         patch("prax.agent.spokes.content.agent._post_status"), \
         patch("prax.agent.spokes.content.agent._finish"):

        from prax.agent.spokes.content.agent import run_content_pipeline

        result = run_content_pipeline("Great Article Topic")
        assert "approved" in result.lower()
        assert "http://example.com/notes/great-article/" in result
        assert "1 review pass" in result


def test_full_pipeline_revision_cycle():
    """Pipeline runs through one revision before approval."""
    write_calls = [0]

    def mock_write(topic, research, feedback=None, previous_draft=None):
        write_calls[0] += 1
        if feedback:
            return "# Revised Article\nImproved content"
        return "# First Draft\nInitial content"

    review_calls = [0]

    def mock_review(draft, url, pass_number):
        review_calls[0] += 1
        if review_calls[0] == 1:
            return "REVISE\n\n### Must Fix\n- Intro is weak"
        return "APPROVED\n\nMuch improved!"

    with patch("prax.agent.spokes.content.agent._research", return_value="Research"), \
         patch("prax.agent.spokes.content.agent._write", side_effect=mock_write), \
         patch("prax.agent.spokes.content.agent._publish", return_value={"slug": "test", "url": "http://example.com/test/"}), \
         patch("prax.agent.spokes.content.agent._review", side_effect=mock_review), \
         patch("prax.agent.spokes.content.agent._post_status"), \
         patch("prax.agent.spokes.content.agent._finish"):

        from prax.agent.spokes.content.agent import run_content_pipeline

        result = run_content_pipeline("Test Topic")
        assert "approved" in result.lower()
        assert "2 review pass" in result
        assert write_calls[0] == 2  # initial + 1 revision
        assert review_calls[0] == 2


def test_full_pipeline_max_revisions():
    """Pipeline exhausts all revision cycles without approval."""
    with patch("prax.agent.spokes.content.agent._research", return_value="Research"), \
         patch("prax.agent.spokes.content.agent._write", return_value="# Draft\nContent"), \
         patch("prax.agent.spokes.content.agent._publish", return_value={"slug": "test", "url": "http://example.com/test/"}), \
         patch("prax.agent.spokes.content.agent._review", return_value="REVISE\n\nStill needs work."), \
         patch("prax.agent.spokes.content.agent._post_status"), \
         patch("prax.agent.spokes.content.agent._finish"):

        from prax.agent.spokes.content.agent import run_content_pipeline

        result = run_content_pipeline("Test Topic")
        assert "3 revision cycle" in result
        assert "did not fully approve" in result


def test_full_pipeline_research_failure():
    """Pipeline handles research failure gracefully."""
    with patch("prax.agent.spokes.content.agent._research", return_value="Research agent failed: timeout"), \
         patch("prax.agent.spokes.content.agent._post_status"):

        from prax.agent.spokes.content.agent import run_content_pipeline

        result = run_content_pipeline("Test Topic")
        assert "failed" in result.lower()


def test_writer_tools_are_minimal():
    """The Writer only gets search and fetch — no browser, no publishing."""
    from prax.agent.spokes.content.writer import _build_writer_tools

    tools = _build_writer_tools()
    names = {t.name for t in tools}
    assert "background_search_tool" in names
    assert "fetch_url_content" in names
    assert "browser_open" not in names
    assert "note_create" not in names


def test_reviewer_tools_include_browser():
    """The Reviewer gets browser delegation and URL fetch."""
    from prax.agent.spokes.content.reviewer import _build_reviewer_tools

    tools = _build_reviewer_tools()
    names = {t.name for t in tools}
    assert "delegate_browser" in names
    assert "fetch_url_content" in names
