"""Plugin loader — discovers, imports, and hot-swaps plugin modules.

The orchestrator calls ``get_tools()`` on every agent invocation.
When a plugin is added or swapped, the version counter increments
so the orchestrator knows to rebuild its agent graph.

Supports two plugin layouts:
  - **Folder-based** (preferred): ``tools/<name>/plugin.py``
  - **Flat** (legacy): ``tools/<name>.py``

Also scans the external plugin repository if configured.
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import threading
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from prax.plugins.capabilities import PluginCapabilities
from prax.plugins.monitored_tool import wrap_with_monitoring
from prax.plugins.registry import PluginRegistry, PluginTrust
from prax.plugins.restricted_env import restricted_import_env
from prax.plugins.sandbox import sandbox_test_plugin

# Late import helper to avoid circular dependency with tools module.
_builtin_tool_names: set[str] | None = None
_builtin_lock = threading.Lock()


def _get_builtin_tool_names() -> set[str]:
    """Return the set of built-in (non-plugin) tool names, cached after first call."""
    global _builtin_tool_names
    if _builtin_tool_names is None:
        with _builtin_lock:
            if _builtin_tool_names is None:
                try:
                    from prax.agent.tools import build_default_tools
                    _builtin_tool_names = {t.name for t in build_default_tools()}
                except Exception:
                    logger.warning("Could not load built-in tool names for protection check")
                    _builtin_tool_names = set()
    return _builtin_tool_names

logger = logging.getLogger(__name__)

_PLUGINS_ROOT = Path(__file__).parent / "tools"


class PluginLoader:
    """Discovers and manages hot-swappable tool plugins."""

    def __init__(self, registry: PluginRegistry | None = None) -> None:
        self._tools: list[BaseTool] = []
        self._tool_to_plugin: dict[str, str] = {}  # tool_name -> rel_path
        self._version: int = 0
        self._lock = threading.Lock()
        self.registry = registry or PluginRegistry()
        self._workspace_dirs: list[Path] = []  # per-user workspace plugin dirs

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    # ------------------------------------------------------------------
    # Discovery & loading
    # ------------------------------------------------------------------

    def _discover_plugins(self, root: Path) -> list[tuple[Path, str]]:
        """Find all plugin files under *root* recursively.

        Returns ``(abs_path, rel_key)`` pairs where *rel_key* is relative to *root*.

        Folder-based plugins  → ``name/plugin.py`` with key ``name``
        Nested folder plugins → ``custom/name/plugin.py`` with key ``custom/name``
        Flat plugins          → ``name.py`` with key ``name.py``
        Nested flat plugins   → ``custom/name.py`` with key ``custom/name.py``
        """
        found: list[tuple[Path, str]] = []
        if not root.is_dir():
            return found
        self._scan_dir(root, root, found)
        return found

    @staticmethod
    def _read_plugin_filter(parent: Path, dir_name: str) -> str | None:
        """Check for a sibling filter file like ``.dirname_plugin_filter``.

        These are created by ``import_plugin_repo`` when the user imports a
        specific subfolder from a multi-plugin repo.  The filter file lives
        next to the submodule (not inside it) to avoid modifying the
        submodule's working tree.
        """
        filter_file = parent / f".{dir_name}_plugin_filter"
        if filter_file.is_file():
            return filter_file.read_text().strip() or None
        return None

    def _scan_dir(
        self, current: Path, root: Path, found: list[tuple[Path, str]]
    ) -> None:
        """Recursively scan *current* for plugins, tracking paths relative to *root*."""
        for entry in sorted(current.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue

            if entry.is_dir():
                # Check for a sibling plugin filter (multi-plugin repo).
                subfolder = self._read_plugin_filter(current, entry.name)
                if subfolder:
                    target = entry / subfolder
                    if target.is_dir():
                        plugin_py = target / "plugin.py"
                        if plugin_py.exists():
                            rel = str(target.relative_to(root))
                            found.append((plugin_py, rel))
                        else:
                            self._scan_dir(target, root, found)
                    else:
                        logger.warning(
                            "Plugin filter points to non-existent subfolder %s in %s",
                            subfolder, entry,
                        )
                    continue

                plugin_py = entry / "plugin.py"
                if plugin_py.exists():
                    # Folder-based plugin — key is the folder's path relative to root.
                    rel = str(entry.relative_to(root))
                    found.append((plugin_py, rel))
                else:
                    # No plugin.py — recurse into subdirectory.
                    self._scan_dir(entry, root, found)

            elif entry.is_file() and entry.suffix == ".py":
                # Flat plugin file.
                rel = str(entry.relative_to(root))
                found.append((entry, rel))

    def add_workspace_plugins_dir(self, plugins_dir: str | Path) -> None:
        """Register a workspace plugins directory for scanning.

        Workspace plugins are highest-priority — they override everything.
        Call this when a user's workspace has a ``plugins/`` directory.
        """
        p = Path(plugins_dir)
        with self._lock:
            if p not in self._workspace_dirs:
                self._workspace_dirs.append(p)

    def remove_workspace_plugins_dir(self, plugins_dir: str | Path) -> None:
        """Unregister a workspace plugins directory."""
        p = Path(plugins_dir)
        with self._lock:
            if p in self._workspace_dirs:
                self._workspace_dirs.remove(p)

    def load_all(self) -> list[BaseTool]:
        """Scan workspace plugins, built-in plugins, and the legacy plugin repo for tools.

        **Priority order**: workspace plugins > legacy plugin repo > built-in.
        If a higher-priority plugin defines a tool with the same name as a
        lower-priority one, the higher-priority version wins and the other is skipped.
        This lets Prax override built-in plugins by writing improved versions.
        """
        # Collect plugins from all sources, highest-priority first.
        # Each entry is (abs_path, rel_key, trust_tier).
        ordered_plugins: list[tuple[Path, str, str]] = []

        # Priority 1 (highest): workspace plugins (custom + shared submodules)
        with self._lock:
            ws_dirs = list(self._workspace_dirs)
        for ws_dir in ws_dirs:
            if ws_dir.is_dir():
                for item in self._discover_plugins(ws_dir):
                    ordered_plugins.append((*item, PluginTrust.WORKSPACE))

        # Priority 2: legacy plugin repo (deprecated — kept for backward compat)
        try:
            from prax.plugins.repo import get_plugin_repo
            repo = get_plugin_repo()
            if repo:
                for item in self._discover_plugins(repo.plugins_dir):
                    ordered_plugins.append((*item, PluginTrust.IMPORTED))
        except Exception:
            logger.debug("Plugin repo not available", exc_info=True)

        # Priority 3: built-in plugins (which includes custom/ via recursion)
        for item in self._discover_plugins(_PLUGINS_ROOT):
            ordered_plugins.append((*item, PluginTrust.BUILTIN))

        tools: list[BaseTool] = []
        tool_map: dict[str, str] = {}
        seen_tool_names: set[str] = set()
        builtin_names = _get_builtin_tool_names()

        for plugin_file, rel_key, trust_tier in ordered_plugins:
            try:
                if trust_tier == PluginTrust.IMPORTED:
                    # Phase 2: IMPORTED plugins are loaded in an isolated subprocess.
                    loaded = self._load_imported_via_bridge(
                        plugin_file, rel_key, trust_tier,
                        builtin_names, seen_tool_names,
                    )
                    for t, name in loaded:
                        tools.append(t)
                        tool_map[name] = rel_key
                        seen_tool_names.add(name)
                    continue

                # BUILTIN / WORKSPACE — load in-process as before.
                mod = self._import_plugin(plugin_file, trust_tier=trust_tier)
                if hasattr(mod, "register"):
                    # Pass PluginCapabilities if register() accepts a parameter.
                    reg_fn = mod.register
                    sig = inspect.signature(reg_fn)
                    if sig.parameters:
                        caps = PluginCapabilities(
                            plugin_rel_path=rel_key,
                            trust_tier=trust_tier,
                        )
                        plugin_tools = reg_fn(caps)
                    else:
                        plugin_tools = reg_fn()

                    if isinstance(plugin_tools, list):
                        new_tools = []
                        for t in plugin_tools:
                            if t.name in builtin_names:
                                logger.warning(
                                    "Rejecting plugin tool %s from %s — cannot override built-in tool",
                                    t.name, rel_key,
                                )
                                continue
                            if t.name in seen_tool_names:
                                logger.info(
                                    "Skipping duplicate tool %s from %s (already loaded from higher-priority source)",
                                    t.name, rel_key,
                                )
                                continue
                            monitored = wrap_with_monitoring(t, rel_key, trust_tier=trust_tier)
                            tools.append(monitored)
                            tool_map[t.name] = rel_key
                            seen_tool_names.add(t.name)
                            new_tools.append(t)
                        if new_tools:
                            version = getattr(mod, "PLUGIN_VERSION", "1")
                            self.registry.activate_plugin(rel_key, version, trust_tier=trust_tier)
                            logger.info(
                                "Loaded plugin %s: %s",
                                rel_key,
                                [t.name for t in new_tools],
                            )
            except Exception:
                logger.exception("Failed to load plugin %s", rel_key)

        with self._lock:
            # Only bump the version when the tool set actually changed.
            old_names = {t.name for t in self._tools}
            new_names = {t.name for t in tools}
            self._tools = tools
            self._tool_to_plugin = tool_map
            if new_names != old_names:
                self._version += 1
                logger.info("Plugin tool set changed (%d tools), version now %d", len(tools), self._version)

        # Regenerate the catalog after every full load.
        self._update_catalog()

        return tools

    def _load_imported_via_bridge(
        self,
        plugin_file: Path,
        rel_key: str,
        trust_tier: str,
        builtin_names: set[str],
        seen_tool_names: set[str],
    ) -> list[tuple[BaseTool, str]]:
        """Load an IMPORTED plugin in an isolated subprocess via the bridge.

        Returns a list of ``(wrapped_tool, tool_name)`` pairs.
        """
        from prax.plugins.bridge import get_bridge, shutdown_bridge

        # Shut down any existing bridge for this plugin (e.g., on reload).
        shutdown_bridge(rel_key)

        bridge = get_bridge(rel_key)
        caps = PluginCapabilities(
            plugin_rel_path=rel_key,
            trust_tier=trust_tier,
        )

        try:
            tool_metadata = bridge.register(
                plugin_path=str(plugin_file),
                trust_tier=trust_tier,
                caps=caps,
            )
        except Exception:
            logger.exception("Failed to register IMPORTED plugin %s via subprocess", rel_key)
            shutdown_bridge(rel_key)
            return []

        loaded: list[tuple[BaseTool, str]] = []
        for meta in tool_metadata:
            name = meta["name"]
            if name in builtin_names:
                logger.warning(
                    "Rejecting plugin tool %s from %s — cannot override built-in tool",
                    name, rel_key,
                )
                continue
            if name in seen_tool_names:
                logger.info(
                    "Skipping duplicate tool %s from %s (already loaded from higher-priority source)",
                    name, rel_key,
                )
                continue

            # Create a proxy StructuredTool that delegates invocations to the bridge.
            proxy_tool = self._make_proxy_tool(meta, rel_key, trust_tier)
            monitored = wrap_with_monitoring(proxy_tool, rel_key, trust_tier=trust_tier)
            loaded.append((monitored, name))

        if loaded:
            self.registry.activate_plugin(rel_key, "1", trust_tier=trust_tier)
            logger.info(
                "Loaded IMPORTED plugin %s via subprocess: %s",
                rel_key, [name for _, name in loaded],
            )

        return loaded

    @staticmethod
    def _make_proxy_tool(meta: dict, rel_key: str, trust_tier: str) -> BaseTool:
        """Create a StructuredTool that delegates invocation to the subprocess bridge."""
        from pydantic import create_model

        tool_name = meta["name"]
        description = meta.get("description", "")
        schema = meta.get("args_schema", {})

        # Build a Pydantic model from the JSON schema for args.
        fields: dict = {}
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        for field_name, field_info in props.items():
            field_type = str  # Default to str for simplicity.
            json_type = field_info.get("type", "string")
            if json_type == "integer":
                field_type = int
            elif json_type == "number":
                field_type = float
            elif json_type == "boolean":
                field_type = bool
            elif json_type == "array":
                field_type = list

            if field_name in required:
                fields[field_name] = (field_type, ...)
            else:
                default = field_info.get("default")
                fields[field_name] = (field_type | None, default)

        ArgsModel = create_model(f"{tool_name}_args", **fields)

        def _proxy_invoke(**kwargs):
            from prax.plugins.bridge import get_bridge
            bridge = get_bridge(rel_key)
            return bridge.invoke(tool_name, kwargs)

        return StructuredTool.from_function(
            func=_proxy_invoke,
            name=tool_name,
            description=description,
            args_schema=ArgsModel,
        )

    def get_tools(self) -> list[BaseTool]:
        """Return the current list of plugin-provided tools."""
        with self._lock:
            return list(self._tools)

    def get_tool_plugin_map(self) -> dict[str, str]:
        """Return mapping of tool_name -> plugin relative key."""
        with self._lock:
            return dict(self._tool_to_plugin)

    # ------------------------------------------------------------------
    # Hot-swap
    # ------------------------------------------------------------------

    def hot_swap(self, plugin_path: str) -> dict:
        """Test and activate a single plugin, then rebuild tool list.

        Args:
            plugin_path: Absolute path to the plugin .py file.

        Returns:
            Dict with status/error and the new version number.
        """
        result = sandbox_test_plugin(plugin_path)
        if not result["passed"]:
            return {"error": "Sandbox test failed", "details": result}

        abs_path = Path(plugin_path).resolve()

        # Determine the relative key.
        rel_key = self._rel_key_for(abs_path)

        self.registry.backup_file(plugin_path)

        version = self._read_plugin_version(plugin_path)
        self.registry.activate_plugin(rel_key, version)

        self.load_all()

        return {"status": "swapped", "version": self.version, "tools": result["tools"]}

    def rollback(self, rel_key: str) -> dict:
        """Rollback a plugin to its previous version."""
        abs_path = self._abs_path_for(rel_key)
        if abs_path and self.registry.restore_file(str(abs_path)):
            self.registry.mark_rolled_back(rel_key)
            self.load_all()
            return {"status": "rolled_back", "rel_key": rel_key}
        return {"error": f"No backup found for {rel_key}"}

    def remove_plugin(self, rel_key: str) -> dict:
        """Remove a plugin and deactivate it."""
        abs_path = self._abs_path_for(rel_key)
        if abs_path and abs_path.exists():
            self.registry.backup_file(str(abs_path))
            abs_path.unlink()
            # Remove the folder if it's now empty (folder-based plugin).
            if abs_path.parent.name != "custom" and not any(abs_path.parent.iterdir()):
                abs_path.parent.rmdir()
            self.registry.deactivate_plugin(rel_key)
            self.load_all()
            return {"status": "removed", "rel_key": rel_key}
        return {"error": f"Plugin not found: {rel_key}"}

    # ------------------------------------------------------------------
    # Monitoring integration
    # ------------------------------------------------------------------

    def record_tool_success(self, tool_name: str) -> None:
        rel = self._tool_to_plugin.get(tool_name)
        if rel:
            self.registry.record_success(rel)

    def record_tool_failure(self, tool_name: str) -> bool:
        """Record a failure. Returns True if auto-rollback was triggered."""
        rel = self._tool_to_plugin.get(tool_name)
        if not rel:
            return False
        self.registry.record_failure(rel)
        if self.registry.needs_rollback(rel):
            logger.warning("Auto-rolling back plugin %s after repeated failures", rel)
            self.rollback(rel)
            self._emit_audit_event(
                "plugin_rollback", rel,
                "reason=auto_rollback_repeated_failures",
            )
            return True
        return False

    @staticmethod
    def _emit_audit_event(event_type: str, plugin_name: str, details: str = "") -> None:
        """Best-effort audit event emission from the loader.

        Guarded because the loader may run at startup before any user context
        is available.
        """
        try:
            from prax.agent.user_context import current_user_id
            from prax.services.workspace_service import append_trace
            uid = current_user_id.get()
            if not uid:
                return
            entry = {
                "type": event_type,
                "content": f"plugin={plugin_name} {details}".strip(),
            }
            append_trace(uid, [entry])
        except Exception:
            pass  # Best-effort — don't block plugin operations

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def _update_catalog(self) -> None:
        """Regenerate CATALOG.md in both local custom/ and the plugin repo."""
        try:
            from prax.plugins.catalog import generate_catalog


            # Local custom catalog
            custom_dir = _PLUGINS_ROOT / "custom"
            if custom_dir.is_dir():
                generate_catalog(
                    _PLUGINS_ROOT, custom_dir,
                    catalog_path=_PLUGINS_ROOT / "CATALOG.md",
                )

            # Plugin repo catalog
            try:
                from prax.plugins.repo import get_plugin_repo
                repo = get_plugin_repo()
                if repo:
                    generate_catalog(
                        repo.plugins_dir,
                        catalog_path=repo.catalog_path,
                    )
            except Exception:
                pass
        except Exception:
            logger.debug("Catalog generation skipped", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rel_key_for(self, abs_path: Path) -> str:
        """Compute a relative key for a plugin given its absolute path."""
        # Folder-based: .../tools/name/plugin.py -> "name"
        if abs_path.name == "plugin.py" and abs_path.parent.name != "tools":
            try:
                return abs_path.parent.name
            except Exception:
                pass

        # Flat: .../tools/name.py -> "name.py"
        for root in self._scan_roots():
            try:
                return str(abs_path.relative_to(root))
            except ValueError:
                continue
        return abs_path.name

    def _abs_path_for(self, rel_key: str) -> Path | None:
        """Resolve a relative key back to an absolute path."""
        for root in self._scan_roots():
            # Folder-based: rel_key is folder name
            candidate = root / rel_key / "plugin.py"
            if candidate.exists():
                return candidate
            # Flat
            candidate = root / rel_key
            if candidate.exists():
                return candidate
        return None

    def _scan_roots(self) -> list[Path]:
        """Return all directories the loader scans."""
        roots = [_PLUGINS_ROOT]
        try:
            from prax.plugins.repo import get_plugin_repo
            repo = get_plugin_repo()
            if repo:
                roots.append(repo.plugins_dir)
        except Exception:
            pass
        return roots

    @staticmethod
    def _import_plugin(path: Path, trust_tier: str = PluginTrust.BUILTIN):
        """Import a plugin module from an absolute path.

        For IMPORTED plugins the module is loaded inside a
        :func:`restricted_import_env` context so that top-level code
        cannot read sensitive environment variables.  After import, the
        module's ``os`` attribute (if any) is replaced with a restricted
        copy so runtime access is also blocked.
        """
        spec = importlib.util.spec_from_file_location(f"plugin_{path.stem}_{path.parent.name}", str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create spec for {path}")
        mod = importlib.util.module_from_spec(spec)

        if trust_tier == PluginTrust.IMPORTED:
            with restricted_import_env(plugin_name=str(path)):
                spec.loader.exec_module(mod)
            # Post-import: replace module's os.environ with sanitized version.
            if hasattr(mod, "os"):
                from prax.plugins.restricted_env import SanitizedEnviron
                mod.os.environ = SanitizedEnviron(plugin_name=str(path))
        else:
            spec.loader.exec_module(mod)

        return mod

    @staticmethod
    def _read_plugin_version(path: str) -> str:
        """Read PLUGIN_VERSION from a plugin file, defaulting to '1'."""
        try:
            mod = PluginLoader._import_plugin(Path(path))
            return getattr(mod, "PLUGIN_VERSION", "1")
        except Exception:
            return "unknown"


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_loader: PluginLoader | None = None
_loader_lock = threading.Lock()


def get_plugin_loader() -> PluginLoader:
    """Return the global plugin loader singleton, creating it on first call."""
    global _loader
    if _loader is None:
        with _loader_lock:
            if _loader is None:
                _loader = PluginLoader()
                _loader.load_all()
    return _loader
