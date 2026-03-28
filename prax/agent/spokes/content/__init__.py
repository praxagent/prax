"""Content Editor spoke — multi-agent blog/article creation pipeline.

Coordinates research, writing, adversarial review, and Hugo publishing
through a multi-pass refinement loop.
"""
from prax.agent.spokes.content.agent import build_spoke_tools

__all__ = ["build_spoke_tools"]
