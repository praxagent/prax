"""Per-space chat history must survive a restart.

``teamwork_routes._space_conversation_key`` derived the key with Python's
``hash()``, which is salted per interpreter (PYTHONHASHSEED): every restart
computed a different key for the same space, so every space's chat history was
orphaned on every deploy.  The derivation now lives in ONE place —
``ConversationService.resolve_conversation`` — and uses the same sha256 scheme
as the TeamWork per-channel key.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SLUG = "reading-list"
UUID_USER = "a98cd46a-952a-429b-b807-0967a9a18785"


def _documented_key(slug: str) -> int:
    """The contract: first 15 hex digits of sha256("space:<slug>"), as an int."""
    return int(hashlib.sha256(f"space:{slug}".encode()).hexdigest()[:15], 16)


@pytest.fixture
def svc():
    # Imported inside the fixture: the module-level singleton builds an LLM and
    # needs the test key that conftest supplies at test time (see
    # test_context_stats_endpoint for the full reasoning).
    from prax.services.conversation_service import conversation_service
    return conversation_service


def test_space_key_equals_the_documented_digest(svc):
    from prax.services.conversation_service import ConversationService
    assert ConversationService.scoped_conversation_key("space", SLUG) == _documented_key(SLUG)


def test_resolve_conversation_derives_the_space_key(svc):
    _, key = svc.resolve_conversation(UUID_USER, space_slug=SLUG)
    assert key == _documented_key(SLUG)
    # An explicit conversation_key still wins (the per-channel TeamWork path).
    _, explicit = svc.resolve_conversation(UUID_USER, 7, space_slug=SLUG)
    assert explicit == 7
    # And no space means the user-derived key, unchanged.
    _, plain = svc.resolve_conversation(UUID_USER)
    assert plain == int(UUID_USER.replace("-", "")[:15], 16)


def test_space_key_is_stable_across_interpreter_runs(svc):
    """Old code: ``abs(hash(...))`` — a different value under a different hash seed."""
    from prax.services.conversation_service import ConversationService
    here = ConversationService.scoped_conversation_key("space", SLUG)

    code = (
        "from prax.services.conversation_service import ConversationService as C;"
        f"print(C.scoped_conversation_key('space', {SLUG!r}))"
    )
    env = dict(os.environ, PYTHONHASHSEED="424242")  # conftest's TEST_ENV is in os.environ
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    other = int(proc.stdout.strip().splitlines()[-1])
    assert other == here == _documented_key(SLUG)


def test_both_teamwork_call_sites_use_the_service():
    """The blueprint no longer owns a derivation of its own."""
    src = (REPO / "prax" / "blueprints" / "teamwork_routes.py").read_text(encoding="utf-8")
    assert "_space_conversation_key" not in src
    assert "abs(hash(" not in src
    # history GET → resolve_conversation(space_slug=...); chat POST → reply(space_slug=...)
    assert src.count("space_slug=space") == 2
    assert "resolve_conversation(\n            user_id, space_slug=space)" in src
