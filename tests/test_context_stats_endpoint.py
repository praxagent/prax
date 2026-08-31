"""The context-stats endpoint 500'd for every TeamWork user (found live 2026-08-30).

`_build_history` grew a `database_name` parameter; two blueprint endpoints had
hand-copied the OLD one-argument call, and their broad `except` turned the
TypeError into `{"error": "Failed to get context stats"}` — the UI showed
"Could not load context stats" with no hint why. The fix is one shared
derivation (`resolve_conversation`) used by reply() and both endpoints.

These tests pin the two things that rotted:
- the derivation used by the endpoints IS the one reply() uses (no copies), and
- calling _build_history through it type-checks against the real signature.
"""
from __future__ import annotations

import inspect

from prax.services.conversation_service import ConversationService, conversation_service


def test_resolve_conversation_returns_db_and_key_for_a_uuid_user():
    name, key = conversation_service.resolve_conversation(
        "a98cd46a-952a-429b-b807-0967a9a18785")
    assert isinstance(name, str) and name
    assert isinstance(key, int)
    # Stable: same user -> same key, every call.
    assert conversation_service.resolve_conversation(
        "a98cd46a-952a-429b-b807-0967a9a18785")[1] == key


def test_resolve_conversation_handles_legacy_prefixed_ids():
    _, key = conversation_service.resolve_conversation("D123456789")
    assert key == 123456789
    _, key2 = conversation_service.resolve_conversation("+15551234567")
    assert key2 == 15551234567


def test_an_explicit_conversation_key_wins():
    _, key = conversation_service.resolve_conversation(
        "a98cd46a-952a-429b-b807-0967a9a18785", conversation_key=42)
    assert key == 42


def test_build_history_call_through_resolve_matches_the_real_signature():
    """The exact call shape the endpoints use must bind to _build_history.

    This is the assertion that would have failed the day the signature grew a
    parameter, instead of 500ing in production behind a broad except.
    """
    args = conversation_service.resolve_conversation("D42")
    sig = inspect.signature(ConversationService._build_history)
    sig.bind(conversation_service, *args)  # raises TypeError on drift


def test_blueprint_no_longer_hand_derives_the_key():
    """The copies are gone: both endpoints go through resolve_conversation."""
    import pathlib
    src = pathlib.Path("prax/blueprints/teamwork_routes.py").read_text()
    assert src.count("resolve_conversation(uid)") == 2
    # The old inline derivation must not survive anywhere in the blueprint.
    assert 'replace("-", "")[:15]' not in src


def test_an_opaque_workspace_id_gets_a_stable_key_never_a_crash():
    """`usr_90c2b48f` killed the first-ever fire of a live schedule.

    The scheduler passed a workspace DIR NAME as the user id; it is neither
    numeric-legacy nor hex, so both derivation branches raised and the
    scheduled message died before generation. Any string must yield a key.
    """
    _, k1 = conversation_service.resolve_conversation("usr_90c2b48f")
    _, k2 = conversation_service.resolve_conversation("usr_90c2b48f")
    assert isinstance(k1, int) and k1 == k2  # stable
    _, k3 = conversation_service.resolve_conversation("any-∆-shape_at all")
    assert isinstance(k3, int)


def test_scheduler_loads_jobs_under_the_canonical_identity(tmp_path, monkeypatch):
    """The dir name is NOT a user id — boot must resolve it when identity knows it.

    Otherwise a rebooted schedule fires under `usr_<id8>` and lands in a
    DIFFERENT conversation (or, before the hardening above, crashed outright).
    """
    import pathlib as _pl

    src = _pl.Path("prax/services/scheduler_service.py").read_text()
    assert "get_user_by_workspace" in src
