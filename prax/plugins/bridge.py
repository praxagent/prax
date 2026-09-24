"""Parent-side bridge manager for subprocess plugin isolation (Phase 2).

Manages one subprocess per IMPORTED plugin.  The subprocess runs
:mod:`prax.plugins.host` with a stripped environment (no API keys).

Each bridge:
  - Spawns the host subprocess lazily on first tool invocation
  - Serializes tool kwargs → sends to subprocess → deserializes result
  - Handles capability callbacks (plugin calling caps.http_get, etc.)
  - Enforces timeouts by killing the subprocess (thread-safe: no signals)
  - Is killed on reload or conversation end
"""
from __future__ import annotations

import atexit
import base64
import collections
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
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
        # Per-subprocess: messages read off its stdout by a pump thread, and
        # the tail of its stderr (drained so a chatty plugin cannot fill the
        # pipe and block).
        self._messages: queue.Queue = queue.Queue()
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=200)

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
        # Fresh channels per subprocess, so nothing from a killed one leaks
        # into the next.
        self._messages = queue.Queue()
        self._stderr_tail = collections.deque(maxlen=200)
        threading.Thread(
            target=_pump_messages, args=(self._proc.stdout, self._messages),
            name=f"plugin-stdout-{self.rel_key}", daemon=True,
        ).start()
        threading.Thread(
            target=_drain_lines, args=(self._proc.stderr, self._stderr_tail),
            name=f"plugin-stderr-{self.rel_key}", daemon=True,
        ).start()
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

        Blocks until a terminal response (ready/result/error) is received or
        *timeout* seconds pass, whichever is first; on timeout the subprocess
        is killed. If a caps_call is received, it is serviced and the
        response sent back.

        The deadline is a queue read, not ``signal.alarm``: SIGALRM handlers
        can only be installed on the main thread, and Prax serves requests
        from Flask, Discord and task-runner threads — where the alarm raised
        ValueError and no IMPORTED plugin could ever register.
        """
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Plugin subprocess {self.rel_key} timed out after {timeout}s")
                try:
                    resp = self._messages.get(timeout=remaining)
                except queue.Empty:
                    raise TimeoutError(
                        f"Plugin subprocess {self.rel_key} timed out after {timeout}s"
                    ) from None
                if resp is None:
                    stderr = "".join(self._stderr_tail)
                    raise RuntimeError(
                        f"Plugin subprocess {self.rel_key} closed unexpectedly. "
                        f"stderr: {stderr[-500:] if stderr else '(empty)'}"
                    )
                if isinstance(resp, Exception):
                    raise RuntimeError(
                        f"Plugin subprocess {self.rel_key} sent an unreadable message: {resp}"
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


def _pump_messages(stream: Any, out: queue.Queue) -> None:
    """Move JSON-lines messages from *stream* onto *out*; ``None`` marks EOF."""
    try:
        while True:
            try:
                msg = recv(stream)
            except json.JSONDecodeError as exc:
                out.put(exc)
                continue
            if msg is None:
                break
            out.put(msg)
    except (OSError, ValueError):
        pass  # pipe closed underneath us (process killed)
    finally:
        out.put(None)


def _drain_lines(stream: Any, tail: collections.deque) -> None:
    """Keep the last lines of *stream* so the pipe never fills up."""
    try:
        for line in stream:
            tail.append(line)
    except (OSError, ValueError):
        pass


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
