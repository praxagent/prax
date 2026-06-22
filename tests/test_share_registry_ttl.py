"""Tests for the optional public share-link TTL (SHARE_LINK_TTL_ENABLED).

Default-off behaviour (shares live until revoked) is covered too, so the
back-compat contract is explicit.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prax.services import share_registry as sr

USER = "+10000000000"


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    """Point the workspace + settings at a fresh temp dir, TTL off by default."""
    from prax.services import workspace_service
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    from prax.settings import settings
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "share_link_ttl_enabled", False, raising=False)
    monkeypatch.setattr(settings, "share_link_ttl_seconds", 604800, raising=False)
    return tmp_path


def _enable_ttl(monkeypatch, seconds=3600):
    from prax.settings import settings
    monkeypatch.setattr(settings, "share_link_ttl_enabled", True, raising=False)
    monkeypatch.setattr(settings, "share_link_ttl_seconds", seconds, raising=False)


# --------------------------------------------------------------------------- #
# default off → no expiry stamped, shares are permanent
# --------------------------------------------------------------------------- #

def test_no_expiry_when_flag_off():
    entry = sr.register_file(USER, "/tmp/doc.pdf", channel="cli")
    assert "expires_at" not in entry
    assert sr.lookup_by_token(USER, entry["token"]) is not None


# --------------------------------------------------------------------------- #
# flag on → expiry stamped, lookups honour it
# --------------------------------------------------------------------------- #

def test_expiry_stamped_when_flag_on(monkeypatch):
    _enable_ttl(monkeypatch, seconds=3600)
    entry = sr.register_file(USER, "/tmp/doc.pdf")
    assert "expires_at" in entry
    # Still live now.
    assert sr.lookup_by_token(USER, entry["token"]) is not None
    assert sr.find_file_share_globally(entry["token"], entry["public_name"]) is not None


def test_expired_file_is_hidden_and_purged(monkeypatch):
    _enable_ttl(monkeypatch, seconds=3600)
    entry = sr.register_file(USER, "/tmp/doc.pdf")
    token = entry["token"]
    # Force the stored entry into the past.
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    _backdate(token, past)

    assert sr.lookup_by_token(USER, token) is None
    assert sr.find_file_share_globally(token, entry["public_name"]) is None
    # list_all both hides AND purges the dead entry from disk.
    assert sr.list_all(USER) == []
    assert sr._load(USER) == {}


def test_course_publish_expires(monkeypatch):
    _enable_ttl(monkeypatch, seconds=3600)
    entry = sr.register_course(USER, "algebra")
    assert sr.is_course_public(USER, "algebra") is True
    assert sr.is_course_public_globally("algebra") is True
    _backdate(entry["token"], (datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    assert sr.is_course_public(USER, "algebra") is False
    assert sr.is_course_public_globally("algebra") is False


def test_republish_renews_lease(monkeypatch):
    _enable_ttl(monkeypatch, seconds=3600)
    first = sr.register_note(USER, "intro")
    # Backdate close to expiry, then re-publish — lease should be pushed out.
    _backdate(first["token"], (datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    assert sr.is_note_public(USER, "intro") is False  # expired
    second = sr.register_note(USER, "intro")           # idempotent re-publish
    assert second["token"] == first["token"]           # same entry
    assert sr.is_note_public(USER, "intro") is True    # lease renewed


def test_malformed_expiry_failclosed(monkeypatch):
    _enable_ttl(monkeypatch, seconds=3600)
    entry = sr.register_file(USER, "/tmp/doc.pdf")
    _backdate(entry["token"], "garbage")
    assert sr.lookup_by_token(USER, entry["token"]) is None


def test_existing_entry_unaffected_by_flag(monkeypatch):
    """A share created while the flag was OFF (no expires_at) stays live even
    after TTL is later switched on — enforcement only acts on stamped entries."""
    entry = sr.register_file(USER, "/tmp/doc.pdf")   # flag off → no expires_at
    _enable_ttl(monkeypatch, seconds=3600)
    assert sr.lookup_by_token(USER, entry["token"]) is not None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _backdate(token: str, expires_at: str) -> None:
    """Overwrite a stored entry's expires_at directly on disk."""
    entries = sr._load(USER)
    entries[token]["expires_at"] = expires_at
    sr._save(USER, entries)
