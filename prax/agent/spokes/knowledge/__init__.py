"""Knowledge spoke — notes, projects, and knowledge management.

Handles note creation/search/linking, research project organization,
and content ingestion (URL-to-note, PDF-to-note).
"""
from prax.agent.spokes.knowledge.agent import build_spoke_tools

__all__ = ["build_spoke_tools"]
