"""Tests for prax.services.mermaid_validator.

The fast (heuristic) tier is pure Python and always runs. The render tier
shells out to ``mmdc`` and is skipped when the binary isn't available
(local dev without the sandbox; CI without the sandbox container running).
"""
from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from prax.services import mermaid_validator as mv


class TestExtractBlocks:
    def test_no_blocks(self):
        assert mv.extract_blocks("# Just a heading\n\nSome text.") == []

    def test_single_block(self):
        content = "Intro\n\n```mermaid\nflowchart TD\n  A --> B\n```\n\nOutro"
        blocks = mv.extract_blocks(content)
        assert len(blocks) == 1
        line_no, code = blocks[0]
        assert line_no == 3
        assert "flowchart TD" in code

    def test_multiple_blocks(self):
        content = (
            "```mermaid\nflowchart TD\n  A --> B\n```\n"
            "Some prose.\n"
            "```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n"
        )
        blocks = mv.extract_blocks(content)
        assert len(blocks) == 2
        assert blocks[0][1].startswith("flowchart TD")
        assert blocks[1][1].startswith("sequenceDiagram")


class TestValidateFast:
    def test_valid_flowchart_passes(self):
        content = "```mermaid\nflowchart TD\n  A --> B\n  B --> C\n```\n"
        assert mv.validate_fast(content) == []

    def test_valid_sequence_diagram_passes(self):
        content = "```mermaid\nsequenceDiagram\n  Alice->>Bob: Hello\n  Bob-->>Alice: Hi\n```\n"
        assert mv.validate_fast(content) == []

    def test_unknown_diagram_type_rejected(self):
        content = "```mermaid\nAn analytical diagram showing things\n  A --> B\n```\n"
        errors = mv.validate_fast(content)
        assert len(errors) == 1
        assert "does not start with a known mermaid diagram type" in errors[0]

    def test_empty_block_rejected(self):
        content = "```mermaid\n\n%% just a comment\n```\n"
        errors = mv.validate_fast(content)
        assert any("empty diagram" in e for e in errors)

    def test_unbalanced_bracket_rejected(self):
        content = "```mermaid\nflowchart TD\n  A[label --> B\n```\n"
        errors = mv.validate_fast(content)
        assert any("unbalanced bracket" in e for e in errors)

    def test_unbalanced_quote_rejected(self):
        content = '```mermaid\nflowchart TD\n  A["unclosed --> B\n```\n'
        errors = mv.validate_fast(content)
        assert any("unbalanced double quote" in e for e in errors)

    def test_brackets_inside_quotes_allowed(self):
        # Mermaid allows arbitrary chars including brackets inside quoted labels.
        content = '```mermaid\nflowchart TD\n  A["a [b] c"] --> B\n```\n'
        assert mv.validate_fast(content) == []

    def test_comments_ignored_for_first_line(self):
        content = "```mermaid\n%% This is a comment\nflowchart TD\n  A --> B\n```\n"
        assert mv.validate_fast(content) == []

    def test_line_number_in_error_message(self):
        content = (
            "# Title\n\nSome intro.\n\n"
            "```mermaid\nbroken thing\n  X --> Y\n```\n"
        )
        errors = mv.validate_fast(content)
        assert len(errors) == 1
        assert "line 5" in errors[0]

    def test_multiple_blocks_each_validated(self):
        content = (
            "```mermaid\nflowchart TD\n  A --> B\n```\n\n"
            "```mermaid\nNot a diagram type\n```\n"
        )
        errors = mv.validate_fast(content)
        # First block is fine; second is not. The second block opens at
        # line 6 (after the 4-line first block + blank line).
        assert len(errors) == 1
        assert "line 6" in errors[0]

    def test_user_observed_break_rejected(self):
        # The exact failure mode the user reported: a prose first line.
        content = (
            "```mermaid\n"
            "An analytical diagram: training context, interventions, "
            "triggers, and observed behavior\n"
            "  A --> B\n"
            "```\n"
        )
        errors = mv.validate_fast(content)
        assert errors
        assert "does not start with a known mermaid diagram type" in errors[0]


class TestValidateRender:
    """Render-tier tests — skipped when mmdc isn't available."""

    @pytest.fixture(autouse=True)
    def _skip_without_mmdc(self):
        if shutil.which("mmdc") is None:
            pytest.skip("mmdc not installed; sandbox-dependent test")

    def test_no_blocks_returns_empty(self):
        assert mv.validate_render("# Just markdown") == []

    def test_valid_block_passes(self):
        content = "```mermaid\nflowchart TD\n  A --> B\n```\n"
        assert mv.validate_render(content) == []


class TestValidateRenderUnavailable:
    """When mmdc isn't installed, render tier is a graceful no-op."""

    def test_returns_empty_when_mmdc_missing(self):
        with patch.object(mv, "_mmdc_available", return_value=False):
            content = "```mermaid\nflowchart TD\n  A --> B\n```\n"
            assert mv.validate_render(content) == []
