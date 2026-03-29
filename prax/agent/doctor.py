"""Self-diagnostic tool -- Prax's equivalent of ``brew doctor``.

Checks LLM configuration, sandbox health, plugin status, spoke availability,
workspace integrity, TeamWork connectivity, and scheduler state.
"""
from __future__ import annotations

import logging
import os

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def prax_doctor() -> str:
    """Run self-diagnostics on Prax's health.

    Checks LLM configuration, sandbox availability, plugin status, spoke
    imports, workspace integrity, TeamWork connectivity, and scheduler state.

    Use this when:
    - Something isn't working and you want to understand why
    - After a restart to verify everything came up healthy
    - When the user reports problems
    - Proactively before complex multi-agent operations
    """
    checks: list[str] = []
    checks.append(_check_llm())
    checks.append(_check_sandbox())
    checks.append(_check_plugins())
    checks.append(_check_spokes())
    checks.append(_check_workspace())
    checks.append(_check_teamwork())
    checks.append(_check_scheduler())
    checks.append(_check_settings())

    ok = sum(1 for c in checks if c.startswith("[OK]"))
    warn = sum(1 for c in checks if c.startswith("[WARN]"))
    fail = sum(1 for c in checks if c.startswith("[FAIL]"))

    header = f"Prax Doctor -- {ok} healthy, {warn} warnings, {fail} errors"
    sep = "-" * len(header)
    return f"{header}\n{sep}\n" + "\n".join(checks)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_llm() -> str:
    try:
        from prax.agent.llm_factory import build_llm
        from prax.settings import settings

        provider = settings.default_llm_provider
        model = settings.base_model

        # Check API key presence for known providers
        key_vars = {
            "openai": "OPENAI_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
        }
        key_var = key_vars.get(provider)
        if key_var and not os.environ.get(key_var):
            return f"[FAIL] LLM: {provider} configured but {key_var} not set"

        # Verify each enabled tier can build an LLM object
        tiers_ok = []
        for tier in ("low", "medium", "high"):
            if getattr(settings, f"{tier}_enabled", False):
                try:
                    build_llm(tier=tier)
                    tiers_ok.append(tier)
                except Exception as e:
                    return f"[WARN] LLM: tier '{tier}' failed to build: {e}"

        return f"[OK] LLM: {provider}/{model}, tiers: {', '.join(tiers_ok)}"
    except Exception as e:
        return f"[FAIL] LLM: {e}"


def _check_sandbox() -> str:
    try:
        from prax.settings import settings

        if not settings.running_in_docker:
            import subprocess

            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0:
                return "[WARN] Sandbox: Docker not available (docker info failed)"
            return "[OK] Sandbox: Docker available (ephemeral mode)"

        # In Docker -- check if sandbox container is reachable
        sandbox_url = os.environ.get("SANDBOX_URL", "")
        if not sandbox_url:
            return "[WARN] Sandbox: running in Docker but SANDBOX_URL not set"

        import requests

        try:
            resp = requests.get(f"{sandbox_url}/health", timeout=3)
            if resp.ok:
                return f"[OK] Sandbox: persistent mode, healthy at {sandbox_url}"
            return f"[WARN] Sandbox: {sandbox_url} returned {resp.status_code}"
        except Exception:
            return f"[WARN] Sandbox: {sandbox_url} unreachable"
    except Exception as e:
        return f"[FAIL] Sandbox: {e}"


def _check_plugins() -> str:
    try:
        from prax.plugins.loader import get_plugin_loader

        loader = get_plugin_loader()
        tools = loader.get_tools()

        # Check for unhealthy plugins
        unhealthy: list[str] = []
        if hasattr(loader, "get_health"):
            health = loader.get_health()
            unhealthy = [name for name, ok in health.items() if not ok]

        plugins = loader.list_plugins() if hasattr(loader, "list_plugins") else []
        plugin_count = len(plugins) if plugins else "?"

        if unhealthy:
            return (
                f"[WARN] Plugins: {plugin_count} plugins, {len(tools)} tools, "
                f"unhealthy: {', '.join(unhealthy)}"
            )
        return f"[OK] Plugins: {plugin_count} plugins, {len(tools)} tools loaded"
    except Exception as e:
        return f"[FAIL] Plugins: {e}"


def _check_spokes() -> str:
    spoke_modules = {
        "browser": "prax.agent.spokes.browser",
        "content": "prax.agent.spokes.content",
        "finetune": "prax.agent.spokes.finetune",
        "knowledge": "prax.agent.spokes.knowledge",
        "sandbox": "prax.agent.spokes.sandbox",
        "sysadmin": "prax.agent.spokes.sysadmin",
    }
    import importlib

    loaded: list[str] = []
    failed: list[str] = []
    for name, module_path in spoke_modules.items():
        try:
            importlib.import_module(module_path)
            loaded.append(name)
        except Exception as e:
            failed.append(f"{name} ({e})")

    if failed:
        return (
            f"[WARN] Spokes: {len(loaded)} OK, "
            f"failed: {', '.join(failed)}"
        )
    return (
        f"[OK] Spokes: all {len(loaded)} importable "
        f"({', '.join(loaded)})"
    )


def _check_workspace() -> str:
    try:
        from prax.settings import settings

        ws_dir = settings.workspace_dir
        if not os.path.isdir(ws_dir):
            return f"[WARN] Workspace: directory '{ws_dir}' does not exist"
        if not os.access(ws_dir, os.W_OK):
            return f"[WARN] Workspace: directory '{ws_dir}' is not writable"

        user_dirs = [
            d
            for d in os.listdir(ws_dir)
            if os.path.isdir(os.path.join(ws_dir, d)) and not d.startswith(".")
        ]
        return f"[OK] Workspace: {len(user_dirs)} user workspace(s) in {ws_dir}"
    except Exception as e:
        return f"[FAIL] Workspace: {e}"


def _check_teamwork() -> str:
    try:
        tw_url = os.environ.get("TEAMWORK_URL", "")
        if not tw_url:
            return "[OK] TeamWork: not configured (standalone mode)"

        import requests

        try:
            resp = requests.get(f"{tw_url}/health", timeout=3)
            if resp.ok:
                return f"[OK] TeamWork: connected at {tw_url}"
            return f"[WARN] TeamWork: {tw_url} returned {resp.status_code}"
        except Exception:
            return f"[WARN] TeamWork: {tw_url} configured but unreachable"
    except Exception as e:
        return f"[FAIL] TeamWork: {e}"


def _check_scheduler() -> str:
    try:
        from prax.services.scheduler_service import scheduler_service

        if (
            not hasattr(scheduler_service, "scheduler")
            or scheduler_service.scheduler is None
        ):
            return "[WARN] Scheduler: not initialized"

        running = scheduler_service.scheduler.running
        jobs = scheduler_service.scheduler.get_jobs()
        if not running:
            return (
                f"[WARN] Scheduler: initialized but not running "
                f"({len(jobs)} jobs)"
            )
        return f"[OK] Scheduler: running, {len(jobs)} active job(s)"
    except Exception as e:
        return f"[WARN] Scheduler: {e}"


def _check_settings() -> str:
    try:
        from prax.settings import settings

        issues: list[str] = []

        if settings.agent_max_tool_calls < 10:
            issues.append(
                f"agent_max_tool_calls={settings.agent_max_tool_calls} (very low)"
            )
        if settings.agent_max_tool_calls > 100:
            issues.append(
                f"agent_max_tool_calls={settings.agent_max_tool_calls} "
                f"(very high, cost risk)"
            )

        secret = os.environ.get("FLASK_SECRET_KEY", "")
        if not secret or secret == "change-me" or len(secret) < 16:
            issues.append("FLASK_SECRET_KEY is weak or default")

        if issues:
            return f"[WARN] Settings: {'; '.join(issues)}"
        return (
            f"[OK] Settings: {settings.agent_name}, "
            f"provider={settings.default_llm_provider}"
        )
    except Exception as e:
        return f"[FAIL] Settings: {e}"


def build_doctor_tools() -> list:
    """Return the doctor tool for the main agent."""
    return [prax_doctor]
