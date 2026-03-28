"""Parent-side bridge manager for subprocess plugin isolation (Phase 2).

Manages one subprocess per IMPORTED plugin.  The subprocess runs
:mod:`prax.plugins.host` with a stripped environment (no API keys).

Each bridge:
  - Spawns the host subprocess lazily on first tool invocation
  - Serializes tool kwargs → sends to subprocess → deserializes result
  - Handles capability callbacks (plugin calling caps.http_get, etc.)
  - Enforces timeouts with SIGTERM → SIGKILL escalation
  - Is killed on reload or conversation end
"""
from __future__ import annotations

import atexit
import base64
import logging
import os
import subprocess
import sys
import threading
from typing import Any

from prax.plugins.rpc import (
    msg_caps_error,
    msg_caps_result,
    msg_invoke,
    msg_register,
    msg_shutdown,
    recv,
    send,
)

logger = logging.getLogger(__name__)

# Stripped environment for the subprocess — no API keys, no secrets.
_SAFE_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
    "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
}


class PluginBridge:
    """Manages a single subprocess for an IMPORTED plugin.

    The subprocess is spawned lazily on the first ``register()`` call
    and kept alive until ``shutdown()`` is called.
    """

    def __init__(self, rel_key: str) -> None:
        self.rel_key = rel_key
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._tool_metadata: list[dict] = []
        self._caps: Any = None  # PluginCapabilities for servicing callbacks

    def _ensure_started(self) -> subprocess.Popen:
        """Start the subprocess if not already running."""
        if self._proc is not None and self._proc.poll() is None:
            return self._proc

        self._proc = subprocess.Popen(
            [sys.executable, "-m", "prax.plugins.host"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_SAFE_ENV,
        )
        logger.info("Started plugin host subprocess for %s (pid=%d)", self.rel_key, self._proc.pid)
        return self._proc

    def register(
        self,
        plugin_path: str,
        trust_tier: str,
        caps: Any = None,
    ) -> list[dict]:
        """Send a register message to the subprocess and return tool metadata.

        Args:
            plugin_path: Absolute path to the plugin.py file.
            trust_tier: Trust tier string.
            caps: A PluginCapabilities instance for servicing capability callbacks.

        Returns:
            List of tool metadata dicts with ``name``, ``description``, ``args_schema``.
        """
        with self._lock:
            self._caps = caps
            proc = self._ensure_started()
            send(proc.stdin, msg_register(plugin_path, self.rel_key, trust_tier))
            return self._read_response(proc, timeout=30)

    def invoke(self, tool_name: str, kwargs: dict, *, timeout: int = 30) -> str:
        """Invoke a tool in the subprocess.

        Returns the tool's string result.
        Raises RuntimeError on subprocess errors or timeout.
        """
        with self._lock:
            proc = self._ensure_started()
            send(proc.stdin, msg_invoke(tool_name, kwargs))
            result = self._read_response(proc, timeout=timeout)
            return result

    def _read_response(self, proc: subprocess.Popen, *, timeout: int = 30) -> Any:
        """Read messages from the subprocess, handling caps callbacks along the way.

        Blocks until a terminal response (ready/result/error) is received.
        If a caps_call is received, it is serviced and the response sent back.
        """
        import signal

        def _alarm_handler(signum, frame):
            raise TimeoutError(f"Plugin subprocess {self.rel_key} timed out after {timeout}s")

        # Set alarm for timeout (Unix only).
        old_handler = None
        if hasattr(signal, "SIGALRM"):
            old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(timeout)

        try:
            while True:
                resp = recv(proc.stdout)
                if resp is None:
                    stderr = ""
                    if proc.stderr:
                        try:
                            stderr = proc.stderr.read()
                        except Exception:
                            pass
                    raise RuntimeError(
                        f"Plugin subprocess {self.rel_key} closed unexpectedly. "
                        f"stderr: {stderr[-500:] if stderr else '(empty)'}"
                    )

                msg_type = resp.get("type")

                if msg_type == "caps_call":
                    # Service the capability callback.
                    self._handle_caps_call(proc, resp)
                    continue

                if msg_type == "ready":
                    self._tool_metadata = resp.get("tools", [])
                    return self._tool_metadata

                if msg_type == "result":
                    return resp.get("value")

                if msg_type == "error":
                    tb = resp.get("traceback", "")
                    if tb:
                        logger.warning(
                            "Plugin %s subprocess error:\n%s", self.rel_key, tb,
                        )
                    raise RuntimeError(
                        f"Plugin subprocess error ({self.rel_key}): {resp.get('message')}"
                    )

                logger.warning("Unknown message from plugin subprocess: %s", msg_type)

        except TimeoutError:
            self._kill_proc()
            raise
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
                if old_handler is not None:
                    signal.signal(signal.SIGALRM, old_handler)

    def _handle_caps_call(self, proc: subprocess.Popen, msg: dict) -> None:
        """Service a capability callback from the subprocess."""
        method = msg.get("method", "")
        args = msg.get("args", [])
        kwargs = msg.get("kwargs", {})

        # Decode bytes arguments.
        args = [
            base64.b64decode(a["__bytes__"]) if isinstance(a, dict) and "__bytes__" in a else a
            for a in args
        ]
        kwargs = {
            k: base64.b64decode(v["__bytes__"]) if isinstance(v, dict) and "__bytes__" in v else v
            for k, v in kwargs.items()
        }

        if self._caps is None:
            send(proc.stdin, msg_caps_error("No capabilities context available"))
            return

        try:
            fn = getattr(self._caps, method, None)
            if fn is None or method.startswith("_"):
                send(proc.stdin, msg_caps_error(f"Unknown capability method: {method}"))
                return

            result = fn(*args, **kwargs)

            # Serialize special return types.
            if hasattr(result, "status_code"):
                # requests.Response
                result = {
                    "status_code": result.status_code,
                    "text": result.text,
                    "headers": dict(result.headers),
                }
            elif hasattr(result, "returncode"):
                # subprocess.CompletedProcess
                result = {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }

            send(proc.stdin, msg_caps_result(result))

        except Exception as exc:
            send(proc.stdin, msg_caps_error(str(exc)))

    def shutdown(self) -> None:
        """Shut down the subprocess gracefully, with SIGKILL fallback."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = None
                return
            try:
                send(self._proc.stdin, msg_shutdown())
                self._proc.wait(timeout=5)
            except Exception:
                self._kill_proc()
            self._proc = None

    def _kill_proc(self) -> None:
        """Force-kill the subprocess."""
        if self._proc and self._proc.poll() is None:
            logger.warning("Force-killing plugin subprocess %s (pid=%d)", self.rel_key, self._proc.pid)
            self._proc.kill()
            try:
                self._proc.wait(timeout=5)
            except Exception:
                pass

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


# ---------------------------------------------------------------------------
# Global bridge registry
# ---------------------------------------------------------------------------

_bridges: dict[str, PluginBridge] = {}
_bridges_lock = threading.Lock()


def get_bridge(rel_key: str) -> PluginBridge:
    """Return (or create) the bridge for a plugin."""
    with _bridges_lock:
        if rel_key not in _bridges:
            _bridges[rel_key] = PluginBridge(rel_key)
        return _bridges[rel_key]


def shutdown_bridge(rel_key: str) -> None:
    """Shut down a specific plugin's bridge."""
    with _bridges_lock:
        bridge = _bridges.pop(rel_key, None)
    if bridge:
        bridge.shutdown()


def shutdown_all_bridges() -> None:
    """Shut down all plugin bridges.  Called on process exit or reload."""
    with _bridges_lock:
        keys = list(_bridges.keys())
    for key in keys:
        shutdown_bridge(key)


# Ensure all subprocesses are cleaned up on exit.
atexit.register(shutdown_all_bridges)
