"""Self-diagnostic tool -- Prax's equivalent of ``brew doctor``.

Checks LLM configuration, sandbox health, plugin status, workspace integrity,
TeamWork connectivity, scheduler state, the health monitor, recurring log
warnings, and the head of the records' hash chain.

Every reading comes from the ``settings`` object, never ``os.environ``:
pydantic loads ``.env`` itself and does not export it, so on a host-process
deployment an environment lookup sees nothing for a value that lives only in
``.env`` -- which is how this tool used to report "TeamWork: not configured"
and a missing API key on a box where both were set.
"""
from __future__ import annotations

import logging
import os
import time

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def prax_doctor() -> str:
    """Run self-diagnostics on Prax's health.

    Checks LLM configuration (builds a model for every enabled tier), sandbox
    availability, plugin status, workspace integrity, TeamWork connectivity,
    scheduler state, the health monitor's verdict, the warnings and errors
    that keep recurring in the log, and the records' hash chain.

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
    checks.append(_check_workspace())
    checks.append(_check_teamwork())
    checks.append(_check_scheduler())
    checks.append(_check_settings())
    checks.append(_check_health_monitor())
    checks.append(_check_log_health())
    checks.append(_check_records())

    ok = sum(1 for c in checks if c.startswith("[OK]"))
    warn = sum(1 for c in checks if c.startswith("[WARN]"))
    fail = sum(1 for c in checks if c.startswith("[FAIL]"))

    header = f"Prax Doctor -- {ok} healthy, {warn} warnings, {fail} errors"
    sep = "-" * len(header)
    return f"{header}\n{sep}\n" + "\n".join(checks)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def _check_llm() -> str:
    try:
        from prax.agent.llm_factory import build_llm, probe_builds
        from prax.agent.model_tiers import get_available_tiers
        from prax.settings import settings

        provider = settings.default_llm_provider

        # Construct the model for every enabled tier through the real factory:
        # it is the code that raises on a missing key, an unsupported provider
        # or an open circuit breaker, so no hand-kept provider->key map can
        # drift from it.  Construction makes no network call.
        built: list[str] = []
        failed: list[str] = []
        with probe_builds():
            for tc in get_available_tiers():
                try:
                    build_llm(tier=tc.tier.value)
                    built.append(f"{tc.tier.value}={tc.model}")
                except Exception as e:
                    failed.append(f"{tc.tier.value} ({tc.model}): {_error_text(e)}")

        if failed:
            return f"[FAIL] LLM: {provider} — cannot build {'; '.join(failed)}"
        if not built:
            return (
                f"[WARN] LLM: {provider} — no model tier is enabled "
                f"(a deprecated <TIER>_ENABLED=false wins over ENABLED_TIERS); "
                f"every call falls back to BASE_MODEL={settings.base_model}"
            )
        return f"[OK] LLM: {provider}, tiers: {', '.join(built)}"
    except Exception as e:
        return f"[FAIL] LLM: {_error_text(e)}"


def _check_sandbox() -> str:
    try:
        from prax.settings import settings

        if not settings.sandbox_available:
            return "[OK] Sandbox: disabled (SANDBOX_ENABLED=false)"

        from prax.services.sandbox_bridge import configured_client as get_client

        if get_client().health():
            return "[OK] Sandbox: persistent mode, healthy"
        return "[WARN] Sandbox: enabled but not reachable (check the sandbox container)"
    except Exception as e:
        return f"[FAIL] Sandbox: {e}"


def _check_plugins() -> str:
    try:
        from prax.plugins.loader import get_plugin_loader

        report = get_plugin_loader().health_report()
        summary = f"{len(report.plugins)} plugin(s), {report.tool_count} tool(s) loaded"
        if report.problems:
            return f"[WARN] Plugins: {summary}; needs attention: {'; '.join(report.problems)}"
        return f"[OK] Plugins: {summary}"
    except Exception as e:
        return f"[FAIL] Plugins: {e}"


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
        from prax.settings import settings

        tw_url = settings.teamwork_url.rstrip("/")
        if not settings.teamwork_active:
            if tw_url:
                # Exactly the "URL set but silently skipped" trap the legacy
                # switch exists to allow, so it is a warning, not a shrug.
                return (
                    f"[WARN] TeamWork: off — TEAMWORK_URL={tw_url} is set, but the "
                    f"deprecated TEAMWORK_ENABLED=false overrides it (unset "
                    f"TEAMWORK_ENABLED to connect, or clear TEAMWORK_URL)"
                )
            return "[OK] TeamWork: not configured — TEAMWORK_URL is empty (standalone mode)"

        import requests

        try:
            resp = requests.get(f"{tw_url}/health", timeout=3)
        except Exception as e:
            return f"[WARN] TeamWork: {tw_url} configured but unreachable ({type(e).__name__})"
        if resp.ok:
            return f"[OK] TeamWork: connected at {tw_url}"
        return f"[WARN] TeamWork: {tw_url}/health returned {resp.status_code}"
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
        from prax.settings import _WEAK_SECRET_KEYS, settings

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

        secret = settings.flask_secret_key or ""
        if secret.lower().strip() in _WEAK_SECRET_KEYS or len(secret) < 16:
            issues.append("FLASK_SECRET_KEY is weak or a placeholder")

        if issues:
            return f"[WARN] Settings: {'; '.join(issues)}"
        return (
            f"[OK] Settings: {settings.agent_name}, "
            f"provider={settings.default_llm_provider}"
        )
    except Exception as e:
        return f"[FAIL] Settings: {e}"


def _check_health_monitor() -> str:
    try:
        from prax.settings import settings
        if not settings.health_monitor_enabled:
            return "[OK] Health Monitor: disabled (HEALTH_MONITOR_ENABLED=false)"

        from prax.agent.health_monitor import get_check
        check = get_check()
        age = f"checked {int(time.time() - check.timestamp)}s ago"

        if check.overall == "healthy":
            return (
                f"[OK] Health Monitor: {check.overall} "
                f"({len(check.subsystems)} subsystems, {age})"
            )
        alert_summary = "; ".join(check.alerts[:3])
        prefix = "[WARN]" if check.overall == "degraded" else "[FAIL]"
        return f"{prefix} Health Monitor: {check.overall} ({age}) — {alert_summary}"
    except Exception as e:
        return f"[WARN] Health Monitor: {e}"


def _check_records() -> str:
    # The records' hash chain (prax/services/record_chain.py): its head, and
    # any change made outside Prax that the writers noticed since startup. The
    # full check is `python -m prax.services.record_chain verify`.
    try:
        from prax.services import record_chain
        s = record_chain.status()
        head = s["head"]
        base = f"chain head seq={head['seq']} hash={head['hash'][:16]}"
        issues = []
        if s["alerts"]:
            shown = "; ".join(f"{a['file']}: {a['detail']}" for a in s["alerts"][-3:])
            issues.append(f"{len(s['alerts'])} change(s) made outside Prax noticed ({shown})")
        if s["journal_errors"]:
            issues.append(f"{s['journal_errors']} journal write(s) failed")
        if issues:
            return f"[WARN] Records: {base}; " + "; ".join(issues)
        return f"[OK] Records: {base}"
    except Exception as e:
        return f"[WARN] Records: {e}"


def _check_log_health() -> str:
    # In-process counts only.  The log file is never read: it is unrotated
    # (tens of MB) and every line of it is formatted user data.
    try:
        from prax.settings import settings
        if not settings.log_health_enabled:
            return (
                "[OK] Log health: off — set LOG_HEALTH_ENABLED=true and restart "
                "to count recurring warnings and errors by call site"
            )

        from prax.services import log_health
        handler = log_health.get_handler()
        if handler is None:
            return (
                "[WARN] Log health: LOG_HEALTH_ENABLED is true but no counter is "
                "installed (app startup installs it; restart Prax)"
            )

        since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(handler.installed_at))
        sites = handler.group_count()
        overflow = (
            f", plus {handler.overflow} record(s) past the {handler.max_groups}-site cap"
            if handler.overflow else ""
        )
        threshold = settings.log_health_warn_count
        rows = handler.summarize(settings.log_health_top_n)
        if not rows or rows[0]["count"] < threshold:
            return (
                f"[OK] Log health: {sites} warning/error call site(s) since {since}"
                f"{overflow}; none has logged {threshold}+ times"
            )
        listing = "\n".join(
            f"    {r['count']} × {r['level']} {r['location']} — "
            f"{r['template'] or '(message not kept)'}"
            for r in rows
        )
        return (
            f"[WARN] Log health: a call site has logged {threshold}+ warnings/errors "
            f"since {since} (top {len(rows)} of {sites} site(s){overflow}):\n{listing}"
        )
    except Exception as e:
        return f"[WARN] Log health: {e}"


def build_doctor_tools() -> list:
    """Return the doctor tool for the main agent."""
    return [prax_doctor]
