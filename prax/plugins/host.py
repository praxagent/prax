"""Subprocess plugin host — child-side entry point for Phase 2 isolation.

Runs in an isolated subprocess with a stripped environment (no API keys).
Communicates with the parent process via JSON-lines on stdin/stdout.

Lifecycle:
  1. Parent sends ``register`` → host imports the plugin, calls register(caps),
     responds with tool metadata.
  2. Parent sends ``invoke`` → host calls the named tool, responds with result.
  3. Parent sends ``shutdown`` → host exits.

Capability callbacks: when the plugin calls a PluginCapabilities method
(e.g., ``caps.http_get(url)``), the proxy sends a ``caps_call`` message to
the parent, blocks until the parent replies with ``caps_result``, and
returns the value to the plugin.
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
import traceback
from typing import Any

# Redirect all logging to stderr so stdout is reserved for JSON-RPC.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logger = logging.getLogger(__name__)


def _import_plugin(plugin_path: str):
    """Import a plugin module from an absolute path."""
    spec = importlib.util.spec_from_file_location("plugin_module", plugin_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create spec for {plugin_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CapsProxy:
    """PluginCapabilities proxy that forwards method calls to the parent process.

    When the plugin calls ``caps.http_get(url)``, this proxy serializes the
    call, sends it to the parent over stdout, and blocks on stdin for the
    result.  The plugin experiences a normal synchronous method call.
    """

    def __init__(self, plugin_rel_path: str, trust_tier: str) -> None:
        self.plugin_rel_path = plugin_rel_path
        self.trust_tier = trust_tier

    def _call_parent(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Send a caps_call to the parent and wait for the response."""
        # Serialize bytes as base64 for JSON transport.
        import base64

        from prax.plugins.rpc import msg_caps_call, recv, send
        clean_args = []
        for a in args:
            if isinstance(a, bytes):
                clean_args.append({"__bytes__": base64.b64encode(a).decode()})
            else:
                clean_args.append(a)
        clean_kwargs = {}
        for k, v in kwargs.items():
            if isinstance(v, bytes):
                clean_kwargs[k] = {"__bytes__": base64.b64encode(v).decode()}
            else:
                clean_kwargs[k] = v

        send(sys.stdout, msg_caps_call(method, clean_args, clean_kwargs))
        resp = recv(sys.stdin)
        if resp is None:
            raise RuntimeError("Parent process closed connection during caps_call")
        if resp.get("type") == "caps_error":
            raise PermissionError(resp.get("message", "Capability call denied"))
        return resp.get("value")

    # -- Proxied methods matching PluginCapabilities interface --

    def build_llm(self, tier: str = "medium") -> Any:
        return self._call_parent("build_llm", tier=tier)

    def http_get(self, url: str, **kwargs: Any) -> Any:
        result = self._call_parent("http_get", url, **kwargs)
        return _DictResponse(result) if isinstance(result, dict) else result

    def http_post(self, url: str, **kwargs: Any) -> Any:
        result = self._call_parent("http_post", url, **kwargs)
        return _DictResponse(result) if isinstance(result, dict) else result

    def save_file(self, filename: str, content: bytes) -> str:
        return self._call_parent("save_file", filename, content)

    def read_file(self, filename: str) -> str:
        return self._call_parent("read_file", filename)

    def workspace_path(self, *parts: str) -> str:
        return self._call_parent("workspace_path", *parts)

    def get_user_id(self) -> str | None:
        return self._call_parent("get_user_id")

    def run_command(self, cmd: list[str], *, timeout: int = 30, cwd: str | None = None) -> Any:
        result = self._call_parent("run_command", cmd, timeout=timeout, cwd=cwd)
        return _DictCompletedProcess(result) if isinstance(result, dict) else result

    def shared_tempdir(self, prefix: str = "prax_") -> str:
        return self._call_parent("shared_tempdir", prefix)

    def tts_synthesize(
        self, text: str, output_path: str, voice: str = "nova", provider: str = "openai"
    ) -> str:
        return self._call_parent("tts_synthesize", text, output_path, voice=voice, provider=provider)

    def get_config(self, key: str) -> str | None:
        return self._call_parent("get_config", key)

    def get_approved_secret(self, env_key: str) -> str | None:
        return self._call_parent("get_approved_secret", env_key)


class _DictResponse:
    """Lightweight response object reconstructed from a serialized dict."""

    def __init__(self, data: dict) -> None:
        self.status_code = data.get("status_code", 200)
        self.text = data.get("text", "")
        self.headers = data.get("headers", {})
        self.ok = 200 <= self.status_code < 400

    @property
    def content(self) -> bytes:
        return self.text.encode()

    def json(self) -> Any:
        import json
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class _DictCompletedProcess:
    """Lightweight CompletedProcess reconstructed from a serialized dict."""

    def __init__(self, data: dict) -> None:
        self.returncode = data.get("returncode", 0)
        self.stdout = data.get("stdout", "")
        self.stderr = data.get("stderr", "")


def main() -> None:
    """Main loop: read JSON-lines from stdin, dispatch, write responses to stdout."""
    from prax.plugins.rpc import msg_error, msg_ready, msg_result, recv, send, tool_to_metadata

    tools: dict[str, Any] = {}  # tool_name -> tool object

    while True:
        msg = recv(sys.stdin)
        if msg is None:
            break  # Parent closed stdin — exit.

        msg_type = msg.get("type")

        if msg_type == "register":
            try:
                plugin_path = msg["plugin_path"]
                rel_key = msg["rel_key"]
                trust_tier = msg["trust_tier"]

                mod = _import_plugin(plugin_path)
                if not hasattr(mod, "register"):
                    send(sys.stdout, msg_error("Plugin has no register() function"))
                    continue

                reg_fn = mod.register
                sig = inspect.signature(reg_fn)
                if sig.parameters:
                    caps = CapsProxy(rel_key, trust_tier)
                    plugin_tools = reg_fn(caps)
                else:
                    plugin_tools = reg_fn()

                if not isinstance(plugin_tools, list):
                    send(sys.stdout, msg_error("register() must return a list"))
                    continue

                tool_metadata = []
                for t in plugin_tools:
                    tools[t.name] = t
                    tool_metadata.append(tool_to_metadata(t))

                send(sys.stdout, msg_ready(tool_metadata))
            except Exception:
                send(sys.stdout, msg_error(
                    "Failed to register plugin",
                    traceback.format_exc(),
                ))

        elif msg_type == "invoke":
            try:
                tool_name = msg["tool_name"]
                kwargs = msg.get("kwargs", {})
                t = tools.get(tool_name)
                if t is None:
                    send(sys.stdout, msg_error(f"Unknown tool: {tool_name}"))
                    continue
                result = t.invoke(kwargs if kwargs else {})
                send(sys.stdout, msg_result(result))
            except Exception:
                send(sys.stdout, msg_error(
                    f"Tool {msg.get('tool_name', '?')} raised an exception",
                    traceback.format_exc(),
                ))

        elif msg_type == "shutdown":
            break

        else:
            send(sys.stdout, msg_error(f"Unknown message type: {msg_type}"))

    sys.exit(0)


if __name__ == "__main__":
    main()
