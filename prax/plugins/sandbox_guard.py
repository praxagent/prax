"""Runtime sandbox guard for IMPORTED plugin execution.

Three defence-in-depth layers, all scoped via the ``current_plugin_trust``
context variable so they only restrict untrusted code:

1. **Audit hook** (``sys.addaudithook``) — monitors and blocks dangerous
   CPython runtime events (subprocess, socket, ctypes, os.system, etc.)
   while an IMPORTED plugin tool is executing.

2. **Import blocker** (``sys.meta_path``) — prevents IMPORTED plugins from
   importing dangerous modules (subprocess, ctypes, socket, pickle, etc.)
   at runtime.

3. **Resource limits** (``resource.setrlimit``) — caps CPU time, virtual
   memory, and open file descriptors during plugin tool execution.

None of these layers are individually unbreakable (Python's introspection
and object graph make true in-process sandboxing impossible — see the
"Glass Sandbox" problem).  Together they catch the overwhelming majority
of casual and low-effort attacks, and the audit log captures attempts
for incident response.

References:
    - PEP 578: Python Runtime Audit Hooks
    - Checkmarx, "The Glass Sandbox: Complexity of Python Sandboxing"
    - Christodorescu et al., "Systems Security Foundations for Agentic
      Computing," IEEE SAGAI 2025 (arXiv:2512.01295)
"""
from __future__ import annotations

import logging
import platform
import sys
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Audit hook — runtime event monitoring
# ---------------------------------------------------------------------------

# Events that should be blocked for untrusted plugins.
# Maps audit event name to a human-readable description.
_BLOCKED_EVENTS: dict[str, str] = {
    "subprocess.Popen": "subprocess creation",
    "os.system": "os.system shell command",
    "os.exec": "os.exec* process replacement",
    "os.spawn": "os.spawn process creation",
    "os.fork": "os.fork process creation",
    "ctypes.dlopen": "ctypes shared library loading",
    "ctypes.call_function": "ctypes foreign function call",
    "ctypes.addressof": "ctypes address-of operation",
    "shutil.rmtree": "recursive directory deletion",
}

# Events to log but not block (monitoring only).
_MONITORED_EVENTS: set[str] = {
    "socket.connect",
    "socket.__new__",
    "socket.bind",
    "open",
    "import",
}

_hook_installed = False


class PluginSecurityViolation(PermissionError):
    """Raised when a plugin attempts a blocked operation."""
    pass


def _plugin_audit_hook(event: str, args: tuple) -> None:
    """Audit hook that blocks dangerous events during IMPORTED plugin execution.

    Only active when ``current_plugin_trust`` context var is set to
    ``PluginTrust.IMPORTED``.  BUILTIN and WORKSPACE plugins are
    unrestricted.
    """
    from prax.plugins.monitored_tool import current_plugin_trust, current_plugin_rel_path
    from prax.plugins.registry import PluginTrust

    trust = current_plugin_trust.get()
    if trust != PluginTrust.IMPORTED:
        return  # Not in an IMPORTED plugin context — allow everything.

    plugin = current_plugin_rel_path.get() or "<unknown>"

    if event in _BLOCKED_EVENTS:
        desc = _BLOCKED_EVENTS[event]
        logger.critical(
            "SECURITY: Plugin '%s' attempted %s (%s) — BLOCKED",
            plugin, desc, event,
        )
        raise PluginSecurityViolation(
            f"Plugin '{plugin}' attempted {desc} — blocked by sandbox guard."
        )

    if event in _MONITORED_EVENTS:
        logger.info(
            "AUDIT: Plugin '%s' triggered event %s (args=%s)",
            plugin, event, repr(args)[:200],
        )


def install_audit_hook() -> None:
    """Install the plugin audit hook.  Safe to call multiple times."""
    global _hook_installed
    if _hook_installed:
        return
    sys.addaudithook(_plugin_audit_hook)
    _hook_installed = True
    logger.info("Plugin audit hook installed")


# ---------------------------------------------------------------------------
# 2. Import blocker — sys.meta_path finder
# ---------------------------------------------------------------------------

# Modules that IMPORTED plugins are not allowed to import.
BLOCKED_MODULES: frozenset[str] = frozenset({
    "subprocess",
    "ctypes",
    "pickle",
    "marshal",
    "shutil",
    "multiprocessing",
    "signal",
    # These are monitored but not blocked (plugins may need HTTP via caps):
    # "socket", "http", "urllib", "requests", "httpx",
})


class PluginImportBlocker:
    """A ``sys.meta_path`` finder that blocks dangerous module imports
    during IMPORTED plugin execution.

    Only active when ``current_plugin_trust`` is ``PluginTrust.IMPORTED``.
    """

    def find_module(self, fullname: str, path: object = None) -> None:
        """Raise ImportError if the module is blocked for the current plugin."""
        from prax.plugins.monitored_tool import current_plugin_trust, current_plugin_rel_path
        from prax.plugins.registry import PluginTrust

        trust = current_plugin_trust.get()
        if trust != PluginTrust.IMPORTED:
            return None  # Not in IMPORTED context — allow.

        top_level = fullname.split(".")[0]
        if top_level in BLOCKED_MODULES:
            plugin = current_plugin_rel_path.get() or "<unknown>"
            logger.warning(
                "SECURITY: Plugin '%s' tried to import '%s' — blocked",
                plugin, fullname,
            )
            raise ImportError(
                f"Plugin '{plugin}' is not permitted to import '{fullname}'."
            )
        return None  # Allow the import to proceed normally.


_import_blocker_installed = False


def install_import_blocker() -> None:
    """Install the import blocker.  Safe to call multiple times."""
    global _import_blocker_installed
    if _import_blocker_installed:
        return
    # Insert at position 0 so it's checked before the default finders.
    blocker = PluginImportBlocker()
    sys.meta_path.insert(0, blocker)
    _import_blocker_installed = True
    logger.info("Plugin import blocker installed")


# ---------------------------------------------------------------------------
# 3. Resource limits — OS-enforced via resource module
# ---------------------------------------------------------------------------

# Default limits for IMPORTED plugin tool invocations.
_DEFAULT_CPU_SECONDS = 30         # Max CPU time per invocation
_DEFAULT_MEMORY_BYTES = 512 * 1024 * 1024  # 512 MB virtual memory
_DEFAULT_MAX_FDS = 64             # Max open file descriptors


@contextmanager
def resource_limits(
    *,
    cpu_seconds: int = _DEFAULT_CPU_SECONDS,
    memory_bytes: int = _DEFAULT_MEMORY_BYTES,
    max_fds: int = _DEFAULT_MAX_FDS,
):
    """Context manager that sets OS-level resource limits.

    Limits are restored to their previous values on exit.

    Only effective on Unix/macOS — silently skipped on Windows.
    Resource limits are enforced by the kernel, not Python, making
    them harder to bypass than pure-Python mechanisms.

    .. note::
        ``RLIMIT_AS`` (virtual memory) limits can cause ``MemoryError``
        which is catchable.  This is a detection layer, not a guarantee.
        ``RLIMIT_CPU`` sends ``SIGXCPU`` which terminates the process
        if not caught — much harder to bypass.
    """
    if platform.system() == "Windows":
        yield
        return

    import resource

    limits_to_set = [
        (resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds)),
        (resource.RLIMIT_NOFILE, (max_fds, max_fds)),
    ]

    # RLIMIT_AS is not available on all platforms (e.g., some macOS versions
    # have it but enforcement is spotty).
    if hasattr(resource, "RLIMIT_AS"):
        limits_to_set.append(
            (resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        )

    # Save current limits.
    saved: list[tuple[int, tuple[int, int]]] = []
    for res, new_limit in limits_to_set:
        try:
            old_soft, old_hard = resource.getrlimit(res)
            saved.append((res, (old_soft, old_hard)))
            # Only tighten the soft limit — leave the hard limit untouched
            # so we can restore the original soft limit when done.
            # (Unprivileged processes can only lower hard limits, never raise them.)
            effective_soft = min(new_limit[0], old_soft) if old_soft != resource.RLIM_INFINITY else new_limit[0]
            resource.setrlimit(res, (effective_soft, old_hard))
        except (ValueError, OSError) as exc:
            logger.debug("Could not set %s: %s", res, exc)

    try:
        yield
    finally:
        # Restore previous limits.
        for res, old_limit in saved:
            try:
                resource.setrlimit(res, old_limit)
            except (ValueError, OSError):
                pass  # Best-effort restore.


# ---------------------------------------------------------------------------
# 4. Convenience: install all guards at startup
# ---------------------------------------------------------------------------

_all_installed = False


def install_all_guards() -> None:
    """Install audit hook + import blocker.  Called once at app startup.

    Resource limits are applied per-invocation via ``resource_limits()``
    context manager in the monitored tool wrapper.
    """
    global _all_installed
    if _all_installed:
        return
    install_audit_hook()
    install_import_blocker()
    _all_installed = True
