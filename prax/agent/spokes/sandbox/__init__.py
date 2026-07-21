"""Sandbox spoke — direct code execution in isolated containers.

The sandbox agent writes and runs code directly in the Docker container (shell,
file editing, package install) — no separate AI coding-agent session.
"""
from prax.agent.spokes.sandbox.agent import build_spoke_tools

__all__ = ["build_spoke_tools"]
