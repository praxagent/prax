"""Sandbox spoke — code execution in isolated containers.

The sandbox agent manages coding sessions with an AI coding agent (OpenCode)
inside Docker containers, handling session lifecycle, artifact archival, and
package management.
"""
from prax.agent.spokes.sandbox.agent import build_spoke_tools

__all__ = ["build_spoke_tools"]
