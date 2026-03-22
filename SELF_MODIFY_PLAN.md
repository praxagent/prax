# Self-Modification Architecture Plan

## Problem

The agent needs to modify its own behavior — fix bugs it encounters, add tools, tune prompts — without risking the stability of the running system. The current approach (codegen_service.py) copies files into the live repo and hopes Werkzeug reloads cleanly. This is fragile: a bad import kills the process, there's no real isolation, and rollback means git-reverting and restarting.

## Core Principle: Immutable Kernel, Hot-Swappable Plugins

The primary agent loop — Flask app, orchestrator, conversation service, message routing — is **never modified by the agent**. It is the kernel. Everything the agent might want to change is loaded dynamically as a **plugin**:

| Layer | Examples | Hot-swappable? |
|-------|----------|----------------|
| **Kernel** | Flask app, orchestrator loop, conversation service, Discord/SMS routing | No — human-only via git |
| **Tools** | Any LangChain `@tool` function | Yes |
| **Subagents** | Delegated task handlers (research, coding, browser) | Yes |
| **System Prompt** | Instructions, persona, behavioral rules | Yes |
| **LLM Config** | Provider, model, temperature per-component | Yes |

## Architecture

```
prax/
  core/                    # THE KERNEL — never agent-modified
    app.py                 # Flask setup
    orchestrator.py        # ReAct loop, graph construction
    conversation_service.py
    plugin_loader.py       # Discovers + loads plugins
    plugin_sandbox.py      # Tests plugins before activation
    plugin_registry.py     # Tracks active/previous versions

  plugins/                 # HOT-SWAPPABLE — agent can modify
    tools/                 # Each file = one or more tools
      builtin.py           # search, datetime, pdf, etc.
      workspace.py         # workspace_save, workspace_read, etc.
      sandbox.py           # sandbox_start, sandbox_message, etc.
      scheduler.py         # schedule_create, reminder, etc.
      browser.py           # browser_open, browser_click, etc.
      custom/              # Agent-created tools land here
        weather_v2.py
        stock_lookup.py

    subagents/             # Each file = one subagent config
      research.py          # Web research subagent
      coding.py            # Code execution subagent
      browser.py           # Browser automation subagent
      custom/              # Agent-created subagents
        data_analyst.py

    prompts/               # System prompts as versioned text
      system_prompt.md     # Main agent instructions
      subagent_research.md
      subagent_coding.md

    configs/               # LLM routing configs
      llm_routing.yaml     # Which model for which component
```

## Plugin Format

Every plugin is a Python module with a standard interface:

```python
# plugins/tools/custom/stock_lookup.py
"""Stock price lookup tool — v2, added after-hours support."""

from langchain_core.tools import tool

PLUGIN_VERSION = "2"
PLUGIN_DESCRIPTION = "Stock price lookup with after-hours support"


@tool
def stock_price(ticker: str) -> str:
    """Get the current stock price for a ticker symbol."""
    import requests
    resp = requests.get(f"https://api.example.com/quote/{ticker}")
    return resp.json()["price"]


def register():
    """Return the list of tools this plugin provides."""
    return [stock_price]
```

The `register()` function is the contract. The loader calls it, gets tools back, and wires them into the agent graph.

## Plugin Lifecycle

```
  Agent writes code
        |
        v
  [1. WRITE] ──> plugins/tools/custom/stock_lookup.py
        |
        v
  [2. SANDBOX TEST] ──> Import in isolated subprocess
        |                Check: no import errors
        |                Check: register() returns valid tools
        |                Check: tool schema is valid (name, docstring, args)
        |                Check: dry-run call doesn't crash
        |                Check: existing tests still pass
        |
        v
  [3. ACTIVATE] ──> plugin_registry marks it as active
        |            orchestrator rebuilds tool list
        |            agent graph is reconstructed with new tools
        |
        v
  [4. MONITOR] ──> If the tool raises exceptions in live use
        |            after N failures, auto-rollback
        |
        v
  [5. ROLLBACK] ──> Revert to previous version
                     Rebuild agent graph
```

## The Sandbox

### What it does

Before any plugin goes live, it runs in an isolated test environment. This catches import errors, syntax bugs, missing dependencies, and obvious runtime failures **before** they can affect the live agent.

### Implementation: Subprocess Isolation

The sandbox does NOT need Docker — it runs in a **subprocess** of the same Python environment, with an import fence:

```python
# prax/core/plugin_sandbox.py

import importlib
import json
import subprocess
import sys
import tempfile


def test_plugin(plugin_path: str) -> dict:
    """Test a plugin in an isolated subprocess.

    Returns {passed: bool, errors: [...], tools: [...]}
    """
    test_script = f'''
import sys, json, traceback
result = {{"passed": True, "errors": [], "tools": []}}
try:
    # 1. Import the module
    import importlib.util
    spec = importlib.util.spec_from_file_location("plugin_under_test", {plugin_path!r})
    mod = spec.loader.load_module()

    # 2. Call register()
    if not hasattr(mod, "register"):
        result["errors"].append("Missing register() function")
        result["passed"] = False
    else:
        tools = mod.register()
        if not isinstance(tools, list):
            result["errors"].append("register() must return a list")
            result["passed"] = False
        else:
            for t in tools:
                if not hasattr(t, "name") or not hasattr(t, "description"):
                    result["errors"].append(f"Tool missing name/description: {{t}}")
                    result["passed"] = False
                else:
                    result["tools"].append(t.name)

except Exception:
    result["errors"].append(traceback.format_exc())
    result["passed"] = False

print(json.dumps(result))
'''
    proc = subprocess.run(
        [sys.executable, "-c", test_script],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return {"passed": False, "errors": [proc.stderr[-500:]], "tools": []}
    return json.loads(proc.stdout)
```

### Extended sandbox: conversation test

For higher-confidence changes (prompt edits, subagent rewrites), run a **test conversation** against the modified agent:

```python
def test_conversation(plugin_path: str, test_cases: list[dict]) -> dict:
    """Spin up a temporary agent with the plugin and run test conversations.

    Each test_case: {"input": "...", "expect_tool_calls": ["tool_name"],
                      "expect_contains": "substring"}
    """
    # 1. Build a temporary agent with the new plugin loaded
    # 2. Run each test case input through it
    # 3. Check expectations
    # 4. Return pass/fail per case
```

This is the "integration test" level — slower but catches behavioral regressions.

## The Registry

Tracks what's active, what's previous, and enables instant rollback:

```json
{
  "plugins": {
    "tools/custom/stock_lookup.py": {
      "active_version": "2",
      "previous_version": "1",
      "activated_at": "2026-03-21T10:00:00Z",
      "status": "active",
      "failure_count": 0,
      "max_failures_before_rollback": 3
    }
  },
  "prompts": {
    "system_prompt.md": {
      "active_hash": "a1b2c3...",
      "previous_hash": "d4e5f6...",
      "activated_at": "2026-03-21T09:00:00Z"
    }
  }
}
```

**Rollback** = swap `active_version` and `previous_version`, rebuild graph. Instant, no restart.

## Hot-Swap Mechanism

The key insight: the orchestrator **already rebuilds the agent graph on every call** (or can be made to). So hot-swapping is just:

1. Update the plugin files on disk
2. Update the registry
3. Next conversation turn picks up the new tools automatically

For **immediate** effect mid-conversation (without waiting for next turn):

```python
# prax/core/plugin_loader.py

class PluginLoader:
    def __init__(self):
        self._tools: list = []
        self._version: int = 0
        self._lock = threading.Lock()

    def load_all(self) -> list:
        """Scan plugins/ and load all registered tools."""
        tools = []
        for plugin_file in Path("prax/plugins/tools").rglob("*.py"):
            mod = self._import_plugin(plugin_file)
            if hasattr(mod, "register"):
                tools.extend(mod.register())
        with self._lock:
            self._tools = tools
            self._version += 1
        return tools

    def get_tools(self) -> list:
        """Called by orchestrator on every agent invocation."""
        with self._lock:
            return list(self._tools)

    def hot_swap(self, plugin_path: str) -> dict:
        """Replace a single plugin and rebuild the tool list."""
        # 1. Sandbox test
        result = test_plugin(plugin_path)
        if not result["passed"]:
            return {"error": "Sandbox test failed", "details": result}

        # 2. Reload just that module
        # 3. Rebuild full tool list
        self.load_all()
        return {"status": "swapped", "version": self._version}
```

The orchestrator calls `plugin_loader.get_tools()` instead of a static list. When a plugin is swapped, the very next `agent.run()` call uses the new tools.

## Agent-Facing Tools

The agent gets these meta-tools for self-modification:

| Tool | Purpose |
|------|---------|
| `plugin_list` | List all active plugins with versions and status |
| `plugin_read(path)` | Read a plugin's source code |
| `plugin_write(path, code)` | Write/update a plugin (triggers sandbox) |
| `plugin_test(path)` | Run sandbox tests without activating |
| `plugin_activate(path)` | Activate a tested plugin (hot-swap) |
| `plugin_rollback(path)` | Revert to previous version |
| `plugin_status(path)` | Check health (failure count, uptime) |
| `prompt_read(name)` | Read a system prompt |
| `prompt_write(name, content)` | Update a system prompt (triggers test conversation) |
| `prompt_rollback(name)` | Revert prompt to previous version |

## Auto-Rollback

The system monitors tool execution at runtime. If a plugin's tool raises exceptions repeatedly, it's automatically rolled back:

```python
# In the orchestrator's tool execution wrapper:

def _execute_tool_with_monitoring(tool, args):
    try:
        result = tool.invoke(args)
        registry.record_success(tool.name)
        return result
    except Exception as e:
        failures = registry.record_failure(tool.name)
        if failures >= registry.max_failures(tool.name):
            logger.warning("Auto-rolling back %s after %d failures", tool.name, failures)
            plugin_loader.rollback(tool.name)
        raise
```

## What Changes vs. Current System

| Current (codegen_service.py) | New (plugin architecture) |
|-----|-----|
| Copies files into live repo | Writes to plugins/ directory only |
| Werkzeug reloader restarts entire app | Hot-swap rebuilds tool list, no restart |
| A bad file kills the process | Subprocess sandbox catches errors first |
| Rollback = git revert + restart | Rollback = registry pointer swap, instant |
| Agent can modify ANY file | Agent can only modify plugins/ |
| No runtime monitoring | Auto-rollback on repeated failures |
| Tests run in worktree (slow) | Tests run in subprocess (fast) |

## Migration Path

1. **Phase 1: Plugin loader** ✅ — `PluginLoader` discovers and imports plugins from `plugins/tools/`. The `tool_registry.py` aggregates built-in + plugin + manual tools. The orchestrator calls `get_registered_tools()` which pulls from the loader.

2. **Phase 2: Sandbox + registry** ✅ — `sandbox.py` validates plugins in a subprocess. `registry.py` tracks versions in JSON. Agent-facing tools (`plugin_write`, `plugin_activate`, etc.) are available.

3. **Phase 3: Auto-rollback** ✅ — `MonitoredTool` wrapper intercepts every plugin tool invocation, records success/failure, and triggers auto-rollback after 3 consecutive failures. The orchestrator detects plugin version changes and rebuilds its agent graph.

4. **Phase 4: Prompt + LLM config plugins** ✅ — System prompt extracted to `plugins/prompts/system_prompt.md` with `{{AGENT_NAME}}` variable expansion. `PromptManager` handles read/write/rollback with content-hash versioning. `llm_config.py` reads `plugins/configs/llm_routing.yaml` for per-component LLM provider/model/temperature overrides (hot-reloaded on every call). Agent has `prompt_read/write/rollback/list` and `llm_config_read/update` tools.

5. **Phase 5: Codegen coexistence** ✅ — `codegen_service.py` remains for kernel-level changes (staging clone + verify + PR workflow). The plugin system handles hot-swappable components. Both paths are available to the agent.

6. **Phase 6: Folder-per-plugin + plugin repo** ✅ — Plugins now use a folder layout (`name/plugin.py` + `name/README.md`). Recursive discovery handles both folder-based and flat (legacy) plugins. Reader tools (NPR, web summary, PDF, YouTube, arXiv, Deutschlandfunk) migrated from hardcoded `tools.py` wrappers to built-in plugins. `repo.py` manages a separate private git repo via SSH deploy key (base64 from `.env`). Agent-created plugins are pushed to a branch for user review. `catalog.py` auto-generates `CATALOG.md` on every load, listing all plugins with versions and tools. `plugin_catalog` tool exposes the catalog to the agent. System prompt updated with setup guidance so the agent proactively helps users configure missing features.

## Implementation Files

| File | Purpose |
|------|---------|
| `prax/plugins/__init__.py` | Package init |
| `prax/plugins/loader.py` | Recursive plugin discovery (folder + flat), hot-swap, version tracking, auto-rollback, catalog generation |
| `prax/plugins/sandbox.py` | Subprocess-isolated plugin validation |
| `prax/plugins/registry.py` | JSON-based version registry with failure counting |
| `prax/plugins/monitored_tool.py` | Runtime monitoring via StructuredTool delegation |
| `prax/plugins/repo.py` | Plugin repository: SSH key auth, clone, commit, push to private repo branch |
| `prax/plugins/catalog.py` | Auto-generated CATALOG.md from plugin metadata (no imports, regex-based) |
| `prax/plugins/prompt_manager.py` | System prompt loading, writing, rollback |
| `prax/plugins/llm_config.py` | Per-component LLM routing from YAML |
| `prax/plugins/prompts/system_prompt.md` | Hot-swappable system prompt with setup guidance |
| `prax/plugins/configs/llm_routing.yaml` | Per-component LLM config |
| `prax/plugins/tools/custom/` | Agent-created tool plugins (folder-per-plugin) |
| `prax/plugins/tools/npr_podcast/` | Built-in plugin: NPR News Now podcast |
| `prax/plugins/tools/web_summary/` | Built-in plugin: webpage summary + TTS |
| `prax/plugins/tools/pdf_reader/` | Built-in plugin: PDF text extraction |
| `prax/plugins/tools/youtube_reader/` | Built-in plugin: YouTube transcription |
| `prax/plugins/tools/arxiv_reader/` | Built-in plugin: arXiv paper fetcher |
| `prax/plugins/tools/deutschlandfunk/` | Built-in plugin: German radio news |
| `prax/agent/plugin_tools.py` | 15 agent-facing tools for self-modification (incl. plugin_catalog) |
| `tests/test_plugin_system.py` | 40+ tests covering all plugin components |

## Resolved Questions

- **Dependency management**: Decided on option (c) — only stdlib + already-installed packages. The sandbox test catches missing imports before activation. If a plugin needs a new package, the human installs it.

- **Plugin namespacing**: Yes — agent-created plugins live in `plugins/tools/custom/` (or the plugin repo) with path traversal protection. The `_safe_plugin_path()` function blocks any attempt to write outside the base directory.

- **Multi-user**: The orchestrator snapshots the tool list via `_rebuild_if_needed()` at the start of each `run()` call. In-flight conversations use the tools they started with; new conversations pick up changes.

- **Persistence across restarts**: Plugin files are on disk, registry is a JSON file. On startup, `get_plugin_loader()` calls `load_all()` which recursively scans `plugins/tools/` (including `custom/`) and the plugin repo directory, rebuilding the tool list.

- **Plugin repo isolation**: Agent-created plugins push to a separate private git repo on a configurable branch. The user reviews and cherry-picks into the main repo. SSH deploy key is base64-encoded in `.env`.

- **Folder-per-plugin**: Each plugin gets its own directory with `plugin.py` + `README.md`. The loader uses recursive discovery, supporting both folder-based (`name/plugin.py`) and flat (`name.py`) layouts for backward compatibility.

- **Reader-to-plugin migration**: Reader tools (NPR, web summary, PDF, YouTube, arXiv, Deutschlandfunk) are now built-in plugins under `plugins/tools/`. They import from `prax.readers.*` and `prax.services.*` — the underlying implementations remain unchanged. The old tool wrappers in `tools.py` have been removed.

- **MonitoredTool recursion**: Changed from BaseTool subclass to StructuredTool delegation to avoid Pydantic annotation recursion in LangChain's type introspection.

## Open Questions (remaining)

- **Prompt testing**: What constitutes a "passing" prompt change? A test conversation framework would validate that the agent still responds to basic inputs and tool calls still work. Not yet implemented — prompt changes are trusted (with rollback as safety net).

- **Subagent plugins**: Subagent tool configurations are currently hardcoded in `subagent.py`. Making subagent definitions hot-swappable (custom subagent configs in `plugins/subagents/`) is a natural next step.
