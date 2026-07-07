"""Tests for the URL_FETCH_SOURCE_TAGS flag on fetch_url_content."""
from __future__ import annotations

import prax.services.url_reader as ur
from prax.agent.action_policy import SourcedResult, SourceReliability
from prax.agent.tools import fetch_url_content


def _set_flag(monkeypatch, value: bool) -> None:
    # conftest reloads prax.settings per-test — patch the live instance, not a
    # stale module-load-time import.
    import prax.settings as settings_mod
    monkeypatch.setattr(
        settings_mod.settings, "url_fetch_source_tags", value, raising=False,
    )


def test_default_output_unchanged(monkeypatch):
    monkeypatch.setattr(
        ur, "fetch_markdown_with_source",
        lambda url, max_chars=15_000: ("# Tweet\n\nbody", "x-api"),
    )
    _set_flag(monkeypatch, False)
    out = fetch_url_content.func("https://x.com/u/status/1")
    assert not isinstance(out, SourcedResult)
    assert out == "# Tweet\n\nbody\n\nSource: https://x.com/u/status/1"


def test_flag_on_labels_api_fetches(monkeypatch):
    monkeypatch.setattr(
        ur, "fetch_markdown_with_source",
        lambda url, max_chars=15_000: ("# Tweet\n\nbody", "x-api"),
    )
    _set_flag(monkeypatch, True)
    out = fetch_url_content.func("https://x.com/u/status/1")
    assert isinstance(out, SourcedResult)
    assert out.reliability is SourceReliability.VERIFIED
    assert out.source_label == "X API v2"
    assert out.endswith("(fetched via X API v2)")


def test_flag_on_leaves_web_reader_untagged(monkeypatch):
    monkeypatch.setattr(
        ur, "fetch_markdown_with_source",
        lambda url, max_chars=15_000: ("page text", "web-reader"),
    )
    _set_flag(monkeypatch, True)
    out = fetch_url_content.func("https://example.com")
    assert not isinstance(out, SourcedResult)
    assert out == "page text\n\nSource: https://example.com"
