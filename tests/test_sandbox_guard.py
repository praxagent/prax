"""Tests for the runtime sandbox guard: audit hook, import blocker, resource limits, and call budget."""
from __future__ import annotations

import platform
import textwrap

import pytest

from prax.plugins.monitored_tool import (
    _increment_call_count,
    current_plugin_rel_path,
    current_plugin_trust,
    reset_plugin_call_counts,
)
from prax.plugins.registry import PluginTrust
from prax.plugins.sandbox_guard import (
    _BLOCKED_EVENTS,
    BLOCKED_MODULES,
    PluginImportBlocker,
    PluginSecurityViolation,
    _plugin_audit_hook,
    resource_limits,
)

# ---------------------------------------------------------------------------
# Audit hook
# ---------------------------------------------------------------------------

class TestAuditHook:
    """Test the plugin audit hook blocks dangerous events for IMPORTED plugins."""

    def test_blocks_subprocess_for_imported(self):
        path_token = current_plugin_rel_path.set("shared/evil")
        trust_token = current_plugin_trust.set(PluginTrust.IMPORTED)
        try:
            with pytest.raises(PluginSecurityViolation, match="subprocess creation"):
                _plugin_audit_hook("subprocess.Popen", ())
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_blocks_os_system_for_imported(self):
        path_token = current_plugin_rel_path.set("shared/evil")
        trust_token = current_plugin_trust.set(PluginTrust.IMPORTED)
        try:
            with pytest.raises(PluginSecurityViolation, match="os.system"):
                _plugin_audit_hook("os.system", ())
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_blocks_ctypes_for_imported(self):
        path_token = current_plugin_rel_path.set("shared/evil")
        trust_token = current_plugin_trust.set(PluginTrust.IMPORTED)
        try:
            with pytest.raises(PluginSecurityViolation, match="ctypes"):
                _plugin_audit_hook("ctypes.dlopen", ())
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_allows_subprocess_for_builtin(self):
        path_token = current_plugin_rel_path.set("pdf_reader")
        trust_token = current_plugin_trust.set(PluginTrust.BUILTIN)
        try:
            # Should not raise — BUILTIN plugins are unrestricted.
            _plugin_audit_hook("subprocess.Popen", ())
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_allows_subprocess_for_workspace(self):
        path_token = current_plugin_rel_path.set("custom/my_plugin")
        trust_token = current_plugin_trust.set(PluginTrust.WORKSPACE)
        try:
            _plugin_audit_hook("subprocess.Popen", ())
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_allows_when_no_plugin_context(self):
        """When no plugin is executing, allow everything."""
        # Default context var values are None — should not block.
        _plugin_audit_hook("subprocess.Popen", ())

    def test_all_blocked_events_have_descriptions(self):
        """Every blocked event should have a human-readable description."""
        for event, desc in _BLOCKED_EVENTS.items():
            assert desc, f"Missing description for {event}"
            assert len(desc) > 5, f"Description too short for {event}: {desc}"


# ---------------------------------------------------------------------------
# Import blocker
# ---------------------------------------------------------------------------

class TestImportBlocker:
    """Test the sys.meta_path import blocker."""

    def test_blocks_subprocess_for_imported(self):
        blocker = PluginImportBlocker()
        path_token = current_plugin_rel_path.set("shared/evil")
        trust_token = current_plugin_trust.set(PluginTrust.IMPORTED)
        try:
            with pytest.raises(ImportError, match="not permitted to import"):
                blocker.find_module("subprocess")
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_blocks_ctypes_for_imported(self):
        blocker = PluginImportBlocker()
        path_token = current_plugin_rel_path.set("shared/evil")
        trust_token = current_plugin_trust.set(PluginTrust.IMPORTED)
        try:
            with pytest.raises(ImportError, match="not permitted to import"):
                blocker.find_module("ctypes")
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_blocks_pickle_for_imported(self):
        blocker = PluginImportBlocker()
        path_token = current_plugin_rel_path.set("shared/evil")
        trust_token = current_plugin_trust.set(PluginTrust.IMPORTED)
        try:
            with pytest.raises(ImportError, match="not permitted to import"):
                blocker.find_module("pickle")
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_blocks_submodule_of_blocked(self):
        """subprocess.run should also be blocked (top-level = subprocess)."""
        blocker = PluginImportBlocker()
        path_token = current_plugin_rel_path.set("shared/evil")
        trust_token = current_plugin_trust.set(PluginTrust.IMPORTED)
        try:
            with pytest.raises(ImportError, match="not permitted"):
                blocker.find_module("subprocess.run")
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_allows_safe_modules_for_imported(self):
        blocker = PluginImportBlocker()
        path_token = current_plugin_rel_path.set("shared/safe")
        trust_token = current_plugin_trust.set(PluginTrust.IMPORTED)
        try:
            # These should return None (allow import to proceed).
            assert blocker.find_module("json") is None
            assert blocker.find_module("os") is None
            assert blocker.find_module("re") is None
            assert blocker.find_module("pathlib") is None
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_allows_everything_for_builtin(self):
        blocker = PluginImportBlocker()
        path_token = current_plugin_rel_path.set("pdf_reader")
        trust_token = current_plugin_trust.set(PluginTrust.BUILTIN)
        try:
            assert blocker.find_module("subprocess") is None
            assert blocker.find_module("ctypes") is None
        finally:
            current_plugin_rel_path.reset(path_token)
            current_plugin_trust.reset(trust_token)

    def test_allows_everything_when_no_context(self):
        blocker = PluginImportBlocker()
        assert blocker.find_module("subprocess") is None

    def test_blocked_modules_complete(self):
        """Verify all expected dangerous modules are in the blocklist."""
        expected = {"subprocess", "ctypes", "pickle", "marshal", "shutil", "multiprocessing", "signal"}
        assert expected.issubset(BLOCKED_MODULES)


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------

class TestResourceLimits:
    """Test the resource_limits context manager."""

    @pytest.mark.skipif(platform.system() == "Windows", reason="resource module unavailable on Windows")
    def test_limits_are_set_and_restored(self):
        import resource

        old_cpu = resource.getrlimit(resource.RLIMIT_CPU)
        old_fds = resource.getrlimit(resource.RLIMIT_NOFILE)

        # Use a high CPU limit (600s) so we don't hit cumulative CPU time
        # already consumed by earlier tests in the full suite (RLIMIT_CPU is
        # a lifetime cap, not per-call).
        with resource_limits(cpu_seconds=600, max_fds=32):
            cur_cpu = resource.getrlimit(resource.RLIMIT_CPU)
            cur_fds = resource.getrlimit(resource.RLIMIT_NOFILE)
            # Soft limits should be tightened.
            assert cur_cpu[0] <= 600
            assert cur_fds[0] <= 32

        # After exiting, limits should be restored.
        restored_cpu = resource.getrlimit(resource.RLIMIT_CPU)
        restored_fds = resource.getrlimit(resource.RLIMIT_NOFILE)
        assert restored_cpu == old_cpu
        assert restored_fds == old_fds

    @pytest.mark.skipif(platform.system() == "Windows", reason="resource module unavailable on Windows")
    def test_does_not_loosen_existing_limits(self):
        """If existing limit is tighter than requested, keep the existing one."""
        import resource

        # Set a tight FD limit.
        old = resource.getrlimit(resource.RLIMIT_NOFILE)
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (16, old[1]))
            with resource_limits(max_fds=100):
                cur = resource.getrlimit(resource.RLIMIT_NOFILE)
                # Should keep the tighter limit (16), not loosen to 100.
                assert cur[0] <= 16
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, old)

    def test_windows_is_noop(self):
        """On Windows, resource_limits should be a no-op."""
        if platform.system() != "Windows":
            pytest.skip("Only relevant on Windows")
        # Should not raise.
        with resource_limits():
            pass


# ---------------------------------------------------------------------------
# Per-plugin call budget
# ---------------------------------------------------------------------------

class TestCallBudget:
    def test_increment_counts(self):
        reset_plugin_call_counts()
        assert _increment_call_count("plugin_a") == 1
        assert _increment_call_count("plugin_a") == 2
        assert _increment_call_count("plugin_b") == 1
        assert _increment_call_count("plugin_a") == 3

    def test_reset_clears_all(self):
        _increment_call_count("x")
        _increment_call_count("y")
        reset_plugin_call_counts()
        assert _increment_call_count("x") == 1

    def test_budget_enforced_in_monitored_tool(self, tmp_path):
        """When a plugin exceeds its call budget, further calls are blocked."""
        from prax.plugins.loader import PluginLoader
        from prax.plugins.registry import PluginRegistry

        reset_plugin_call_counts()

        plugin_dir = tmp_path / "tools" / "budget_test"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.py").write_text(textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def budget_tool(x: str) -> str:
                \"\"\"A test tool.\"\"\"
                return x

            def register():
                return [budget_tool]
        """))

        registry = PluginRegistry(registry_path=str(tmp_path / "reg.json"))
        loader = PluginLoader(registry=registry)

        # Simulate the tool being loaded as IMPORTED.
        mod = loader._import_plugin(plugin_dir / "plugin.py", trust_tier=PluginTrust.IMPORTED)
        tools = mod.register()

        from prax.plugins.monitored_tool import wrap_with_monitoring
        monitored = wrap_with_monitoring(tools[0], "shared/budget_test", trust_tier=PluginTrust.IMPORTED)

        # Pre-fill the call count to be at the limit.
        import prax.plugins.loader as loader_mod
        original_loader = loader_mod._loader
        loader_mod._loader = loader
        try:
            # Set count to max (10 = default limit).
            for _ in range(10):
                _increment_call_count("shared/budget_test")

            # The 11th call should be blocked.
            with pytest.raises(PermissionError, match="exceeded its call budget"):
                monitored.invoke({"x": "test"})
        finally:
            loader_mod._loader = original_loader
            reset_plugin_call_counts()
