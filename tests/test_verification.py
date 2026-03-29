"""Tests for prax.agent.verification — self-verification checks."""
from __future__ import annotations

import os

import pytest

from prax.agent.verification import (
    VerificationResult,
    verify_delegation_result,
    verify_workspace_file,
)


class TestVerifyWorkspaceFile:
    def test_file_exists_and_has_content(self, tmp_path):
        (tmp_path / "test.md").write_text("# Hello World\n\nSome content here.")
        result = verify_workspace_file(str(tmp_path), "test.md")
        assert result.passed
        assert result.checks_passed >= 2

    def test_file_not_found(self, tmp_path):
        result = verify_workspace_file(str(tmp_path), "missing.md")
        assert not result.passed
        assert "not found" in result.issues[0].lower()

    def test_file_too_short(self, tmp_path):
        (tmp_path / "tiny.md").write_text("hi")
        result = verify_workspace_file(str(tmp_path), "tiny.md", min_length=10)
        assert not result.passed
        assert any("too short" in i for i in result.issues)

    def test_expected_patterns_present(self, tmp_path):
        (tmp_path / "quantum.md").write_text(
            "# Quantum Computing\n\nQubits and superposition are fundamental."
        )
        result = verify_workspace_file(
            str(tmp_path), "quantum.md",
            expected_patterns=["qubits", "superposition"],
        )
        assert result.passed

    def test_expected_patterns_missing(self, tmp_path):
        (tmp_path / "quantum.md").write_text("# Quantum Computing\n\nBasic intro.")
        result = verify_workspace_file(
            str(tmp_path), "quantum.md",
            expected_patterns=["entanglement"],
        )
        assert not result.passed
        assert any("entanglement" in i for i in result.issues)

    def test_finds_file_in_active_subdirectory(self, tmp_path):
        active = tmp_path / "active"
        active.mkdir()
        (active / "notes.md").write_text("# My Notes\n\nContent goes here.")
        result = verify_workspace_file(str(tmp_path), "notes.md")
        assert result.passed

    def test_summary_property(self, tmp_path):
        (tmp_path / "ok.md").write_text("Enough content to pass verification.")
        result = verify_workspace_file(str(tmp_path), "ok.md")
        assert "Verified" in result.summary

        result2 = verify_workspace_file(str(tmp_path), "missing.md")
        assert "failed" in result2.summary.lower()


class TestVerifyDelegationResult:
    def test_good_result_passes(self):
        result = verify_delegation_result(
            "I found three papers on quantum computing. Here are the key findings..."
        )
        assert result.passed

    def test_empty_result_fails(self):
        result = verify_delegation_result("")
        assert not result.passed
        assert any("empty" in i.lower() for i in result.issues)

    def test_none_result_fails(self):
        result = verify_delegation_result(None)
        assert not result.passed

    def test_error_result_fails(self):
        result = verify_delegation_result("Sub-agent failed: connection timeout")
        assert not result.passed
        assert any("error" in i.lower() for i in result.issues)

    def test_too_short_result_fails(self):
        result = verify_delegation_result("OK")
        assert not result.passed
        assert any("short" in i.lower() for i in result.issues)

    def test_substantial_result_passes(self):
        result = verify_delegation_result(
            "The research agent found several relevant papers on the topic "
            "including Smith et al. 2024 which discusses the main findings."
        )
        assert result.passed
        assert result.checks_passed == result.checks_run
