"""Tests for Phase 2 subprocess plugin isolation (bridge, host, rpc)."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from io import StringIO
from unittest.mock import MagicMock

import pytest

from prax.plugins.rpc import (
    msg_caps_call,
    msg_caps_result,
    msg_error,
    msg_invoke,
    msg_ready,
    msg_register,
    msg_result,
    msg_shutdown,
    recv,
    send,
    tool_to_metadata,
)

# ---------------------------------------------------------------------------
# RPC protocol tests
# ---------------------------------------------------------------------------

class TestRPC:
    def test_send_recv_roundtrip(self):
        buf = StringIO()
        msg = {"type": "invoke", "tool_name": "foo", "kwargs": {"x": 1}}
        send(buf, msg)
        buf.seek(0)
        result = recv(buf)
        assert result == msg

    def test_recv_eof(self):
        buf = StringIO("")
        assert recv(buf) is None

    def test_msg_constructors(self):
        assert msg_register("/path", "key", "imported")["type"] == "register"
        assert msg_invoke("tool", {"x": 1})["type"] == "invoke"
        assert msg_shutdown()["type"] == "shutdown"
        assert msg_ready([])["type"] == "ready"
        assert msg_result("ok")["type"] == "result"
        assert msg_error("fail")["type"] == "error"
        assert msg_caps_call("http_get", [], {})["type"] == "caps_call"
        assert msg_caps_result("data")["type"] == "caps_result"

    def test_tool_to_metadata(self):
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"
        mock_tool.args_schema = None
        meta = tool_to_metadata(mock_tool)
        assert meta["name"] == "test_tool"
        assert meta["description"] == "A test tool"
        assert meta["args_schema"] == {}


# ---------------------------------------------------------------------------
# Host subprocess tests (integration)
# ---------------------------------------------------------------------------

class TestHost:
    def _make_plugin(self, tmp_path, code):
        """Write a plugin to tmp_path and return its path."""
        plugin_dir = tmp_path / "tools" / "test_plugin"
        plugin_dir.mkdir(parents=True)
        plugin_file = plugin_dir / "plugin.py"
        plugin_file.write_text(code)
        return str(plugin_file)

    def test_host_register_and_invoke(self, tmp_path):
        """Test the full host lifecycle: register → invoke → shutdown."""
        plugin_path = self._make_plugin(tmp_path, textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def greet(name: str) -> str:
                \"\"\"Greet someone.\"\"\"
                return f"Hello, {name}!"

            def register():
                return [greet]
        """))

        import os
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        }

        proc = subprocess.Popen(
            [sys.executable, "-m", "prax.plugins.host"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        try:
            # Register
            register_msg = json.dumps(msg_register(plugin_path, "test_plugin", "imported")) + "\n"
            proc.stdin.write(register_msg)
            proc.stdin.flush()

            line = proc.stdout.readline()
            resp = json.loads(line)
            assert resp["type"] == "ready"
            assert len(resp["tools"]) == 1
            assert resp["tools"][0]["name"] == "greet"

            # Invoke
            invoke_msg = json.dumps(msg_invoke("greet", {"name": "World"})) + "\n"
            proc.stdin.write(invoke_msg)
            proc.stdin.flush()

            line = proc.stdout.readline()
            resp = json.loads(line)
            assert resp["type"] == "result"
            assert resp["value"] == "Hello, World!"

            # Shutdown
            shutdown_msg = json.dumps(msg_shutdown()) + "\n"
            proc.stdin.write(shutdown_msg)
            proc.stdin.flush()
            proc.wait(timeout=5)
            assert proc.returncode == 0
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_host_invoke_unknown_tool(self, tmp_path):
        """Invoking a non-existent tool returns an error."""
        plugin_path = self._make_plugin(tmp_path, textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def dummy(x: str) -> str:
                \"\"\"Dummy.\"\"\"
                return x

            def register():
                return [dummy]
        """))

        import os
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        }

        proc = subprocess.Popen(
            [sys.executable, "-m", "prax.plugins.host"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        try:
            # Register
            proc.stdin.write(json.dumps(msg_register(plugin_path, "test", "imported")) + "\n")
            proc.stdin.flush()
            proc.stdout.readline()  # consume ready

            # Invoke unknown tool
            proc.stdin.write(json.dumps(msg_invoke("nonexistent", {})) + "\n")
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline())
            assert resp["type"] == "error"
            assert "Unknown tool" in resp["message"]

            proc.stdin.write(json.dumps(msg_shutdown()) + "\n")
            proc.stdin.flush()
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_host_caps_callback(self, tmp_path):
        """Plugin calls caps.get_config() which triggers a callback to parent."""
        plugin_path = self._make_plugin(tmp_path, textwrap.dedent("""\
            from langchain_core.tools import tool

            def register(caps):
                @tool
                def config_reader(key: str) -> str:
                    \"\"\"Read a config value via capabilities.\"\"\"
                    val = caps.get_config(key)
                    return f"config={val}"
                return [config_reader]
        """))

        import os
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        }

        proc = subprocess.Popen(
            [sys.executable, "-m", "prax.plugins.host"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        try:
            # Register
            proc.stdin.write(json.dumps(msg_register(plugin_path, "test", "imported")) + "\n")
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline())
            assert resp["type"] == "ready"

            # Invoke — this will trigger a caps_call
            proc.stdin.write(json.dumps(msg_invoke("config_reader", {"key": "workspace_dir"})) + "\n")
            proc.stdin.flush()

            # Read the caps_call from the subprocess
            caps_msg = json.loads(proc.stdout.readline())
            assert caps_msg["type"] == "caps_call"
            assert caps_msg["method"] == "get_config"

            # Respond with the caps_result
            proc.stdin.write(json.dumps(msg_caps_result("/tmp/workspace")) + "\n")
            proc.stdin.flush()

            # Now read the tool result
            result = json.loads(proc.stdout.readline())
            assert result["type"] == "result"
            assert result["value"] == "config=/tmp/workspace"

            proc.stdin.write(json.dumps(msg_shutdown()) + "\n")
            proc.stdin.flush()
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()


# ---------------------------------------------------------------------------
# Bridge tests
# ---------------------------------------------------------------------------

class TestBridge:
    def _make_plugin(self, tmp_path, code):
        plugin_dir = tmp_path / "tools" / "bridge_test"
        plugin_dir.mkdir(parents=True)
        plugin_file = plugin_dir / "plugin.py"
        plugin_file.write_text(code)
        return str(plugin_file)

    def test_bridge_register_and_invoke(self, tmp_path):
        """Full bridge lifecycle: register + invoke + shutdown."""
        from prax.plugins.bridge import PluginBridge

        plugin_path = self._make_plugin(tmp_path, textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def add(a: str, b: str) -> str:
                \"\"\"Add two numbers.\"\"\"
                return str(int(a) + int(b))

            def register():
                return [add]
        """))

        bridge = PluginBridge("bridge_test")
        try:
            tools = bridge.register(plugin_path, "imported")
            assert len(tools) == 1
            assert tools[0]["name"] == "add"

            result = bridge.invoke("add", {"a": "3", "b": "4"}, timeout=10)
            assert result == "7"
        finally:
            bridge.shutdown()

    def test_bridge_caps_callback(self, tmp_path):
        """Bridge services capability callbacks from the subprocess."""
        from prax.plugins.bridge import PluginBridge

        plugin_path = self._make_plugin(tmp_path, textwrap.dedent("""\
            from langchain_core.tools import tool

            def register(caps):
                @tool
                def get_uid(x: str) -> str:
                    \"\"\"Get user ID via caps.\"\"\"
                    uid = caps.get_user_id()
                    return f"uid={uid}"
                return [get_uid]
        """))

        mock_caps = MagicMock()
        mock_caps.get_user_id.return_value = "user_123"

        bridge = PluginBridge("caps_test")
        try:
            tools = bridge.register(plugin_path, "imported", caps=mock_caps)
            assert len(tools) == 1

            result = bridge.invoke("get_uid", {"x": "test"}, timeout=10)
            assert result == "uid=user_123"
            mock_caps.get_user_id.assert_called()
        finally:
            bridge.shutdown()

    def test_bridge_subprocess_error(self, tmp_path):
        """Tool that raises an exception returns an error through the bridge."""
        from prax.plugins.bridge import PluginBridge

        plugin_path = self._make_plugin(tmp_path, textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def explode(x: str) -> str:
                \"\"\"Blow up.\"\"\"
                raise ValueError("boom")

            def register():
                return [explode]
        """))

        bridge = PluginBridge("error_test")
        try:
            bridge.register(plugin_path, "imported")
            with pytest.raises(RuntimeError, match="subprocess error"):
                bridge.invoke("explode", {"x": "test"}, timeout=10)
        finally:
            bridge.shutdown()

    def test_bridge_shutdown_kills_subprocess(self, tmp_path):
        """Shutdown terminates the subprocess."""
        from prax.plugins.bridge import PluginBridge

        plugin_path = self._make_plugin(tmp_path, textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def noop(x: str) -> str:
                \"\"\"Noop.\"\"\"
                return x

            def register():
                return [noop]
        """))

        bridge = PluginBridge("shutdown_test")
        bridge.register(plugin_path, "imported")
        assert bridge.is_alive

        bridge.shutdown()
        assert not bridge.is_alive


# ---------------------------------------------------------------------------
# Security isolation tests
# ---------------------------------------------------------------------------

class TestSubprocessIsolation:
    def _make_plugin(self, tmp_path, code):
        plugin_dir = tmp_path / "tools" / "security_test"
        plugin_dir.mkdir(parents=True)
        plugin_file = plugin_dir / "plugin.py"
        plugin_file.write_text(code)
        return str(plugin_file)

    def test_subprocess_cannot_read_env_secrets(self, tmp_path):
        """Plugin in subprocess cannot see parent's OPENAI_KEY."""
        from prax.plugins.bridge import PluginBridge

        plugin_path = self._make_plugin(tmp_path, textwrap.dedent("""\
            import os
            from langchain_core.tools import tool

            @tool
            def read_env(key: str) -> str:
                \"\"\"Read an env var.\"\"\"
                return os.environ.get(key, "NOT_FOUND")

            def register():
                return [read_env]
        """))

        bridge = PluginBridge("env_test")
        try:
            bridge.register(plugin_path, "imported")
            result = bridge.invoke("read_env", {"key": "OPENAI_KEY"}, timeout=10)
            assert result == "NOT_FOUND"

            result = bridge.invoke("read_env", {"key": "ANTHROPIC_KEY"}, timeout=10)
            assert result == "NOT_FOUND"
        finally:
            bridge.shutdown()

    def test_subprocess_has_path(self, tmp_path):
        """Subprocess still has basic env vars like PATH."""
        from prax.plugins.bridge import PluginBridge

        plugin_path = self._make_plugin(tmp_path, textwrap.dedent("""\
            import os
            from langchain_core.tools import tool

            @tool
            def read_env(key: str) -> str:
                \"\"\"Read an env var.\"\"\"
                return os.environ.get(key, "NOT_FOUND")

            def register():
                return [read_env]
        """))

        bridge = PluginBridge("path_test")
        try:
            bridge.register(plugin_path, "imported")
            result = bridge.invoke("read_env", {"key": "PATH"}, timeout=10)
            assert result != "NOT_FOUND"
            assert len(result) > 0
        finally:
            bridge.shutdown()


# ---------------------------------------------------------------------------
# Loader integration tests
# ---------------------------------------------------------------------------

class TestLoaderBridgeIntegration:
    def test_proxy_tool_creation(self, tmp_path):
        """_make_proxy_tool creates a callable StructuredTool."""
        from prax.plugins.loader import PluginLoader

        meta = {
            "name": "test_tool",
            "description": "A test tool",
            "args_schema": {
                "properties": {
                    "x": {"type": "string"},
                    "y": {"type": "integer", "default": 0},
                },
                "required": ["x"],
            },
        }

        tool = PluginLoader._make_proxy_tool(meta, "test_plugin", "imported")
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        # Verify args_schema has the right fields
        schema = tool.args_schema.model_json_schema()
        assert "x" in schema["properties"]
        assert "y" in schema["properties"]

    def test_load_imported_via_bridge(self, tmp_path):
        """PluginLoader loads IMPORTED plugins via subprocess bridge."""
        from prax.plugins.loader import PluginLoader
        from prax.plugins.registry import PluginRegistry

        plugin_dir = tmp_path / "tools" / "imported_test"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.py").write_text(textwrap.dedent("""\
            from langchain_core.tools import tool

            PLUGIN_VERSION = "1"

            @tool
            def imported_tool(x: str) -> str:
                \"\"\"An imported tool.\"\"\"
                return f"result={x}"

            def register():
                return [imported_tool]
        """))

        registry = PluginRegistry(registry_path=str(tmp_path / "reg.json"))
        loader = PluginLoader(registry=registry)

        loaded = loader._load_imported_via_bridge(
            plugin_dir / "plugin.py",
            "imported_test",
            "imported",
            set(),
            set(),
        )

        from prax.plugins.bridge import shutdown_bridge
        try:
            assert len(loaded) == 1
            tool, name = loaded[0]
            assert name == "imported_tool"
        finally:
            shutdown_bridge("imported_test")
