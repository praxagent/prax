"""Bridge between prax settings and the prax-sandbox client.

This is the one place prax knows the sandbox's configuration shape. It builds a
:class:`SandboxConfig` from prax settings plus prax-specific callbacks (TeamWork
live-output, the git-backed workspace) and hands out a configured
``SandboxClient``. The sandbox package itself has no dependency on prax — prax
injects everything it needs through here.

prax modules that drive the sandbox import :func:`configured_client` from this
module instead of ``prax_sandbox_client.get_client`` (which would use the
control plane's bare built-in defaults).
"""
from __future__ import annotations

from prax_sandbox_client import SandboxClient, SandboxConfig


def _on_output(label: str, text: str) -> None:
    """Stream incremental coding-agent output to TeamWork (best-effort)."""
    try:
        from prax.services.teamwork_hooks import push_live_output
        push_live_output(label, text, status="running", append=True)
    except Exception:
        pass


def _resolve_workspace(user_id: str) -> str:
    """Ensure + return the user's git-backed workspace root."""
    from prax.services.workspace_service import ensure_workspace
    return ensure_workspace(user_id)


def _commit(root: str, message: str) -> None:
    """Commit the workspace git repo after archiving a sandbox solution."""
    from prax.services.workspace_service import git_commit
    git_commit(root, message)


def _tls_verify(value: str):
    """Map the SANDBOX_TLS_VERIFY string to bool | CA-path."""
    v = (value or "").strip()
    if v.lower() in {"true", "1", "yes", ""}:
        return True
    if v.lower() in {"false", "0", "no"}:
        return False
    return v  # a path to a CA bundle


def build_config() -> SandboxConfig:
    """Build a SandboxConfig from the current prax settings + callbacks.

    When ``SANDBOX_DAEMON_URL`` is set the client drives a remote daemon; the
    callbacks below stay client-side (the HTTP transport ignores them).
    """
    from prax.settings import settings
    return SandboxConfig(
        host=settings.sandbox_host,
        image=settings.sandbox_image,
        persistent=settings.sandbox_persistent,
        workspace_dir=settings.workspace_dir,
        default_model=settings.sandbox_default_model,
        max_concurrent=settings.sandbox_max_concurrent,
        max_rounds=settings.sandbox_max_rounds,
        timeout=settings.sandbox_timeout,
        anthropic_key=settings.anthropic_key,
        openai_key=settings.openai_key,
        # Remote transport (empty daemon_url -> in-process, the default):
        daemon_url=settings.sandbox_daemon_url or None,
        daemon_token=settings.sandbox_daemon_token or None,
        tls_verify=_tls_verify(settings.sandbox_tls_verify),
        client_cert=settings.sandbox_client_cert or None,
        client_key=settings.sandbox_client_key or None,
        on_output=_on_output,
        resolve_workspace=_resolve_workspace,
        commit=_commit,
    )


def configured_client() -> SandboxClient:
    """Return a SandboxClient with prax's config installed in the control plane.

    Rebuilds the config from current settings on each call (cheap; keeps the
    control plane in sync if settings change, e.g. across tests).
    """
    return SandboxClient(build_config())


def install() -> None:
    """Install the prax-built config into the control plane (app startup hook)."""
    configured_client()
