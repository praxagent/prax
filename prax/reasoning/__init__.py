"""Standalone reasoning capabilities — importable without the heavy Prax stack.

Deliberately light: only stdlib + a caller-supplied model callable, so these run
in Prax, standalone, or a Kaggle notebook (no Flask/Docker/network dependency).
"""
from prax.reasoning.worldmodel import CodeResult, run_code, world_model_solve

__all__ = ["CodeResult", "run_code", "world_model_solve"]
