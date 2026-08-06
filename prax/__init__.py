"""Prax — a safety-first agentic harness.

Most of Prax is imported from its submodules (``prax.agent``, ``prax.eval``,
``prax.services``). The one thing exported at the top level is the **execution
contract** another system uses to hand Prax work:

    from prax import execute
    result = execute(job_spec)   # JobSpec in, hashed artifacts out

Everything else stays behind its own import, so ``import prax`` remains cheap
and does not drag in the agent stack. ``execute`` is lazily resolved for the
same reason — the sandbox client is only imported when a job actually runs.
"""
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

__all__ = ["execute", "validate_job_spec", "JobSpecError", "__version__"]

try:
    __version__ = _pkg_version("prax")
except PackageNotFoundError:  # running from a source checkout without install
    __version__ = "0.0.0+unknown"


def __getattr__(name: str):
    """PEP 562 lazy export — keep ``import prax`` free of heavy dependencies."""
    if name in {"execute", "validate_job_spec", "JobSpecError"}:
        from prax import exec_api

        return getattr(exec_api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
