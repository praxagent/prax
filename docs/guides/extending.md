# Extending the Agent

[← Guides](README.md)

### Plugin System (recommended — hot-swappable at runtime)

The agent can create and manage its own tool plugins at runtime. Plugins use a **folder-per-plugin** layout and are validated in a subprocess sandbox before activation. No restart needed — the orchestrator rebuilds its tool graph automatically.

**Plugin layout:**

```
prax/plugins/tools/
  npr_podcast/          ← Built-in plugin (ships with repo)
    plugin.py
    README.md
  pdf_reader/
    plugin.py
    README.md
  custom/               ← Agent-created plugins
    weather/
      plugin.py
      README.md
  CATALOG.md            ← Auto-generated plugin listing
```

**Plugin format** — a Python module with a `register()` function:

```python
# prax/plugins/tools/custom/weather/plugin.py
from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Weather lookup for any city"

def register(caps):
    @tool
    def weather_lookup(city: str) -> str:
        """Get the current weather for a city."""
        resp = caps.http_get(f"https://wttr.in/{city}?format=3")
        return resp.text

    return [weather_lookup]
```

> **Note:** `register(caps)` receives a [`PluginCapabilities`](#plugin-security) instance.
> Use `caps.http_get()` instead of `requests.get()`, `caps.build_llm()` instead of
> importing the LLM factory, and `caps.get_config()` instead of reading `prax.settings`
> directly. Zero-arg `register()` still works for backward-compatible built-in plugins.

**Lifecycle:**

1. **Write** — `plugin_write("weather", code)` creates the folder, saves `plugin.py` + `README.md`, runs sandbox tests
2. **Activate** — `plugin_activate("weather")` hot-swaps it into the live agent
3. **Monitor** — runtime failures are counted; after 3 consecutive failures, the plugin auto-rolls back
4. **Rollback** — `plugin_rollback("weather")` reverts to the previous version instantly
5. **Catalog** — `plugin_catalog()` shows all available plugins with versions and tools

**Workspace sync (optional):** Prax stores custom plugins and user files in a git-backed workspace. You can push this workspace to a **private** remote for backup and review. Prax verifies the remote is private before pushing — public repos are refused.

**Setting up workspace sync (step by step):**

**Step 1 — Create a private GitHub repo for the workspace:**

Go to [github.com/new](https://github.com/new), create a repo (e.g. `prax-workspace`), and **make sure it is set to Private**. Do NOT initialize it with a README.

**Step 2 — Generate an SSH deploy key:**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/prax_deploy_key -N "" -C "prax-workspace"
```

This creates two files:
- `~/.ssh/prax_deploy_key` — the **private** key (stays on your server, goes into `.env`)
- `~/.ssh/prax_deploy_key.pub` — the **public** key (goes onto GitHub)

**Step 3 — Add the public key to GitHub as a deploy key:**

1. Go to your repo → **Settings** → **Deploy keys** → **Add deploy key**
2. Title: `Prax workspace`
3. Key: paste the output of `cat ~/.ssh/prax_deploy_key.pub`
4. **Check "Allow write access"** — this is required for Prax to push
5. Click **Add key**

**Step 4 — Make the private key available to Prax:**

*Option A — Bind-mount (preferred, avoids env var leakage):*

```bash
# In docker-compose.yml, add under app.volumes:
#   - ~/.ssh/prax_deploy_key:/run/secrets/prax_ssh_key:ro
```

*Option B — Base64 in `.env` (simpler, but env vars can leak in crash dumps, APM traces, `/proc/self/environ`, and child processes):*

```bash
cat ~/.ssh/prax_deploy_key | base64 | tr -d '\n'
```

Copy the output (one long string) and add to your Prax `.env`:

```bash
PRAX_SSH_KEY_B64=<paste the base64 string here>
```

**Step 5 — Tell Prax the remote URL:**

In a conversation with Prax, say: *"Set my workspace remote to git@github.com:yourname/prax-workspace.git"*

Prax will verify the repo is private, then set it up. After that, say *"push my workspace"* any time to sync.

> **Security:** Prax checks the GitHub/GitLab API before every push to confirm the remote repo is private. If someone changes the repo to public, Prax will refuse to push.

**Importing shared plugins:** Users can share plugin repos publicly. Import them with `plugin_import("https://github.com/someone/cool-tools.git")` — they're added as git submodules in the workspace. `plugin_import_list` shows what's installed, `plugin_import_remove` uninstalls.

**Workspace .gitignore:** Every workspace automatically gets a `.gitignore` that blocks media files (mp3, mp4, wav, etc.), LaTeX build artifacts (aux, log, nav, etc.), and Python caches. PDFs, `.tex` files, and text are committed normally.

The agent also has tools for modifying its own system prompt (`prompt_write`, `prompt_rollback`) and LLM routing (`llm_config_update`) — all without restart.

| Tool | Purpose |
|------|---------|
| `plugin_list` | List all active plugins with versions |
| `plugin_read` / `plugin_write` | Read/write plugin source code |
| `plugin_test` / `plugin_activate` | Sandbox test / hot-swap activation |
| `plugin_rollback` / `plugin_remove` | Revert or remove a plugin |
| `plugin_status` | Health: version, failure count, auto-rollback threshold |
| `plugin_catalog` | Auto-generated listing of all available plugins |
| `plugin_import` / `plugin_import_activate` / `plugin_import_remove` / `plugin_import_list` | Import shared plugins from public repos (git submodules), security review |
| `workspace_set_remote` / `workspace_push` | Configure and push workspace to a private remote |
| `workspace_share_file` / `workspace_unshare_file` | Publish/unpublish workspace files via ngrok (opt-in, token-based) |
| `sandbox_install` | Install system packages in the persistent sandbox |
| `sandbox_rebuild` | Edit sandbox Dockerfile and rebuild the container image |
| `prompt_read` / `prompt_write` / `prompt_rollback` | System prompt management |
| `prompt_list` | List all prompt files with version info |
| `llm_config_read` / `llm_config_update` | Per-component LLM provider/model/temperature routing |
| `source_read` / `source_list` | Read any source file or list directories in the codebase |

**Plugin priority:** Workspace custom plugins override built-in ones when they define tools with the same name. Priority: workspace plugins > built-in. This lets Prax fix or improve any built-in tool by writing a better version.

**Example plugins — [prax-plugins](https://github.com/praxagent/prax-plugins):** A collection of open-source plugins. Install one by telling Prax: *"Import the txt2presentation plugin from https://github.com/praxagent/prax-plugins"* — or install them all: *"Import all plugins from https://github.com/praxagent/prax-plugins"*. See its README for how to create your own plugins.

**Architecture:** See [SELF_MODIFY_PLAN.md](SELF_MODIFY_PLAN.md) for the full design rationale.

### Manual Tool Registration (for deployment-time extensions)

For tools that should be registered at startup (not runtime), use the tool registry before the Flask app is created:

```python
from langchain_core.tools import tool
from prax.agent.tool_registry import register_tool

@tool
def city_guide(city: str) -> str:
    """Return travel resources for a city."""
    return f"Here are resources for {city}"

register_tool(city_guide)
```

Registered tools automatically become available to both SMS and voice flows without editing the blueprints.

### Per-User Workspace Locking

Prax uses a per-user `threading.Lock` to prevent concurrent git operations on the same workspace. Every service or tool that writes to the workspace acquires it via `get_lock(user_id)`:

```python
from prax.services.workspace_service import get_lock, ensure_workspace

with get_lock(user_id):
    root = ensure_workspace(user_id)
    # … read/write files, git commit …
```

**The lock is NOT reentrant.** `threading.Lock` will deadlock if the same thread tries to acquire it twice. This is the most common cause of silent hangs — no error, no log, just a tool that never returns.

**How deadlocks happen:**

```python
# ❌ BAD — deadlock: tool holds the lock, then calls a service that also takes it
@tool
def my_tool():
    with get_lock(uid):           # acquires lock
        data = read_config(root)
        publish_something(uid)    # internally calls get_lock(uid) → deadlock

# ✅ GOOD — release the lock before calling functions that need it
@tool
def my_tool():
    with get_lock(uid):           # acquires lock
        data = read_config(root)  # quick I/O under lock
    # lock released
    publish_something(uid)        # free to acquire its own lock
```

**Rules for safe locking:**

1. **Hold the lock for the shortest possible scope** — read config, write a file, commit — then release.
2. **Never call a service function while holding the lock** unless you have verified the service does NOT acquire the same lock internally. Services like `publish_notes()`, `publish_news()`, `run_hugo()`, and `generate_hugo_content()` all take the lock.
3. **Never nest `get_lock()` calls** for the same user ID, even indirectly.
4. **If a tool hangs silently**, check for lock re-entry: trace the call chain from the tool through every function it calls, looking for `get_lock`.

**Debugging a deadlock in production:**

If you see a `Tool X starting` log with no matching `Tool X finished`, it's almost certainly a deadlock. To confirm:
- Add logging around `get_lock()` calls (the lock itself doesn't log)
- Check whether the tool's execution path calls any service that acquires the lock
- The fix is always the same: release the lock before calling into the service

### Adding a spoke

Prefer spokes over adding tools directly to the orchestrator — the
orchestrator's tool count is kept under Anthropic's ~50-tool
accuracy threshold (~42 today). Every new top-level tool erodes
that margin.

**Minimum spoke skeleton** (see `prax/agent/spokes/tasks/` for a
fresh reference implementation):

```
prax/agent/spokes/<name>/
  __init__.py        # exports build_spoke_tools
  agent.py           # SYSTEM_PROMPT, build_tools(), delegate_<name>(), build_spoke_tools()
```

Steps:

1. Create the folder and copy the shape from an existing spoke
   (`tasks` is the smallest; `workspace` is a good fuller example).
2. Implement `build_tools()` returning the spoke's internal tool
   list — these are the tools the sub-agent can use, not the
   orchestrator.
3. Implement `@tool def delegate_<name>(task: str)` that calls
   `run_spoke(...)` with the right `config_key`, system prompt, and
   tool list.
4. Register in `prax/agent/spokes/__init__.py` —
   `build_all_spoke_tools()` imports and concatenates the spoke's
   `build_spoke_tools()`.
5. Remove any tools that used to be orchestrator-level but belong in
   this spoke.

### Architectural boundaries (mechanical)

`scripts/check_layers.py` runs in `make ci` and enforces:

- **Plugin isolation.** Code under `prax/plugins/tools/**` must not
  import `prax.services.*` or `prax.agent.*`. Go through the
  capability gateway (`prax.plugins.capabilities`).
- **No reverse dep.** `prax/services/**` must not import
  `prax.agent.*`.  Two carve-outs: `prax.agent.llm_factory` and
  `prax.agent.user_context` — both should eventually move out of
  `prax.agent`.
- **Services are HTTP-agnostic.** `prax/services/**` must not import
  `prax.blueprints.*`.

If your change hits one of these, the CI output points you at the
allowlist. Either fix the import or (if it's genuine debt) add to
`ALLOWLIST` with a comment explaining why.
