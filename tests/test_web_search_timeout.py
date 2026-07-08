"""Tests for the WEB_SEARCH_TIMEOUT_S flag on background_search_tool."""
from __future__ import annotations

import asyncio
import time

import prax.agent.tools as tools_mod
from prax.agent.tools import background_search_tool


def _set_timeout(monkeypatch, value: int) -> None:
    # conftest reloads prax.settings per-test — patch the live instance.
    import prax.settings as settings_mod
    monkeypatch.setattr(
        settings_mod.settings, "web_search_timeout_s", value, raising=False,
    )


def test_hanging_search_times_out_with_flag(monkeypatch):
    async def _hangs(query, to_number=None, sms_bool=False):
        await asyncio.sleep(60)
        return "never reached"

    monkeypatch.setattr(tools_mod, "background_search", _hangs)
    _set_timeout(monkeypatch, 1)
    start = time.monotonic()
    out = background_search_tool.func("anything")
    assert time.monotonic() - start < 10  # abandoned, not stuck for 60s
    assert "timed out after 1s" in out
    assert "fetch_url_content" in out  # actionable guidance for the agent


def test_fast_search_unaffected_by_flag(monkeypatch):
    async def _fast(query, to_number=None, sms_bool=False):
        return "search results here"

    monkeypatch.setattr(tools_mod, "background_search", _fast)
    _set_timeout(monkeypatch, 5)
    assert background_search_tool.func("q") == "search results here"


def test_default_zero_means_no_timeout_wrapping(monkeypatch):
    async def _slowish(query, to_number=None, sms_bool=False):
        await asyncio.sleep(0.05)
        return "completed without a bound"

    monkeypatch.setattr(tools_mod, "background_search", _slowish)
    _set_timeout(monkeypatch, 0)  # prior behavior: run to completion
    assert background_search_tool.func("q") == "completed without a bound"
