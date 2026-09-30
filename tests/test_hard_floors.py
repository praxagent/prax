"""Hard floors: a person decides each floor action, and nothing lowers the floor.

Pattern credit: OpenWorker (Andrew Ng et al.) — "dangerous operations are
human-only, always".
"""
from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

import prax.agent.governed_tool as gov
import prax.settings as prax_settings
from prax.agent import hard_floors, human_approval
from prax.agent.user_context import current_user_message


@pytest.fixture(autouse=True)
def _floors_on(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "hard_floors_enabled", True)
    monkeypatch.setattr(prax_settings.settings, "hard_floor_extra_tools", "")
    monkeypatch.setattr(prax_settings.settings, "out_of_band_approvals_enabled", False)
    gov.begin_turn()
    yield
    gov.drain_audit_log()


@pytest.fixture
def said():
    tokens = []

    def _say(text: str):
        tokens.append(current_user_message.set(text))
    yield _say
    for t in reversed(tokens):
        current_user_message.reset(t)


def _tool(name: str, calls: list, *, enforce: bool):
    def fn(domain: str = "", name: str = "") -> str:
        calls.append((domain, name))
        return "ran"
    return gov.wrap_with_governance(
        StructuredTool.from_function(func=fn, name=name, description="t"),
        layer="spoke", enforce=enforce)


# --- the gate ------------------------------------------------------------------

@pytest.mark.parametrize("enforce", [True, False])
def test_a_floor_action_is_refused_without_a_person_even_when_spokes_are_unenforced(enforce, said):
    calls: list = []
    login = _tool("browser_login", calls, enforce=enforce)
    said("summarise my inbox")
    out = login.invoke({"domain": "leetcode.com"})
    assert out.startswith("⛔ Not done: browser_login for leetcode.com is a hard-floor action")
    assert 'yes, log in to leetcode.com' in out
    assert calls == []


def test_the_model_calling_again_does_not_confirm(said):
    calls: list = []
    login = _tool("browser_request_login", calls, enforce=True)
    said("solve the leetcode problem")  # no request to log in
    for _ in range(3):
        assert login.invoke({"domain": "leetcode.com"}).startswith("⛔")
    assert calls == []


def test_the_users_own_message_naming_action_and_target_allows_that_call(said):
    calls: list = []
    said("please log in to LeetCode so you can submit")
    # Tools capture the user's message when built — per delegation in real runs.
    login = _tool("browser_request_login", calls, enforce=True)
    assert login.invoke({"domain": "https://www.leetcode.com/problems/x"}) == "ran"
    # ...and only for the site the user named.
    assert login.invoke({"domain": "github.com"}).startswith("⛔")
    assert calls == [("https://www.leetcode.com/problems/x", "")]


def test_a_generic_yes_is_not_enough(said):
    login = _tool("browser_fill_login", [], enforce=True)
    said("yes go ahead")
    assert login.invoke({"domain": "leetcode.com"}).startswith("⛔")


def test_earned_trust_cannot_lower_a_floor(monkeypatch, said):
    from prax.agent import earned_trust

    monkeypatch.setattr(earned_trust, "get_trust_adjustments", lambda component: earned_trust.TrustAdjustments(
        risk_downgrade_eligible={"browser_request_login", "browser_finish_login"}))
    said("check my submissions")
    assert _tool("browser_finish_login", [], enforce=True).invoke({"domain": "leetcode.com"}).startswith("⛔")


def test_plugin_install_needs_the_plugin_named(said):
    calls: list = []
    said("install the weather plugin")
    install = _tool("plugin_import", calls, enforce=True)
    assert install.invoke({"name": "weather"}) == "ran"
    assert install.invoke({"name": "crypto-miner"}).startswith("⛔")


def test_non_floor_tools_are_untouched(said):
    said("anything")
    assert _tool("browser_navigate", [], enforce=True).invoke({"domain": "x.com"}) == "ran"


def test_floors_off_keeps_the_prior_behaviour(monkeypatch, said):
    monkeypatch.setattr(prax_settings.settings, "hard_floors_enabled", False)
    said("summarise my inbox")
    # browser_login is unclassified -> MEDIUM -> runs, as before.
    assert _tool("browser_login", [], enforce=True).invoke({"domain": "leetcode.com"}) == "ran"


def test_operators_can_add_floors_but_not_remove_them(monkeypatch, said):
    monkeypatch.setattr(prax_settings.settings, "hard_floor_extra_tools", "workspace_send_file, ")
    assert hard_floors.is_floor("workspace_send_file")
    assert hard_floors.is_floor("browser_login")  # built-ins stay
    said("send me the file")
    # An operator-added floor has no chat fallback: TeamWork approval only.
    assert _tool("workspace_send_file", [], enforce=True).invoke({"name": "a.pdf"}).startswith("⛔")


# --- with out-of-band approvals ---------------------------------------------------

@pytest.fixture
def approvals(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "out_of_band_approvals_enabled", True)
    asked = []

    def decide(result):
        def fake(tool_name, kwargs, *, kind, reason, summary, **_):
            asked.append((tool_name, kind))
            return result
        monkeypatch.setattr(human_approval, "request", fake)
    return asked, decide


def test_a_persons_approval_runs_that_exact_call_once(approvals, said):
    asked, decide = approvals
    decide(human_approval.Decision(True, "", "ap1", "tj"))
    calls: list = []
    login = _tool("browser_fill_login", calls, enforce=True)
    said("do the thing")
    assert login.invoke({"domain": "leetcode.com"}) == "ran"
    assert asked == [("browser_fill_login", "hard_floor")]
    assert login.invoke({"domain": "leetcode.com"}) == "ran"  # same call: already decided
    assert len(asked) == 1


def test_a_timed_grant_cannot_approve_a_floor(approvals, said):
    _, decide = approvals
    decide(human_approval.Decision(True, "", "ap2", "grant:g1"))
    calls: list = []
    out = _tool("browser_fill_login", calls, enforce=True).invoke({"domain": "leetcode.com"})
    assert out.startswith("⛔") and "approve it in TeamWork" in out
    assert calls == []
    audit = gov.drain_audit_log()
    assert any("timed grant can't approve" in str(e) for e in audit)


def test_a_refusal_in_teamwork_refuses(approvals, said):
    _, decide = approvals
    decide(human_approval.Decision(False, "Refused by the user.", "ap3"))
    assert _tool("gpu_power_on", [], enforce=True).invoke({"name": "a100"}).startswith("⛔")


# --- the matcher ---------------------------------------------------------------------

@pytest.mark.parametrize("msg, kwargs, ok", [
    ("log in to leetcode.com", {"domain": "leetcode.com"}, True),
    ("sign in on LeetCode please", {"domain": "https://leetcode.com/login"}, True),
    ("leetcode is great", {"domain": "leetcode.com"}, False),             # no verb
    ("log in", {"domain": "leetcode.com"}, False),                        # no target
    ("log in to github", {"domain": "leetcode.com"}, False),              # other target
    ("log in to leetcodefan.net", {"domain": "leetcode.com"}, False),     # prefix isn't the site
])
def test_login_matcher(msg, kwargs, ok):
    assert hard_floors.user_named_it("browser_request_login", kwargs, msg) is ok
