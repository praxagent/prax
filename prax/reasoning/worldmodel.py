"""World-model reasoning — solve a problem by building an executable model of it.

The System-1 → System-2 move. Instead of answering in one forward pass, the model
**writes a Python program that models and solves the problem**; we execute it (in a
subprocess with a hard timeout — no Docker), and on failure feed the traceback back
for repair rounds. This externalizes reasoning into a *verifiable artifact* and lets
the model compute/search rather than pattern-match — the general form of the
executable-world-model approach (ARC, WorldCoder, Program-of-Thoughts).

Model-agnostic and sandbox-optional by construction:
- ``complete_fn(prompt) -> str`` is supplied by the caller (OpenRouter in the lab,
  a local model in a Kaggle notebook, a fake in tests).
- Code runs via ``subprocess`` (stdlib) with a timeout — no Docker, no network
  needed, importable with only the standard library.

Safety note: this executes model-generated code. It runs in a subprocess with a
timeout and no elevated privileges; hardening (restricted namespace / resource
limits / no-network enforcement) is a follow-up before any untrusted use.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

Complete = Callable[[str], str]

_SOLVE_PROMPT = (
    "You are solving a problem by MODELLING it as a Python program, not by "
    "guessing. Write a single self-contained Python script that constructs a "
    "model of the problem (its objects, quantities, constraints, or rules), "
    "computes the solution, and VERIFIES it where possible. The script must "
    "print the final answer on its own line in EXACTLY this format:\n"
    "FINAL: <answer>\n\n"
    "Use only the Python standard library (plus math). Do not read files or the "
    "network. Put the code in a single ```python fenced block.\n\n"
    "Problem:\n{problem}\n"
)

_REPAIR_SUFFIX = (
    "\n\nYour previous program:\n```python\n{code}\n```\n"
    "It did not produce a valid `FINAL:` line. Output/error was:\n{err}\n"
    "Fix the program and output the corrected single ```python block."
)

_CODE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_FINAL_RE = re.compile(r"^FINAL:\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class CodeResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


def run_code(code: str, timeout: float = 20.0) -> CodeResult:
    """Execute *code* in a subprocess with a hard timeout. Stdlib-only, no Docker."""
    fd, path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(code)
        # Minimal env: keep PATH so python resolves; drop the rest (incl. secrets).
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0"}
        proc = subprocess.run(
            [sys.executable, "-I", path], capture_output=True, text=True,
            timeout=timeout, env=env, cwd=tempfile.gettempdir(),
        )
        return CodeResult(proc.stdout, proc.stderr, proc.returncode)
    except subprocess.TimeoutExpired as exc:
        return CodeResult(exc.stdout or "", "TIMEOUT", -1, timed_out=True)
    except Exception as exc:  # noqa: BLE001
        return CodeResult("", f"{type(exc).__name__}: {exc}", -1)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _extract_code(text: str) -> str | None:
    m = _CODE_RE.search(text or "")
    return m.group(1).strip() if m else None


def _extract_final(text: str) -> str | None:
    hits = _FINAL_RE.findall(text or "")
    return hits[-1].strip() if hits else None


def world_model_solve(problem: str, complete_fn: Complete, *, max_rounds: int = 3,
                      timeout: float = 20.0) -> dict:
    """Solve *problem* by inducing + running an executable model, with repair.

    Returns ``{answer, via, rounds, code, trace}``:
    - ``via='code'`` — a program ran and produced a ``FINAL:`` answer.
    - ``via='text'`` — the model answered in prose (no code); ``answer`` is the
      ``FINAL:`` line if present, else None.
    - ``via='code-failed'`` — code kept failing; best-effort answer or None.
    """
    prompt = _SOLVE_PROMPT.format(problem=problem)
    trace: list[dict] = []
    code = last_out = last_err = ""
    for rnd in range(1, max_rounds + 1):
        resp = complete_fn(prompt) or ""
        code = _extract_code(resp)
        if not code:
            # No program — accept a FINAL line from prose if present.
            trace.append({"round": rnd, "via": "text"})
            return {"answer": _extract_final(resp), "via": "text",
                    "rounds": rnd, "code": None, "trace": trace}
        res = run_code(code, timeout=timeout)
        last_out, last_err = res.stdout, res.stderr
        final = _extract_final(res.stdout)
        trace.append({"round": rnd, "returncode": res.returncode,
                      "timed_out": res.timed_out, "got_final": final is not None})
        if res.returncode == 0 and final is not None:
            return {"answer": final, "via": "code", "rounds": rnd,
                    "code": code, "trace": trace}
        # Repair round: feed back the failure.
        err = (last_err or last_out or "no output")[:800]
        prompt = _SOLVE_PROMPT.format(problem=problem) + _REPAIR_SUFFIX.format(
            code=code, err=err)
    return {"answer": _extract_final(last_out), "via": "code-failed",
            "rounds": max_rounds, "code": code, "trace": trace}
