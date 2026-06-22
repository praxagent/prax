"""Deployment / reachability introspection tool.

Gives Prax ground truth about how he's reachable (Tailscale / ngrok / local,
the public URL, serve mappings, and TEAMWORK_BASE_URL) so he can answer access
questions correctly instead of inferring from source code.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def deployment_info() -> str:
    """Report how this Prax instance is deployed and reachable from the network.

    Use this when a user asks why they can't reach TeamWork / a shared link, what
    the public URL is, or whether Prax is behind Tailscale/ngrok. Reports the
    runtime (Docker/local), Tailscale status + MagicDNS hostname, any
    `tailscale serve` mappings, the best-guess public base URL, TEAMWORK_BASE_URL,
    and advisories (e.g. a localhost base URL that off-network users can't reach).
    """
    from prax.services.deployment_info import format_report
    try:
        return format_report()
    except Exception as e:
        return f"Could not determine deployment info: {e}"


def build_deployment_tools() -> list:
    return [deployment_info]
