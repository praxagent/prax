"""Drift guard: every TeamWork channel Prax posts to must be registered, and
no spoke role may be.

``prax/services/teamwork_channels.py`` lists the channels Prax ensures at
startup and the role agents it registers.  A post to a channel the TeamWork
project does not have is dropped, which is how every ``#browser`` and
``#content`` post was lost: the spokes named channels nothing ever created.
These tests scan the source for the literal names at every posting call shape
and FAIL if one is missing from the registry — so a new channel cannot
silently start dropping posts again.

Roles run the other way.  Registering a role switches on TeamWork's activity
log, live output and status for it, so when the spoke roles were briefly
registered their tool output — ``browser_login`` results included — flowed
into the persistent activity log, and every turn's ``reset_all_idle`` grew
from 5 to 16 synchronous PATCHes.  The role tests pin startup to the core
roles so that sink cannot be switched on again by a one-line registry edit.

The rest covers the runtime half: the lazy ensure that recovers a registry
channel the project lacks, the once-per-name warning, per-name ensure locks,
and the startup hooks.
"""
from __future__ import annotations

import ast
import logging
import re
import threading
import time
from pathlib import Path

import pytest

from prax.services import teamwork_hooks
from prax.services.teamwork_channels import PRAX_CHANNELS, PRAX_ROLE_AGENTS, role_agent_names
from prax.services.teamwork_service import TeamWorkClient

PRAX_ROOT = Path(__file__).resolve().parent.parent / "prax"

# Call name -> (positional index or None, keyword) of the channel-name argument.
# send_message/send_typing/typing are keyword-only here: other modules have
# functions of the same name whose positional arguments are not channels.
_CHANNEL_ARGS: dict[str, tuple[int | None, str]] = {
    "post_to_channel": (0, "channel"),
    "forward_to_channel": (0, "channel_name"),
    "forward_external_message": (0, "channel_name"),
    "mirror_coding_agent_turn": (0, "channel"),
    "run_spoke": (None, "channel"),
    "send_message": (None, "channel"),
    "send_typing": (None, "channel"),
    "typing": (None, "channel"),
}

# Same, for the role an agent posts as or reports status for.
_ROLE_ARGS: dict[str, tuple[int | None, str]] = {
    "set_role_status": (0, "role_name"),
    "push_live_output": (0, "agent_name"),
    "log_activity": (0, "agent_name"),
    "post_to_channel": (2, "agent_name"),
    "run_spoke": (None, "role_name"),
}

# The coding-agent channels are created lazily on first use, so they are
# legitimately absent from the registry — taken from the code, not restated.
_LAZY_CHANNELS = set(teamwork_hooks._AGENT_DISPLAY_NAMES)

# The role agents startup registers besides the orchestrator.  Restated here
# rather than read from PRAX_ROLE_AGENTS, so the registry cannot grow without
# this file changing too.  Executor is also the workspace spoke's role_name;
# that predates this guard.
_CORE_ROLES = ["Planner", "Researcher", "Executor", "Auditor"]


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _literal_args(source: str, shapes: dict[str, tuple[int | None, str]]) -> list[tuple[str, int]]:
    """(literal, lineno) for each string literal passed at one of *shapes*."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        shape = shapes.get(_call_name(node) or "")
        if shape is None:
            continue
        position, keyword = shape
        candidates = [kw.value for kw in node.keywords if kw.arg == keyword]
        if position is not None and len(node.args) > position:
            candidates.append(node.args[position])
        for value in candidates:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.append((value.value, node.lineno))
    return found


def _scan(shapes: dict[str, tuple[int | None, str]]) -> dict[str, list[str]]:
    """Literal name -> ["path:line", ...] across the prax package."""
    names: dict[str, list[str]] = {}
    for path in sorted(PRAX_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(PRAX_ROOT.parent).as_posix()
        for name, lineno in _literal_args(path.read_text(encoding="utf-8"), shapes):
            names.setdefault(name, []).append(f"{rel}:{lineno}")
    return names


# ── Drift guards ─────────────────────────────────────────────────────────────

def test_every_channel_prax_posts_to_is_registered():
    """A post to an unregistered channel is dropped. If this fails: add the
    channel to PRAX_CHANNELS in prax/services/teamwork_channels.py."""
    posted = _scan(_CHANNEL_ARGS)
    missing = {name: sites for name, sites in posted.items()
               if name not in PRAX_CHANNELS and name not in _LAZY_CHANNELS}
    assert not missing, (
        f"Prax posts to these TeamWork channels but they are not in PRAX_CHANNELS, "
        f"so nothing creates them and every post is dropped: {missing}"
    )


def test_the_registry_has_no_stale_entries():
    """Every registered channel is posted to somewhere (no ghosts ensured at startup)."""
    stale = set(PRAX_CHANNELS) - set(_scan(_CHANNEL_ARGS))
    assert not stale, f"PRAX_CHANNELS entries nothing posts to: {sorted(stale)}"


def _spoke_roles() -> dict[str, list[str]]:
    """Every literal role Prax reports under, other than the core roles."""
    return {name: sites for name, sites in _scan(_ROLE_ARGS).items() if name not in _CORE_ROLES}


def test_the_registry_holds_exactly_the_core_roles():
    assert role_agent_names() == _CORE_ROLES


def test_no_spoke_role_is_registered():
    """TeamWorkClient skips activity-log, live-output and status calls for an
    unregistered agent; registering a spoke role turns them on, streaming its
    tool output into the persistent activity log. If this fails: do not
    register the spoke role (its channel posts land without it)."""
    spoke_roles = _spoke_roles()
    # Guards the guard: a scan that found nothing would pass for the wrong reason.
    assert {"Browser Agent", "Content Editor"} <= set(spoke_roles)
    registered = set(role_agent_names()) & set(spoke_roles)
    assert not registered, f"Spoke roles registered in PRAX_ROLE_AGENTS: {sorted(registered)}"


def test_no_create_agent_call_names_a_spoke_role():
    # The registry is not the only way in: a direct create_agent at startup
    # (app.py) or anywhere in the package registers the role just the same.
    shape = {"create_agent": (0, "name")}
    created = _scan(shape)
    app_py = PRAX_ROOT.parent / "app.py"
    for name, lineno in _literal_args(app_py.read_text(encoding="utf-8"), shape):
        created.setdefault(name, []).append(f"app.py:{lineno}")
    offending = {name: sites for name, sites in created.items() if name in _spoke_roles()}
    assert not offending, f"create_agent registers spoke roles: {offending}"


def test_the_role_list_has_no_stale_entries():
    """Every registered role is used somewhere (no idle ghosts in the UI)."""
    stale = set(role_agent_names()) - set(_scan(_ROLE_ARGS))
    assert not stale, f"PRAX_ROLE_AGENTS entries nothing posts as: {sorted(stale)}"


def test_lazily_created_channels_stay_out_of_the_registry():
    # Ensuring them at startup would put dead channels in every sidebar; they
    # appear only once a coding-agent session actually uses one.
    assert _LAZY_CHANNELS
    assert not _LAZY_CHANNELS & set(PRAX_CHANNELS)


def test_registered_names_are_valid_and_unique():
    for name in PRAX_CHANNELS:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", name), name
    names = [agent.name for agent in PRAX_ROLE_AGENTS]
    assert len(names) == len(set(names))
    assert role_agent_names() == names


def test_the_scanner_sees_every_call_shape():
    # Guards the guard: a scanner that silently matched nothing would pass
    # the drift tests above for the wrong reason.
    source = (
        'post_to_channel("a", "x", agent_name="Role A")\n'
        'hooks.post_to_channel(channel="b", content="x")\n'
        'forward_to_channel("c", "who", "x")\n'
        'tw.forward_external_message(channel_name="d", sender_label="w", content="x")\n'
        'mirror_coding_agent_turn("e", None, None)\n'
        'run_spoke(task="t", channel="f", role_name="Role B")\n'
        'tw.send_message(content="x", channel="g")\n'
        'tw.send_message(uid, "not a channel")\n'
        'set_role_status("Role C", "idle")\n'
        'post_to_channel(variable, "x")\n'
    )
    assert sorted(n for n, _ in _literal_args(source, _CHANNEL_ARGS)) == list("abcdefg")
    assert sorted(n for n, _ in _literal_args(source, _ROLE_ARGS)) == ["Role A", "Role B", "Role C"]


# ── Lazy ensure and the once-per-name warning ────────────────────────────────

class _FakeTeamWork:
    """Stands in for TeamWork's HTTP API behind TeamWorkClient._post."""

    def __init__(self, channels: dict[str, str] | None = None):
        self.channels = dict(channels or {})
        self.ensure_calls: list[list[str]] = []
        self.posts: list[dict] = []
        self.ensure_fails = False
        self.ensure_creates = True
        self.ensure_delay = 0.0

    def __call__(self, path: str, json: dict) -> dict:
        if path.endswith("/ensure-channels"):
            self.ensure_calls.append([ch["name"] for ch in json["channels"]])
            if self.ensure_delay:
                time.sleep(self.ensure_delay)
            if self.ensure_fails:
                raise RuntimeError("teamwork down")
            if self.ensure_creates:
                for ch in json["channels"]:
                    self.channels.setdefault(ch["name"], f"id-{ch['name']}")
            return {"channels": dict(self.channels)}
        if path.endswith("/messages"):
            self.posts.append(json)
            return {"message_id": f"m{len(self.posts)}"}
        raise AssertionError(f"unexpected TeamWork call: {path}")


@pytest.fixture
def client(monkeypatch):
    tw = TeamWorkClient(base_url="http://stub.invalid", api_key="stub")
    tw._project_id = "p1"
    tw._channels = {"general": "id-general"}
    fake = _FakeTeamWork(tw._channels)
    monkeypatch.setattr(tw, "_post", fake)
    return tw, fake


def _messages(caplog, level):
    return [r.getMessage() for r in caplog.records
            if r.name == "prax.services.teamwork_service" and r.levelno == level]


def test_a_registry_channel_the_project_lacks_is_created_on_first_post(client):
    tw, fake = client
    assert tw.send_message("found it", channel="browser", agent_name=None) == "m1"
    assert fake.ensure_calls == [["browser"]]
    assert fake.posts[0]["channel_id"] == "id-browser"
    # Now known: the next post needs no ensure.
    tw.send_message("again", channel="browser")
    assert fake.ensure_calls == [["browser"]]


def test_the_lazy_ensure_is_tried_once_per_name(client):
    # An unreachable TeamWork must cost one request, not one per post.
    tw, fake = client
    fake.ensure_fails = True
    assert tw.send_message("x", channel="content") is None
    assert tw.send_message("y", channel="content") is None
    assert fake.ensure_calls == [["content"]]
    assert fake.posts == []


def test_a_name_outside_the_registry_is_never_ensured(client):
    tw, fake = client
    assert tw.send_message("x", channel="not-a-channel") is None
    assert fake.ensure_calls == []


def test_an_unknown_channel_warns_once_then_counts_at_debug(client, caplog):
    tw, _ = client
    with caplog.at_level(logging.DEBUG, logger="prax.services.teamwork_service"):
        for _ in range(3):
            tw.send_message("x", channel="not-a-channel")
    warnings = _messages(caplog, logging.WARNING)
    assert len(warnings) == 1 and "Unknown channel: not-a-channel" in warnings[0]
    debug = [m for m in _messages(caplog, logging.DEBUG) if "not-a-channel" in m]
    assert "dropped 1 more" in debug[0] and "dropped 2 more" in debug[1]


def test_each_unknown_name_gets_its_own_warning(client, caplog):
    tw, _ = client
    with caplog.at_level(logging.WARNING, logger="prax.services.teamwork_service"):
        tw.send_message("x", channel="one")
        tw.send_message("x", channel="two")
    assert len(_messages(caplog, logging.WARNING)) == 2


def test_a_name_that_resolves_is_forgotten_by_the_warning(client, caplog):
    tw, _ = client
    with caplog.at_level(logging.WARNING, logger="prax.services.teamwork_service"):
        tw.send_message("x", channel="later")
        tw._channels["later"] = "id-later"
        assert tw.send_message("x", channel="later") == "m1"
        del tw._channels["later"]
        tw.send_message("x", channel="later")
    # Missing again after it resolved is news, so it warns again.
    assert len(_messages(caplog, logging.WARNING)) == 2


def test_forwarding_resolves_channel_names_the_same_way(client):
    tw, fake = client
    assert tw.forward_external_message("discord", "Alice", "hello") == "m1"
    assert fake.ensure_calls == [["discord"]]
    assert fake.posts[0] == {"channel_id": "id-discord", "agent_id": None,
                             "content": "**[Alice]** hello"}


def test_forwarding_to_an_unknown_channel_warns(client, caplog):
    # It used to return None without a word.
    tw, _ = client
    with caplog.at_level(logging.WARNING, logger="prax.services.teamwork_service"):
        assert tw.forward_external_message("not-a-channel", "Alice", "hi") is None
    assert any("not-a-channel" in m for m in _messages(caplog, logging.WARNING))


def test_forwarding_before_the_project_exists_spends_nothing(client):
    tw, fake = client
    tw._project_id = None
    assert tw.forward_external_message("sms", "+1555", "hi") is None
    assert fake.ensure_calls == []
    # The channel's one lazy ensure is still available once there is a project.
    tw._project_id = "p1"
    assert tw.forward_external_message("sms", "+1555", "hi") == "m1"
    assert fake.ensure_calls == [["sms"]]


def test_concurrent_posts_to_a_missing_channel_ensure_it_once(client):
    tw, fake = client
    fake.ensure_delay = 0.05
    results: list[str | None] = []
    threads = [threading.Thread(target=lambda: results.append(tw.send_message("x", channel="research")))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fake.ensure_calls == [["research"]]
    # Posts that arrived mid-ensure waited for it rather than dropping.
    assert len(fake.posts) == 8 and None not in results


def test_a_slow_lazy_ensure_does_not_stall_other_channel_names(client, monkeypatch, caplog):
    # forward_to_channel runs on Discord's event loop: a post to one name must
    # not wait out another name's lazy ensure (a request with a 15 s timeout).
    tw, fake = client
    entered, release = threading.Event(), threading.Event()

    def post(path, json):
        if path.endswith("/ensure-channels") and json["channels"][0]["name"] == "browser":
            entered.set()
            release.wait(10)
        return fake(path, json)

    monkeypatch.setattr(tw, "_post", post)
    slow_result: list[str | None] = []
    others: list[str | None] = []
    slow = threading.Thread(target=lambda: slow_result.append(tw.send_message("x", channel="browser")),
                            daemon=True)
    other = threading.Thread(target=lambda: others.extend([
        tw.send_message("x", channel="not-a-channel"),   # unknown: dropped, warned
        tw.send_message("x", channel="not-a-channel"),   # counted at DEBUG
        tw.send_message("y", channel="content"),         # its own lazy ensure
    ]), daemon=True)
    try:
        slow.start()
        assert entered.wait(5), "the browser ensure never started"
        with caplog.at_level(logging.WARNING, logger="prax.services.teamwork_service"):
            other.start()
            other.join(5)
        assert not other.is_alive(), "posts to other channels waited behind the browser ensure"
        assert others == [None, None, "m1"]
        assert not slow_result  # still inside its ensure
        warnings = [m for m in _messages(caplog, logging.WARNING) if "not-a-channel" in m]
        assert len(warnings) == 1
    finally:
        release.set()
        slow.join(5)
    assert slow_result == ["m2"]
    assert sorted(fake.ensure_calls) == [["browser"], ["content"]]


def test_a_failed_ensure_is_a_warning_not_debug(client, caplog):
    tw, fake = client
    fake.ensure_fails = True
    with caplog.at_level(logging.WARNING, logger="prax.services.teamwork_service"):
        tw.ensure_channels([{"name": "browser", "description": "d"}])
    assert any("Failed to ensure" in m and "browser" in m for m in _messages(caplog, logging.WARNING))


def test_an_ensure_that_does_not_return_a_channel_warns(client, caplog):
    tw, fake = client
    fake.ensure_creates = False
    with caplog.at_level(logging.WARNING, logger="prax.services.teamwork_service"):
        tw.ensure_channels([{"name": "general", "description": "d"},
                            {"name": "browser", "description": "d"}])
    warnings = [m for m in _messages(caplog, logging.WARNING) if "did not return" in m]
    assert len(warnings) == 1 and "browser" in warnings[0] and "general" not in warnings[0]


# ── Startup hooks ────────────────────────────────────────────────────────────

class _RecordingClient:
    def __init__(self):
        self.ensured: list[list[dict]] = []
        self.created: list[tuple[str, str, str]] = []
        self.fail_on: set[str] = set()

    def ensure_channels(self, channels):
        self.ensured.append(channels)

    def create_agent(self, name, role="assistant", soul=""):
        self.created.append((name, role, soul))
        if name in self.fail_on:
            raise RuntimeError("teamwork rejected it")
        return f"id-{name}"


@pytest.fixture
def hooked(monkeypatch):
    fake = _RecordingClient()
    monkeypatch.setattr(teamwork_hooks, "_tw", lambda: fake)
    return fake


def test_ensure_prax_channels_ensures_the_whole_registry(hooked):
    teamwork_hooks.ensure_prax_channels()
    assert hooked.ensured == [[{"name": n, "description": d} for n, d in PRAX_CHANNELS.items()]]


def test_ensure_mirror_channels_is_kept_as_an_alias(hooked):
    teamwork_hooks.ensure_mirror_channels()
    assert [ch["name"] for ch in hooked.ensured[0]] == list(PRAX_CHANNELS)


def test_the_startup_hooks_are_no_ops_without_teamwork(monkeypatch):
    monkeypatch.setattr(teamwork_hooks, "_tw", lambda: None)
    teamwork_hooks.ensure_prax_channels()
    teamwork_hooks.register_role_agents()


def test_after_the_startup_ensure_every_registry_post_lands(client, monkeypatch):
    tw, fake = client
    monkeypatch.setattr(teamwork_hooks, "_tw", lambda: tw)
    teamwork_hooks.ensure_prax_channels()
    for name in PRAX_CHANNELS:
        assert tw.send_message("x", channel=name) is not None, name
    assert len(fake.ensure_calls) == 1  # the startup one; nothing lazy needed


def test_startup_registers_the_core_roles_and_no_spoke_role(hooked):
    # What app.py's startup runs after registering the orchestrator.
    teamwork_hooks.register_role_agents()
    registered = [name for name, _, _ in hooked.created]
    assert registered == _CORE_ROLES
    assert not set(registered) & set(_spoke_roles())
    assert hooked.created == [tuple(a) for a in PRAX_ROLE_AGENTS]


def test_one_failed_registration_does_not_cost_the_rest(hooked, caplog):
    first = PRAX_ROLE_AGENTS[0].name
    hooked.fail_on = {first}
    with caplog.at_level(logging.WARNING, logger="prax.services.teamwork_hooks"):
        teamwork_hooks.register_role_agents()
    assert [c[0] for c in hooked.created] == _CORE_ROLES
    assert any(first in r.getMessage() for r in caplog.records)


def test_reset_all_idle_patches_only_the_orchestrator_and_core_roles(monkeypatch):
    # It runs on every turn's critical path, one synchronous PATCH per role,
    # and flips a role idle even if a concurrent turn is using it.
    from prax.settings import settings

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(teamwork_hooks, "set_role_status", lambda role, status: calls.append((role, status)))
    teamwork_hooks.reset_all_idle()
    expected = list(dict.fromkeys([settings.agent_name, *_CORE_ROLES]))
    assert calls == [(role, "idle") for role in expected]
