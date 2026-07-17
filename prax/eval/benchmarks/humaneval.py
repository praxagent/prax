"""HumanEval — functional-correctness code generation (Chen et al., arXiv 2107.03374).

The canonical coding benchmark: complete a Python function from its signature +
docstring; grading is **execution-based and deterministic** — run the candidate
against the problem's unit tests, pass iff every assertion holds. No LLM judge.

Unlike the pure-Q&A adapters, scoring requires RUNNING code, which Prax does in the
**sandbox** (never the host). Execution is injected (``executor``) so the scoring
pipeline is unit-tested keylessly with a fake; the real path shells `python3` in the
sandbox via the client — mirrors the lean_check pattern. This is the coding forcing
function: the harness grows a fresh-sandbox code-execution path.

Inline keyless seed set here (hand-authored HumanEval-style problems + canonical
solutions + tests); the full 164-problem set loads from PRAX_EVAL_DIR (never
committed — contamination firewall). Sandbox-dependent, so live runs need the
container; CI exercises only the pure assemble/verdict logic with a fake executor.
"""
from __future__ import annotations

import base64
import logging
import re

logger = logging.getLogger(__name__)

# Each case: a HumanEval-shaped record. `prompt` is the signature+docstring shown
# to the model; `entry_point` is the function name; `test` defines `check(candidate)`
# with asserts; `canonical_solution` is a known-correct completion (for self-test).
SEED_CASES: list[dict] = [
    {"id": "he_add",
     "entry_point": "add",
     "prompt": "def add(a, b):\n    \"\"\"Return the sum of a and b.\"\"\"\n",
     "canonical_solution": "def add(a, b):\n    return a + b\n",
     "test": ("def check(candidate):\n"
              "    assert candidate(2, 3) == 5\n"
              "    assert candidate(-1, 1) == 0\n"
              "    assert candidate(0, 0) == 0\n")},
    {"id": "he_is_even",
     "entry_point": "is_even",
     "prompt": "def is_even(n):\n    \"\"\"Return True if n is even, else False.\"\"\"\n",
     "canonical_solution": "def is_even(n):\n    return n % 2 == 0\n",
     "test": ("def check(candidate):\n"
              "    assert candidate(4) is True\n"
              "    assert candidate(7) is False\n"
              "    assert candidate(0) is True\n")},
    {"id": "he_reverse",
     "entry_point": "reverse_string",
     "prompt": "def reverse_string(s):\n    \"\"\"Return the string s reversed.\"\"\"\n",
     "canonical_solution": "def reverse_string(s):\n    return s[::-1]\n",
     "test": ("def check(candidate):\n"
              "    assert candidate('abc') == 'cba'\n"
              "    assert candidate('') == ''\n"
              "    assert candidate('a') == 'a'\n")},
    {"id": "he_max",
     "entry_point": "max_in_list",
     "prompt": ("def max_in_list(lst):\n"
                "    \"\"\"Return the largest number in the non-empty list lst.\"\"\"\n"),
     "canonical_solution": "def max_in_list(lst):\n    return max(lst)\n",
     "test": ("def check(candidate):\n"
              "    assert candidate([1, 5, 3]) == 5\n"
              "    assert candidate([-2, -7, -1]) == -1\n"
              "    assert candidate([42]) == 42\n")},
    {"id": "he_vowels",
     "entry_point": "count_vowels",
     "prompt": ("def count_vowels(s):\n"
                "    \"\"\"Return the number of vowels (a, e, i, o, u) in the lowercase "
                "string s.\"\"\"\n"),
     "canonical_solution": ("def count_vowels(s):\n"
                            "    return sum(1 for c in s if c in 'aeiou')\n"),
     "test": ("def check(candidate):\n"
              "    assert candidate('hello') == 2\n"
              "    assert candidate('xyz') == 0\n"
              "    assert candidate('aeiou') == 5\n")},
]

_FENCED = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(response: str, entry_point: str) -> str:
    """Pull the candidate code from a model response.

    Prefer a fenced ```python block; else use the raw response. If a fenced block
    doesn't define the entry point but another block does, pick the defining one.
    """
    text = response or ""
    blocks = _FENCED.findall(text)
    defining = [b for b in blocks if re.search(rf"def\s+{re.escape(entry_point)}\b", b)]
    if defining:
        return defining[-1].strip()
    if blocks:
        return blocks[-1].strip()
    return text.strip()


def build_program(case: dict, code: str) -> str:
    """Assemble a runnable program: the candidate code + the test + the call."""
    return f"{code}\n\n{case['test']}\n\ncheck({case['entry_point']})\nprint('__HE_OK__')\n"


def _sandbox_executor(program: str, timeout: int = 30) -> dict:
    """Default executor — run *program* with python3 in the sandbox container.

    Isolated temp dir per run (keeps container /tmp clean, like lean_check). Returns
    ``{passed, output}``; degrades to a clear message when the sandbox is absent.
    """
    from prax.services.sandbox_bridge import configured_client as get_client
    from prax.settings import settings
    if not settings.sandbox_available:
        return {"passed": False, "output": "sandbox disabled — HumanEval needs code execution"}
    b64 = base64.b64encode(program.encode()).decode()
    cmd = (
        'd=$(mktemp -d) && '
        f'printf %s {b64} | base64 -d > "$d/prog.py" && '
        'python3 "$d/prog.py"; rc=$?; rm -rf "$d"; echo "__HE_EXIT__$rc"'
    )
    try:
        result = get_client().run_shell(cmd, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("HumanEval sandbox exec failed: %s", exc)
        return {"passed": False, "output": f"sandbox exec error: {exc}"}
    if "error" in result:
        return {"passed": False, "output": f"sandbox error: {result['error']}"}
    out = (result.get("stdout", "") or "") + (result.get("stderr", "") or "")
    m = re.search(r"__HE_EXIT__(-?\d+)", out)
    rc = int(m.group(1)) if m else 1
    passed = rc == 0 and "__HE_OK__" in out
    return {"passed": passed, "output": out[:2000]}


class HumanEvalAdapter:
    name = "humaneval"
    variant = "hidden-unit-test execution in sandbox"

    def __init__(self, cases: list[dict] | None = None, executor=None, full: bool = False):
        from prax.eval.benchmarks.datasets import cases_for
        self._cases = cases if cases is not None else cases_for("humaneval", SEED_CASES, full=full)
        # executor(program:str, timeout:int) -> {"passed": bool, "output": str}
        self._executor = executor or _sandbox_executor

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return (f"Complete this Python function. Return ONLY the full function "
                f"definition (you may use a ```python block).\n\n{case['prompt']}")

    def score(self, case: dict, response: str) -> dict:
        code = extract_code(response, case["entry_point"])
        program = build_program(case, code)
        result = self._executor(program)
        ok = bool(result.get("passed"))
        return {"passed": ok, "score": 1.0 if ok else 0.0,
                "checks": {"entry_point": case["entry_point"],
                           "output": (result.get("output") or "")[:500]}}
