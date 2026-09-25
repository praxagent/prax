"""DISCORD_USE_PROXY: discord.py ignores HTTPS_PROXY, so pass it explicitly."""
from __future__ import annotations

import prax.settings as prax_settings
from prax.services import discord_service


def test_off_by_default(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8786")
    assert discord_service._proxy_kwargs() == {}


def test_proxy_and_credentials_are_passed_separately(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "discord_use_proxy", True)
    monkeypatch.setenv("HTTPS_PROXY", "http://prax:s3cret@127.0.0.1:8786")
    kw = discord_service._proxy_kwargs()
    assert kw["proxy"] == "http://127.0.0.1:8786"  # no credentials in the URL
    assert (kw["proxy_auth"].login, kw["proxy_auth"].password) == ("prax", "s3cret")


def test_flag_without_a_proxy_connects_directly(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "discord_use_proxy", True)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    assert discord_service._proxy_kwargs() == {}


def test_the_client_accepts_the_arguments():
    import inspect

    import discord
    src = inspect.getsource(discord.Client.__init__)
    assert "proxy" in src and "proxy_auth" in src


def test_encoded_credentials_are_decoded_and_ipv6_keeps_brackets(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "discord_use_proxy", True)
    monkeypatch.setenv("HTTPS_PROXY", "http://prax:p%40ss@[::1]:8786")
    kw = discord_service._proxy_kwargs()
    assert kw["proxy"] == "http://[::1]:8786"
    assert kw["proxy_auth"].password == "p@ss"
