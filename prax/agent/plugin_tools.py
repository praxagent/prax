"""Agent-facing tools for self-modification via the plugin system.

These tools let the agent list, read, write, test, activate, and
rollback plugins at runtime without restarting the process.

Plugins use a folder-per-plugin layout::

    custom/<name>/plugin.py   # Tool code + register()
    custom/<name>/README.md   # Description

Agent-created plugins are written to the user's workspace ``plugins/custom/``
directory.  Shared plugins from public repos are imported as git submodules
into ``plugins/shared/<name>/``.

The workspace can be pushed to a private remote using PRAX_SSH_KEY_B64.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.agent.user_context import current_user_id
from prax.plugins.loader import get_plugin_loader
from prax.trace_events import TraceEvent

_PLUGINS_TOOLS_ROOT = Path(__file__).resolve().parent.parent / "plugins" / "tools"
_CUSTOM_DIR = _PLUGINS_TOOLS_ROOT / "custom"


def _audit_plugin_event(event_type: str, plugin_name: str, details: str = "") -> None:
    """Emit a plugin lifecycle audit event to the workspace trace."""
    from prax.agent.user_context import current_user_id
    from prax.services.workspace_service import append_trace
    uid = current_user_id.get()
    if not uid:
        return
    entry = {
        "type": event_type,
        "content": f"plugin={plugin_name} {details}".strip(),
    }
    try:
        append_trace(uid, [entry])
    except Exception:
        pass  # Best-effort — don't block plugin operations


def _get_plugin_base_dir() -> Path:
    """Return the directory where agent-created plugins are stored.

    Prefers the current user's workspace ``plugins/custom/`` directory.
    Falls back to the built-in ``plugins/tools/custom/``.
    """
    uid = current_user_id.get()
    if uid:
        try:
            from prax.services.workspace_service import get_workspace_plugins_dir
            ws_plugins = get_workspace_plugins_dir(uid)
            if ws_plugins:
                custom = Path(ws_plugins) / "custom"
                custom.mkdir(parents=True, exist_ok=True)
                return custom
        except Exception:
            pass
    return _CUSTOM_DIR


def _safe_plugin_path(name: str) -> Path:
    """Resolve a plugin name to its ``plugin.py`` path, blocking path traversal."""
    name = name.removesuffix(".py").removesuffix("/plugin").strip("/")
    base = _get_plugin_base_dir()
    joined = (base / name / "plugin.py").resolve()
    if not str(joined).startswith(str(base.resolve())):
        raise ValueError(f"Path traversal blocked: {name}")
    return joined


# ------------------------------------------------------------------
# Plugin management tools
# ------------------------------------------------------------------


@tool
def plugin_list() -> str:
    """List all active plugins with their versions and status."""
    loader = get_plugin_loader()
    registry_plugins = loader.registry.list_plugins()
    plugin_tools = loader.get_tools()

    if not registry_plugins and not plugin_tools:
        return "No custom plugins are currently loaded."

    lines = ["**Active Plugins:**\n"]
    for rel_key, info in registry_plugins.items():
        status = info.get("status", "unknown")
        version = info.get("active_version", "?")
        failures = info.get("failure_count", 0)
        trust = info.get("trust_tier", "imported")
        lines.append(
            f"- `{rel_key}` — v{version} ({status}), trust: {trust}, failures: {failures}"
        )

    if plugin_tools:
        lines.append(
            f"\n**Plugin-provided tools:** {', '.join(t.name for t in plugin_tools)}"
        )

    return "\n".join(lines)


@tool
def plugin_read(name: str) -> str:
    """Read a plugin's source code.

    Args:
        name: Plugin name (e.g. "weather" for custom/weather/plugin.py).
    """
    try:
        abs_path = _safe_plugin_path(name)
    except ValueError as e:
        return str(e)
    if not abs_path.exists():
        return f"Plugin not found: {name}"
    return abs_path.read_text()


@risk_tool(risk=RiskLevel.HIGH)
def plugin_write(name: str, code: str, description: str = "") -> str:
    """Write or update a plugin and run sandbox tests.

    Creates a folder-based plugin at ``<name>/plugin.py`` with an auto-generated
    ``README.md``.  If a plugin repo is configured, the change is pushed there.

    The plugin MUST define a ``register()`` function that returns a list of
    LangChain ``@tool`` decorated functions.  Example::

        from langchain_core.tools import tool

        PLUGIN_VERSION = "1"
        PLUGIN_DESCRIPTION = "What this plugin does"

        @tool
        def my_tool(arg: str) -> str:
            \\\"\\\"\\\"Description of what this tool does.\\\"\\\"\\\"
            return "result"

        def register():
            return [my_tool]

    Args:
        name: Plugin name (creates ``<name>/plugin.py``).
        code: The full Python source code for the plugin.
        description: One-line description for the README (optional).
    """
    try:
        abs_path = _safe_plugin_path(name)
    except ValueError as e:
        return f"Error: {e}"

    loader = get_plugin_loader()

    # Back up existing version if any.
    if abs_path.exists():
        loader.registry.backup_file(str(abs_path))

    # Write plugin.py
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(code)

    # Write/update README.md
    readme = abs_path.parent / "README.md"
    if not readme.exists() or description:
        readme.write_text(
            f"# {name}\n\n{description or 'Custom plugin.'}\n"
        )

    # Run sandbox test.
    from prax.plugins.sandbox import sandbox_test_plugin
    result = sandbox_test_plugin(str(abs_path))

    if not result["passed"]:
        if loader.registry.restore_file(str(abs_path)):
            return (
                f"Sandbox test FAILED — reverted to previous version.\n"
                f"Errors: {result['errors']}"
            )
        else:
            abs_path.unlink(missing_ok=True)
            return f"Sandbox test FAILED — file removed.\nErrors: {result['errors']}"

    _audit_plugin_event(
        TraceEvent.PLUGIN_ACTIVATE, name,
        f"action=write tools={result['tools']}",
    )
    return (
        f"Plugin written and sandbox test PASSED.\n"
        f"Tools defined: {result['tools']}\n"
        f"Use plugin_activate('{name}') to make it live.\n"
        f"Use workspace_push() to sync to the remote."
    )


@tool
def plugin_test(name: str) -> str:
    """Run sandbox tests on a plugin without activating it.

    Args:
        name: Plugin name (e.g. "weather").
    """
    try:
        abs_path = _safe_plugin_path(name)
    except ValueError as e:
        return str(e)

    if not abs_path.exists():
        return f"Plugin not found: {name}"

    from prax.plugins.sandbox import sandbox_test_plugin
    result = sandbox_test_plugin(str(abs_path))

    if result["passed"]:
        return f"PASSED. Tools: {result['tools']}"
    return f"FAILED.\nErrors: {result['errors']}"


@risk_tool(risk=RiskLevel.HIGH)
def plugin_activate(name: str) -> str:
    """Activate a plugin, making its tools available to the agent.

    The plugin must already exist and pass sandbox tests.

    Args:
        name: Plugin name (e.g. "weather").
    """
    try:
        abs_path = _safe_plugin_path(name)
    except ValueError as e:
        return str(e)

    if not abs_path.exists():
        return f"Plugin not found: {name}. Use plugin_write first."

    loader = get_plugin_loader()
    result = loader.hot_swap(str(abs_path))

    if "error" in result:
        _audit_plugin_event(
            TraceEvent.PLUGIN_BLOCK, name,
            f"reason={result['error']}",
        )
        return f"Activation FAILED: {result['error']}\nDetails: {result.get('details', '')}"

    _audit_plugin_event(
        TraceEvent.PLUGIN_ACTIVATE, name,
        f"version={result['version']} tools={result.get('tools', [])}",
    )
    return (
        f"Plugin activated! Tools: {result.get('tools', [])}\n"
        f"Plugin system version: {result['version']}\n"
        f"IMPORTANT: The new tools are NOT available in this turn. "
        f"Tell the user the plugin is ready and ask them to send their "
        f"request again. Do NOT attempt to call the new tools in this "
        f"same turn — they will fail with stale bindings."
    )


@tool
def plugin_rollback(name: str) -> str:
    """Revert a plugin to its previous version.

    Args:
        name: Plugin name (e.g. "weather").
    """
    name = name.removesuffix(".py").strip("/")

    loader = get_plugin_loader()
    result = loader.rollback(name)

    if "error" in result:
        return f"Rollback failed: {result['error']}"

    _audit_plugin_event(TraceEvent.PLUGIN_ROLLBACK, name)
    return f"Rolled back `{name}` to previous version. Tools reloaded."


@tool
def plugin_status(name: str) -> str:
    """Check the health and version info of a plugin.

    Args:
        name: Plugin name (e.g. "weather").
    """
    name = name.removesuffix(".py").strip("/")

    loader = get_plugin_loader()
    info = loader.registry.get_plugin_info(name)

    if not info:
        return f"No registry entry for `{name}`. Plugin may not be activated yet."

    lines = [
        f"**Plugin: {name}**",
        f"- Status: {info.get('status', 'unknown')}",
        f"- Trust tier: {info.get('trust_tier', 'imported')}",
        f"- Active version: {info.get('active_version', '?')}",
        f"- Previous version: {info.get('previous_version', 'none')}",
        f"- Activated at: {info.get('activated_at', '?')}",
        f"- Failure count: {info.get('failure_count', 0)}",
        f"- Auto-rollback after: {info.get('max_failures_before_rollback', 3)} failures",
    ]
    return "\n".join(lines)


@tool
def plugin_remove(name: str) -> str:
    """Remove a plugin entirely.

    A backup is kept so plugin_rollback can restore it if needed.

    Args:
        name: Plugin name (e.g. "weather").
    """
    name = name.removesuffix(".py").strip("/")

    loader = get_plugin_loader()
    result = loader.remove_plugin(name)

    if "error" in result:
        return f"Remove failed: {result['error']}"

    _audit_plugin_event(TraceEvent.PLUGIN_REMOVE, name)
    return f"Plugin `{name}` removed. Backup saved — use plugin_rollback to restore."


# ------------------------------------------------------------------
# Prompt management tools
# ------------------------------------------------------------------


@tool
def prompt_read(name: str = "system_prompt.md") -> str:
    """Read the current system prompt (or any named prompt).

    Args:
        name: Prompt filename (default: "system_prompt.md").
    """
    from prax.plugins.prompt_manager import get_prompt_manager
    return get_prompt_manager().read(name)


@tool
def prompt_write(name: str, content: str) -> str:
    """Update a system prompt. A backup is saved for rollback.

    CAUTION: Changing the system prompt affects all future conversations.
    Test carefully. The previous version can be restored with prompt_rollback.

    Args:
        name: Prompt filename (e.g. "system_prompt.md").
        content: The full new prompt text. Use {{AGENT_NAME}} as a placeholder
                 for the agent's configured name.
    """
    from prax.plugins.prompt_manager import get_prompt_manager
    result = get_prompt_manager().write(name, content)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Prompt '{name}' updated (hash: {result['hash']}). Takes effect on next conversation turn."


@tool
def prompt_rollback(name: str = "system_prompt.md") -> str:
    """Revert a prompt to its previous version.

    Args:
        name: Prompt filename (default: "system_prompt.md").
    """
    from prax.plugins.prompt_manager import get_prompt_manager
    result = get_prompt_manager().rollback(name)
    if "error" in result:
        return f"Rollback failed: {result['error']}"
    return f"Prompt '{name}' rolled back (hash: {result['hash']}). Takes effect on next conversation turn."


@tool
def prompt_list() -> str:
    """List all available prompts with their version info."""
    from prax.plugins.prompt_manager import get_prompt_manager
    prompts = get_prompt_manager().list_prompts()
    if not prompts:
        return "No prompts found."
    lines = ["**Available Prompts:**\n"]
    for p in prompts:
        prev = f" (prev: {p['previous_hash']})" if p.get("previous_hash") else ""
        lines.append(f"- `{p['name']}` — {p['size']} bytes, hash: {p['hash']}{prev}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# LLM routing tools
# ------------------------------------------------------------------


@tool
def llm_config_read(component: str = "orchestrator") -> str:
    """Read the current LLM routing config for a component.

    Shows which provider, model, and temperature are used for a component.
    A null value means the global default from environment variables is used.

    Args:
        component: Component name — e.g. "orchestrator", "subagent_research",
                   "subagent_coding", "subagent_browser".
    """
    from prax.plugins.llm_config import get_component_config
    cfg = get_component_config(component)
    lines = [
        f"**LLM Config: {component}**",
        f"- Provider: {cfg['provider'] or '(global default)'}",
        f"- Model: {cfg['model'] or '(global default)'}",
        f"- Temperature: {cfg['temperature'] if cfg['temperature'] is not None else '(global default)'}",
    ]
    return "\n".join(lines)


@tool
def llm_config_update(component: str, provider: str | None = None,
                       model: str | None = None, temperature: float | None = None) -> str:
    """Update the LLM routing config for a component.

    Changes are persisted to llm_routing.yaml and take effect on the next
    agent initialization (or next conversation turn for subagents).

    Supported providers: openai, anthropic, google, ollama, vllm.

    Args:
        component: Component name — e.g. "orchestrator", "subagent_research".
        provider: LLM provider (or None to keep current).
        model: Model name (or None to keep current).
        temperature: Temperature (or None to keep current).
    """
    from prax.plugins.llm_config import update_component_config
    kwargs = {}
    if provider is not None:
        kwargs["provider"] = provider
    if model is not None:
        kwargs["model"] = model
    if temperature is not None:
        kwargs["temperature"] = temperature

    if not kwargs:
        return "No changes specified. Pass provider, model, and/or temperature."

    result = update_component_config(component, **kwargs)
    return (
        f"Updated LLM config for '{component}':\n"
        f"- Provider: {result.get('provider', '(unchanged)')}\n"
        f"- Model: {result.get('model', '(unchanged)')}\n"
        f"- Temperature: {result.get('temperature', '(unchanged)')}\n"
        f"Changes saved to llm_routing.yaml."
    )


# ------------------------------------------------------------------
# Plugin catalog tool
# ------------------------------------------------------------------


@tool
def plugin_catalog() -> str:
    """Show the auto-generated plugin catalog listing all available plugins."""
    from prax.plugins.catalog import generate_catalog
    from prax.plugins.loader import _PLUGINS_ROOT

    dirs = [_PLUGINS_ROOT]

    try:
        from prax.plugins.repo import get_plugin_repo
        repo = get_plugin_repo()
        if repo:
            dirs.append(repo.plugins_dir)
    except Exception:
        pass

    return generate_catalog(*dirs)


# ------------------------------------------------------------------
# Source code introspection tools
# ------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@tool
def source_read(path: str) -> str:
    """Read a source file from the Prax codebase.

    Use this to inspect your own implementation — any ``.py``, ``.md``,
    ``.yaml``, or ``.txt`` file under the project root.  This is read-only;
    use the plugin or self-improve tools to make changes.

    Args:
        path: Relative path from the project root (e.g. "prax/agent/tools.py",
              "prax/plugins/loader.py", "README.md").
    """
    # Block path traversal.
    abs_path = (_PROJECT_ROOT / path).resolve()
    if not str(abs_path).startswith(str(_PROJECT_ROOT)):
        return f"Path traversal blocked: {path}"

    if not abs_path.exists():
        return f"File not found: {path}"

    if abs_path.is_dir():
        # List directory contents instead.
        entries = sorted(abs_path.iterdir())
        items = []
        for e in entries:
            if e.name.startswith("."):
                continue
            suffix = "/" if e.is_dir() else ""
            items.append(f"  {e.name}{suffix}")
        return f"Directory listing of {path}/:\n" + "\n".join(items)

    # Only allow text-like files.
    allowed = {".py", ".md", ".yaml", ".yml", ".txt", ".toml", ".cfg", ".ini", ".json", ".env-example"}
    if abs_path.suffix not in allowed and abs_path.name not in {"Makefile", "Dockerfile", ".env-example"}:
        return f"Cannot read binary file: {path} (suffix: {abs_path.suffix})"

    content = abs_path.read_text()
    if len(content) > 50_000:
        content = content[:50_000] + "\n\n[Truncated — file too large]"
    return content


@tool
def source_list(path: str = "prax") -> str:
    """List files and directories in the Prax codebase.

    Args:
        path: Relative directory path from project root (default: "prax").
    """
    abs_path = (_PROJECT_ROOT / path).resolve()
    if not str(abs_path).startswith(str(_PROJECT_ROOT)):
        return f"Path traversal blocked: {path}"

    if not abs_path.is_dir():
        return f"Not a directory: {path}"

    entries = sorted(abs_path.iterdir())
    items = []
    for e in entries:
        if e.name.startswith(".") or e.name == "__pycache__":
            continue
        suffix = "/" if e.is_dir() else ""
        items.append(f"  {e.name}{suffix}")
    return f"{path}/:\n" + "\n".join(items)


# ------------------------------------------------------------------
# Workspace push + shared plugin import tools
# ------------------------------------------------------------------


@tool
def workspace_set_remote(remote_url: str) -> str:
    """Set the git remote for the user's workspace.

    This must be a PRIVATE repository — Prax will verify before setting.
    Once set, use workspace_push to sync the workspace to the remote.

    Args:
        remote_url: Git remote URL (e.g. "git@github.com:user/prax-workspace.git").
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."
    from prax.services.workspace_service import set_remote
    result = set_remote(uid, remote_url)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Remote set to {result['url']}. Use workspace_push() to sync."


@tool
def workspace_push() -> str:
    """Push the workspace (files, plugins, notes) to its private remote.

    Requires PRAX_SSH_KEY_B64 in .env and a remote set via workspace_set_remote.
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."
    from prax.services.workspace_service import push
    result = push(uid)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Workspace pushed to branch '{result['branch']}'."


@tool
def workspace_share_file(file_path: str) -> str:
    """Publish a workspace file and get a public URL to share with the user.

    Use this when you need to share a large file (video, PDF, etc.) that
    can't be sent inline via SMS or Discord. The URL is accessible via ngrok.
    Only the specific file you publish is shared — nothing else in the
    workspace is exposed.

    Args:
        file_path: Path relative to the workspace root (e.g. "active/presentation.mp4").
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."
    from prax.services.workspace_service import publish_file
    result = publish_file(uid, file_path)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"File shared: {result['url']}"


@tool
def workspace_unshare_file(token: str) -> str:
    """Remove a previously shared file link.

    Args:
        token: The share token returned by workspace_share_file.
    """
    from prax.services.workspace_service import unpublish_file
    result = unpublish_file(token)
    if "error" in result:
        return f"Error: {result['error']}"
    return "File link removed."


@tool
def plugin_import(repo_url: str, name: str | None = None, plugin_subfolder: str | None = None) -> str:
    """Import a shared plugin repository from a public URL.

    The repo is cloned as a git submodule into the workspace's
    ``plugins/shared/<name>/`` directory.  After importing, the plugins
    are automatically loaded and available.

    Supports multi-plugin repos: if the URL points to a specific subfolder
    (e.g. ``https://github.com/org/plugins/tree/main/pdf2presentation``),
    only that plugin is activated.  If the URL points to the repo root, all
    plugin subfolders are loaded.

    IMPORTANT: Before installing, review the plugin's code for security risks.
    If anything looks suspicious (shell commands, network calls to unknown hosts,
    file access outside the workspace, obfuscated code), warn the user and do NOT
    install unless they explicitly confirm after seeing the warning.

    Args:
        repo_url: Git URL of the plugin repo, or a GitHub subfolder link.
        name: Optional name for the plugin directory. Auto-derived from URL if omitted.
        plugin_subfolder: Optional subfolder within the repo to activate (e.g. "pdf2presentation").
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."
    from prax.services.workspace_service import import_plugin_repo
    result = import_plugin_repo(uid, repo_url, name, plugin_subfolder)
    if "error" in result:
        return f"Error: {result['error']}"

    # --- Security warnings ---
    # If the scan found anything, report to the user and do NOT load the
    # plugins yet.  The user must explicitly confirm before activation.
    warnings = result.get("security_warnings", [])
    if warnings:
        _audit_plugin_event(
            TraceEvent.PLUGIN_SECURITY_WARN, result.get("name", repo_url),
            f"warnings={len(warnings)}",
        )
        _audit_plugin_event(
            TraceEvent.PLUGIN_BLOCK, result.get("name", repo_url),
            "reason=security_warnings",
        )
        lines = [
            f"**Security review for '{result['name']}':**\n",
            f"Found {len(warnings)} potential concern(s) in the plugin code:\n",
        ]
        for w in warnings[:20]:  # cap at 20 to avoid wall of text
            lines.append(f"- **{w['file']}** line {w['line']}: {w['pattern']}")
            lines.append(f"  `{w['code']}`\n")
        if len(warnings) > 20:
            lines.append(f"... and {len(warnings) - 20} more.\n")
        lines.append(
            "\n**The plugin has been cloned but NOT activated.**\n"
            "Please review the warnings above. If you trust this plugin, "
            "tell me to activate it and I will load it."
        )
        return "\n".join(lines)

    # No warnings — safe to load immediately.
    loader = get_plugin_loader()
    try:
        from prax.services.workspace_service import get_workspace_plugins_dir
        plugins_dir = get_workspace_plugins_dir(uid)
        if plugins_dir:
            loader.add_workspace_plugins_dir(plugins_dir)
    except Exception:
        pass
    loader.load_all()

    subfolder_note = ""
    if result.get("subfolder"):
        subfolder_note = f"Active subfolder: {result['subfolder']}\n"

    _audit_plugin_event(
        TraceEvent.PLUGIN_IMPORT, result.get("name", repo_url),
        f"url={result.get('url', repo_url)}",
    )
    return (
        f"Imported plugin repo '{result['name']}' from {result['url']}.\n"
        f"Path: {result['path']}\n"
        f"{subfolder_note}"
        f"Plugins loaded and available."
    )


@tool
def plugin_import_activate(name: str) -> str:
    """Activate a previously imported plugin that was held back due to security warnings.

    Only call this AFTER the user has reviewed the security warnings and
    explicitly confirmed they want to proceed.

    Args:
        name: The plugin name (directory name under plugins/shared/).
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."
    loader = get_plugin_loader()
    try:
        from prax.services.workspace_service import get_workspace_plugins_dir
        plugins_dir = get_workspace_plugins_dir(uid)
        if plugins_dir:
            loader.add_workspace_plugins_dir(plugins_dir)
    except Exception:
        pass
    loader.load_all()
    return f"Plugin '{name}' activated. Tools reloaded."


@tool
def plugin_import_remove(name: str) -> str:
    """Remove a previously imported shared plugin repository.

    Args:
        name: The plugin name (directory name under plugins/shared/).
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."
    from prax.services.workspace_service import remove_plugin_repo
    result = remove_plugin_repo(uid, name)
    if "error" in result:
        return f"Error: {result['error']}"

    # Reload plugins.
    get_plugin_loader().load_all()
    return f"Removed shared plugin '{result['name']}'. Tools reloaded."


@tool
def plugin_import_update(name: str) -> str:
    """Update an imported shared plugin to its latest version.

    Pulls the newest commit from upstream. If the update introduces new
    security concerns, warnings are shown and the plugin is NOT reloaded
    until the user confirms.

    Args:
        name: The plugin name (directory name under plugins/shared/).
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."
    from prax.services.workspace_service import update_plugin_repo
    result = update_plugin_repo(uid, name)
    if "error" in result:
        return f"Error: {result['error']}"

    if result["status"] == "up_to_date":
        return f"Plugin '{result['name']}' is already up to date ({result['commit']})."

    # Check for security warnings in the updated code.
    warnings = result.get("security_warnings", [])
    if warnings:
        lines = [
            f"Updated '{result['name']}' ({result['old_commit']} → {result['new_commit']}).\n",
            f"**Security review found {len(warnings)} concern(s):**\n",
        ]
        for w in warnings[:20]:
            lines.append(f"- **{w['file']}** line {w['line']}: {w['pattern']}")
            lines.append(f"  `{w['code']}`\n")
        lines.append(
            "\n**Plugin updated but NOT reloaded.** "
            "Review the warnings above. Tell me to activate it if you trust the changes."
        )
        return "\n".join(lines)

    # No warnings — reload immediately.
    loader = get_plugin_loader()
    try:
        from prax.services.workspace_service import get_workspace_plugins_dir
        plugins_dir = get_workspace_plugins_dir(uid)
        if plugins_dir:
            loader.add_workspace_plugins_dir(plugins_dir)
    except Exception:
        pass
    loader.load_all()

    return (
        f"Updated '{result['name']}' ({result['old_commit']} → {result['new_commit']}). "
        f"Tools reloaded."
    )


@tool
def plugin_import_list() -> str:
    """List all imported shared plugin repositories."""
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."
    from prax.services.workspace_service import list_shared_plugins
    plugins = list_shared_plugins(uid)
    if not plugins:
        return "No shared plugins imported."
    lines = ["**Imported Shared Plugins:**\n"]
    for p in plugins:
        url = f" — {p['url']}" if p.get("url") else ""
        sub = f" (filter: {p['subfolder_filter']})" if p.get("subfolder_filter") else ""
        found = ""
        if p.get("plugins_found"):
            found = f" — plugins: {', '.join(p['plugins_found'])}"
        lines.append(f"- `{p['name']}`{url}{sub}{found}")
    return "\n".join(lines)


def build_plugin_tools() -> list:
    """Return all agent-facing plugin management tools."""
    return [
        plugin_list,
        plugin_read,
        plugin_write,
        plugin_test,
        plugin_activate,
        plugin_rollback,
        plugin_status,
        plugin_remove,
        plugin_catalog,
        plugin_import,
        plugin_import_activate,
        plugin_import_update,
        plugin_import_remove,
        plugin_import_list,
        prompt_read,
        prompt_write,
        prompt_rollback,
        prompt_list,
        llm_config_read,
        llm_config_update,
        source_read,
        source_list,
        workspace_set_remote,
        workspace_push,
        workspace_share_file,
        workspace_unshare_file,
    ]
