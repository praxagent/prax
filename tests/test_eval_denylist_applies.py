"""The eval scope applies EVAL_MODE_TOOL_DENYLIST through the registry ContextVar.

``_isolated_prax_scope`` used to replace ``tool_registry.get_registered_tools``
with a filtering wrapper. The orchestrator binds that name by from-import at
module load and the spokes never consult the registry, so the "denylist" filtered
nothing that mattered (prax/eval/README.md documented it as intent only). The
scope now sets ``tool_registry.eval_tool_denylist`` — the ContextVar every
tool-list build site filters through ``apply_eval_denylist`` — and resets it on
exit. ``tests/test_eval_denylist_contextvar.py`` covers the build sites; this
file covers the scope and the list itself.
"""
from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

from prax.agent import tool_registry
from prax.agent.user_context import current_user_id
from prax.eval._guards import EVAL_MODE_TOOL_DENYLIST
from prax.eval.gaia_single import _isolated_prax_scope


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(func=lambda x="": name, name=name, description=name)


def _names(tools) -> set[str]:
    return {t.name for t in tools}


def test_scope_sets_the_contextvar_and_a_list_built_inside_omits_source_read(tmp_path):
    assert tool_registry.eval_tool_denylist.get() == frozenset()
    original_get = tool_registry.get_registered_tools
    with _isolated_prax_scope(tmp_path / "ws", "task-0001", user_prefix="test-eval"):
        # The ContextVar holds the set …
        assert tool_registry.eval_tool_denylist.get() == frozenset(EVAL_MODE_TOOL_DENYLIST)
        # … and a tool list built from it (via the governance fixer's
        # apply_eval_denylist) omits a denylisted name. Old code: the ContextVar
        # stayed empty in here, so apply_eval_denylist was a no-op.
        kept = tool_registry.apply_eval_denylist([_tool("source_read"), _tool("get_current_datetime")])
        assert _names(kept) == {"get_current_datetime"}
        # No monkey-patch any more: the registry function is untouched.
        assert tool_registry.get_registered_tools is original_get
    # Reset on exit — the next (non-eval) turn in this context is unfiltered.
    assert tool_registry.eval_tool_denylist.get() == frozenset()
    assert tool_registry.get_registered_tools is original_get


def test_scope_resets_the_contextvar_when_the_body_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with _isolated_prax_scope(tmp_path / "ws", "task-0002", user_prefix="test-eval"):
            assert tool_registry.eval_tool_denylist.get()
            raise RuntimeError("agent crashed inside the scope")
    assert tool_registry.eval_tool_denylist.get() == frozenset()


def test_scope_restores_workspace_dir_and_unsets_the_synthetic_user(tmp_path):
    from prax.settings import settings

    before_ws = settings.workspace_dir
    assert current_user_id.get(None) is None  # nothing bound in this test context
    with _isolated_prax_scope(tmp_path / "ws", "task-0003", user_prefix="test-eval") as uid:
        assert uid == "test-eval-task-000"
        assert current_user_id.get(None) == uid
        assert settings.workspace_dir == str(tmp_path / "ws")
    assert settings.workspace_dir == before_ws
    # Old code re-set the previous user only when there was one and otherwise
    # left the synthetic eval user bound after the scope.
    assert current_user_id.get(None) is None


def test_hub_registry_built_inside_the_scope_omits_the_denylisted_hub_tools(tmp_path):
    outside = _names(tool_registry.get_registered_tools())
    assert "delegate_sysadmin" in outside  # a real hub tool, so the check is not vacuous
    with _isolated_prax_scope(tmp_path / "ws", "task-0004", user_prefix="test-eval"):
        inside = _names(tool_registry.get_registered_tools())
    assert "delegate_sysadmin" not in inside
    assert inside == outside - EVAL_MODE_TOOL_DENYLIST
    assert _names(tool_registry.get_registered_tools()) == outside  # unchanged after exit


def test_every_denylisted_name_is_a_real_tool():
    """The list must name tools that exist — ``self_improve_status`` sat in it
    for months and matched nothing. Resolves each name against the builders that
    can produce it (hub registry, sysadmin spoke, self-improve agent, codegen)."""
    from prax.agent.codegen_tools import build_codegen_tools
    from prax.agent.self_improve_agent import _build_self_improve_tools, delegate_self_improve
    from prax.agent.spokes.sysadmin.agent import build_tools as sysadmin_tools

    known = (
        _names(tool_registry.get_registered_tools())
        | _names(sysadmin_tools())
        | _names(_build_self_improve_tools())
        | _names(build_codegen_tools())
        | {delegate_self_improve.name}
    )
    missing = EVAL_MODE_TOOL_DENYLIST - known
    assert not missing, f"denylist names that resolve to no tool: {sorted(missing)}"


def test_denylist_covers_the_repo_reaching_tools():
    """The names the 2026-09 review asked for, pinned so they cannot quietly drop."""
    required = {
        "delegate_sysadmin", "source_read", "source_list", "source_grep",
        "plugin_write", "plugin_activate", "plugin_remove",
        "self_improve_start", "self_improve_deploy", "self_improve_rollback",
    }
    assert required <= EVAL_MODE_TOOL_DENYLIST
