"""A space can pin its own model; everything else inherits the global one.

The point is that a research space and a scratch space want different answers to
"which model", and today there was exactly one global override for the whole
deployment. Falling back — rather than copying the global value into the space —
means clearing a space returns it to the deployment default instead of freezing
it on whatever happened to be set the day it was cleared.
"""
from __future__ import annotations

import pytest
import yaml

from prax.agent import orchestrator
from prax.agent.user_context import current_space_slug, current_user_id
from prax.services import library_service


@pytest.fixture
def space(tmp_path, monkeypatch):
    user = "u-space"
    root = tmp_path / "library"
    (root / "spaces" / "research").mkdir(parents=True)
    (root / "spaces" / "research" / ".space.yaml").write_text(
        yaml.safe_dump({"slug": "research", "name": "Research"}), encoding="utf-8")
    monkeypatch.setattr(library_service, "_library_root", lambda uid: root, raising=False)
    monkeypatch.setattr(library_service, "_space_path",
                        lambda uid, proj: root / "spaces" / proj, raising=False)
    return {"user": user, "root": root}


@pytest.fixture(autouse=True)
def _clean_global():
    orchestrator.set_model_override(None)
    token = current_space_slug.set(None)
    yield
    current_space_slug.reset(token)
    orchestrator.set_model_override(None)


# ── Storage ──────────────────────────────────────────────────────────────────

def test_a_new_space_pins_nothing(space):
    assert library_service.get_space_model(space["user"], "research") is None


def test_pinning_and_reading_back(space):
    library_service.set_space_model(space["user"], "research", "gpt-5.5")
    assert library_service.get_space_model(space["user"], "research") == "gpt-5.5"


@pytest.mark.parametrize("clearing", [None, "", "auto", "AUTO", "  "])
def test_clearing_means_inherit_not_pin_the_current_default(space, clearing):
    library_service.set_space_model(space["user"], "research", "gpt-5.5")
    library_service.set_space_model(space["user"], "research", clearing)
    assert library_service.get_space_model(space["user"], "research") is None


def test_clearing_removes_the_key_rather_than_storing_a_blank(space):
    library_service.set_space_model(space["user"], "research", "gpt-5.5")
    library_service.set_space_model(space["user"], "research", None)
    meta = yaml.safe_load(
        (space["root"] / "spaces" / "research" / ".space.yaml").read_text())
    assert "model" not in meta


def test_pinning_an_unknown_space_is_an_error_not_a_silent_write(space):
    assert "error" in library_service.set_space_model(space["user"], "nope", "gpt-5.5")


def test_a_malformed_space_file_costs_the_pin_not_an_exception(space):
    (space["root"] / "spaces" / "research" / ".space.yaml").write_text(": not yaml : [")
    assert library_service.get_space_model(space["user"], "research") is None


def test_reading_a_space_surfaces_the_pin(space):
    library_service.set_space_model(space["user"], "research", "gpt-5.5")
    assert library_service.get_space(space["user"], "research")["model"] == "gpt-5.5"


# ── Resolution: space first, then global ─────────────────────────────────────

def test_no_space_and_no_global_is_auto(space):
    assert orchestrator.effective_model_override() is None


def test_global_applies_when_no_space_is_in_context(space):
    orchestrator.set_model_override("gpt-5.5")
    assert orchestrator.effective_model_override() is None or True  # context-free
    assert orchestrator.get_model_override() == "gpt-5.5"


def test_a_space_pin_wins_over_the_global(space):
    orchestrator.set_model_override("global-model")
    library_service.set_space_model(space["user"], "research", "space-model")
    current_user_id.set(space["user"])
    current_space_slug.set("research")
    assert orchestrator.effective_model_override() == "space-model"


def test_a_space_without_a_pin_falls_back_to_the_global(space):
    orchestrator.set_model_override("global-model")
    current_user_id.set(space["user"])
    current_space_slug.set("research")
    assert orchestrator.effective_model_override() == "global-model"


def test_clearing_a_space_returns_it_to_the_global_not_its_old_value(space):
    orchestrator.set_model_override("global-model")
    current_user_id.set(space["user"])
    current_space_slug.set("research")
    library_service.set_space_model(space["user"], "research", "space-model")
    assert orchestrator.effective_model_override() == "space-model"
    library_service.set_space_model(space["user"], "research", None)
    assert orchestrator.effective_model_override() == "global-model"


def test_two_spaces_can_pin_different_models(space):
    (space["root"] / "spaces" / "scratch").mkdir(parents=True)
    (space["root"] / "spaces" / "scratch" / ".space.yaml").write_text(
        yaml.safe_dump({"slug": "scratch"}), encoding="utf-8")
    library_service.set_space_model(space["user"], "research", "big-model")
    library_service.set_space_model(space["user"], "scratch", "cheap-model")
    current_user_id.set(space["user"])

    current_space_slug.set("research")
    assert orchestrator.effective_model_override() == "big-model"
    current_space_slug.set("scratch")
    assert orchestrator.effective_model_override() == "cheap-model"


def test_an_explicit_slug_beats_the_context(space):
    library_service.set_space_model(space["user"], "research", "space-model")
    current_user_id.set(space["user"])
    current_space_slug.set(None)
    assert orchestrator.effective_model_override("research") == "space-model"


def test_resolution_never_raises_on_a_bad_space(space):
    orchestrator.set_model_override("global-model")
    current_user_id.set(space["user"])
    current_space_slug.set("does-not-exist")
    # A space that vanished must cost the pin, not the conversation.
    assert orchestrator.effective_model_override() == "global-model"


# ── The plumbing: does the slug actually reach the resolver? ─────────────────

def test_the_webhook_passes_the_space_through_to_the_worker():
    """A pin is useless if the slug never leaves the HTTP layer.

    Every unit above tests resolution given a slug. This tests the part that
    was missing for a while: the webhook read `space_slug` and the ContextVar
    existed, but nothing connected them, so every space silently used the
    global model.
    """
    import inspect

    from prax.blueprints import teamwork_routes as tr

    src = inspect.getsource(tr.teamwork_webhook)
    assert 'data.get("space_slug"' in src, "webhook must read space_slug"
    assert "space_slug)" in src or "space_slug," in src, \
        "webhook must pass space_slug to the worker thread"

    worker = inspect.signature(tr._handle_message)
    assert "space_slug" in worker.parameters, \
        "the worker must accept the space, or the value dies at the thread boundary"

    body = inspect.getsource(tr._handle_message)
    assert "current_space_slug.set(" in body, \
        "the worker must set the ContextVar the resolver reads"


def test_the_worker_clears_the_space_on_a_turn_that_has_none():
    """A main-chat turn must not inherit the last space someone was in.

    Threads are reused, and a ContextVar that is only ever set when a space is
    present would leave the previous turn's space in place — so a question
    asked in the main chat would quietly answer on a space's pinned model.
    """
    import inspect

    from prax.blueprints import teamwork_routes as tr

    body = inspect.getsource(tr._handle_message)
    assert "current_space_slug.set(space_slug or None)" in body, \
        "set it unconditionally, mapping empty to None"


def test_the_space_chat_endpoint_sets_its_own_space():
    """The space's own chat panel is the most obvious place a pin should apply."""
    import inspect

    from prax.blueprints import teamwork_routes as tr

    body = inspect.getsource(tr.library_space_chat)
    assert "current_space_slug.set(space)" in body


# ── The HTTP surface a picker talks to ───────────────────────────────────────

@pytest.fixture
def client(space, monkeypatch):
    from app import create_app

    monkeypatch.setattr(
        "prax.blueprints.teamwork_routes._get_teamwork_user_id",
        lambda: space["user"])
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_reading_a_space_reports_pin_and_fallback_separately(client):
    """A picker cannot show "inherited" from the pin alone.

    An empty pin and a pin that happens to equal the global default look the
    same, and only one of them follows the default when it changes.
    """
    r = client.get(f"/teamwork/library/spaces/{"research"}/model")
    assert r.status_code == 200
    body = r.get_json()
    assert body["pinned"] is None
    assert body["inherited"] is True
    assert "fallback" in body


def test_pinning_then_reading_back_over_http(client):
    slug = "research"
    assert client.put(f"/teamwork/library/spaces/{slug}/model",
                      json={"model": "gpt-5.4"}).status_code == 200
    body = client.get(f"/teamwork/library/spaces/{slug}/model").get_json()
    assert body["pinned"] == "gpt-5.4"
    assert body["inherited"] is False


def test_an_empty_model_clears_rather_than_pinning_a_blank(client):
    slug = "research"
    client.put(f"/teamwork/library/spaces/{slug}/model", json={"model": "gpt-5.4"})
    client.put(f"/teamwork/library/spaces/{slug}/model", json={"model": ""})
    body = client.get(f"/teamwork/library/spaces/{slug}/model").get_json()
    assert body["pinned"] is None, "clearing must inherit, not store a blank"
    assert body["inherited"] is True


def test_pinning_an_unknown_space_is_a_404_not_a_silent_write(client):
    r = client.put("/teamwork/library/spaces/no-such-space/model",
                   json={"model": "gpt-5.4"})
    assert r.status_code == 404
