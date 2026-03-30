"""Sysadmin spoke — plugin management, self-improvement, and system maintenance.

Consolidates all plugin/prompt/LLM/source management tools behind a single
delegation tool so the main orchestrator's tool list stays focused on the
user's task.
"""
from prax.agent.spokes.sysadmin.agent import build_spoke_tools

__all__ = ["build_spoke_tools"]
