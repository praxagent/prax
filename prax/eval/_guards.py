"""Pre-run contamination and isolation assertions.

Before any eval task runs, we verify that ``PRAX_EVAL_DIR`` is
structurally isolated from Prax's normal operating scope.  Three hard
checks run fail-fast with clear errors:

1. ``PRAX_EVAL_DIR`` is outside the git repository root.
2. ``PRAX_EVAL_DIR`` is outside ``settings.workspace_dir``.
3. No path under ``settings.workspace_dir`` resolves inside
   ``PRAX_EVAL_DIR``.

If any check fails, the runner refuses to run.  This is cheap
insurance against accidentally putting eval data in a place Prax's
tools can reach.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


class EvalIsolationError(RuntimeError):
    """Raised when eval data is not properly isolated from Prax's scope."""


def _git_repo_root(start: Path) -> Path | None:
    """Return the git repo root for ``start``, or None if not a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return Path(result.stdout.strip()).resolve()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _is_inside(child: Path, parent: Path) -> bool:
    """Return True iff ``child`` is the same as or nested inside ``parent``."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def assert_eval_isolation(eval_dir: Path, workspace_dir: Path) -> None:
    """Raise ``EvalIsolationError`` unless ``eval_dir`` is properly isolated.

    Checks:

    1. ``eval_dir`` is not inside the current git repository root.
    2. ``eval_dir`` is not inside ``workspace_dir``.
    3. ``workspace_dir`` is not inside ``eval_dir``.

    All three must hold — if any fails, we cannot safely run an eval
    because Prax's tools could reach the eval data.
    """
    eval_dir = eval_dir.resolve()
    workspace_dir = workspace_dir.resolve()

    # Check 1: outside the git repo root
    repo_root = _git_repo_root(Path.cwd())
    if repo_root is None:
        # Not in a git repo — unusual but allowed (the runner might be
        # invoked from a non-repo script).  Skip this check.
        pass
    elif _is_inside(eval_dir, repo_root):
        raise EvalIsolationError(
            f"PRAX_EVAL_DIR ({eval_dir}) is inside the git repository "
            f"root ({repo_root}).  Eval data must live OUTSIDE the "
            f"repository so it cannot be committed or pushed.  Move "
            f"PRAX_EVAL_DIR to a sibling directory of the repo."
        )

    # Check 2: outside workspace_dir
    if _is_inside(eval_dir, workspace_dir):
        raise EvalIsolationError(
            f"PRAX_EVAL_DIR ({eval_dir}) is inside settings.workspace_dir "
            f"({workspace_dir}).  Prax's workspace tools (workspace_list, "
            f"workspace_read, workspace_search) would be able to read "
            f"eval ground-truth answers — a total contamination failure.  "
            f"Move PRAX_EVAL_DIR to a sibling of workspace_dir."
        )

    # Check 3: workspace_dir is not inside eval_dir
    if _is_inside(workspace_dir, eval_dir):
        raise EvalIsolationError(
            f"settings.workspace_dir ({workspace_dir}) is inside "
            f"PRAX_EVAL_DIR ({eval_dir}).  This would mean every write "
            f"to a user's workspace lands under the eval root — "
            f"inverted isolation, equally broken.  Fix one of the two "
            f"paths so they're siblings, not nested."
        )


def ensure_eval_dir(eval_dir: Path) -> Path:
    """Create ``eval_dir`` and its subdirectories if missing.

    Writes a ``.gitignore`` at the root containing ``*`` so an accidental
    ``git init`` inside the eval dir would ignore all content.  Also
    creates the ``runs/`` and ``gaia-cache/`` subdirectories.
    """
    eval_dir = eval_dir.resolve()
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "runs").mkdir(exist_ok=True)
    (eval_dir / "gaia-cache").mkdir(exist_ok=True)

    gitignore = eval_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Belt-and-suspenders: ignore everything if this dir is ever\n"
            "# accidentally turned into a git repo.  Eval data must NOT be\n"
            "# committed anywhere — see prax/eval/README.md.\n"
            "*\n",
            encoding="utf-8",
        )

    readme = eval_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# prax-evals — DO NOT COMMIT\n\n"
            "This directory holds Prax's external benchmark data, runs,\n"
            "traces, and artifacts.  It is **intentionally outside the\n"
            "gpt-transcriber git repo** to prevent contamination of\n"
            "Prax's workspace tools and to honor HuggingFace gated-dataset\n"
            "licensing terms (GAIA etc.).\n\n"
            "**Never commit anything under this directory anywhere.**\n\n"
            "See `gpt-transcriber/prax/eval/README.md` for the full rules.\n",
            encoding="utf-8",
        )

    return eval_dir


# Tools that must be disabled in eval mode because they have filesystem
# access outside Prax's designed scope.  This is defense-in-depth beyond
# the directory-isolation of PRAX_EVAL_DIR.
EVAL_MODE_TOOL_DENYLIST: frozenset[str] = frozenset({
    # Code generation tools — can touch the repo itself
    "self_improve_start",
    "self_improve_deploy",
    "self_improve_rollback",
    "self_improve_status",
    # Plugin writing — can write arbitrary files
    "plugin_write",
    "plugin_activate",
    "plugin_remove",
})
