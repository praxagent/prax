"""Terminal-Bench-style — end-to-end terminal task completion, scored in the sandbox.

Terminal-Bench (tbench.ai / harbor-framework) measures whether an agent can *complete
a real task in a terminal*: given an instruction, it must run the right commands and
leave the environment in a correct final state, verified by a hidden test. Unlike
HumanEval (complete one function) this is **task-level** — set up files, transform
data, write and run a script — the coding-agent capability commercial harnesses (Capy,
etc.) compete on, and the biggest gap in Prax's benchmark matrix.

**What this adapter is — and is NOT (honesty).** This runs Terminal-Bench-*style* tasks
through Prax's own sandbox and scores them deterministically. It is NOT the official
Terminal-Bench harness: the official benchmark ships a per-task Docker image and drives
the agent through an interactive tmux session, so an official leaderboard number needs
that harness (see ``docs/VERIFICATION_LEDGER.md``). Here the agent produces a shell
solution which is executed in a fresh sandbox working dir, then a hidden verify script
checks the final state — deterministic, un-spikeable (hidden test), no LLM judge. The
inline seed set is hand-authored; the real task set can be layered in from
``PRAX_EVAL_DIR`` once each task's environment is provisioned.

Sandbox-dependent (real runs need the container); CI exercises only the assemble/verdict
logic with a fake executor, exactly like HumanEval.
"""
from __future__ import annotations

import base64
import logging
import re

logger = logging.getLogger(__name__)

# Each case: an instruction the agent must satisfy in a terminal, an optional `setup`
# shell snippet that seeds the working dir, a `verify` snippet that exits 0 IFF the task
# is complete, and a `canonical_solution` (a known-correct shell script — used to
# self-test that the verify accepts a right answer).
SEED_CASES: list[dict] = [
    {"id": "tb_greeting",
     "instruction": "Create a file named greeting.txt whose only contents are the "
                    "single line: hello world",
     "setup": "",
     "canonical_solution": "printf 'hello world\\n' > greeting.txt",
     "verify": "test \"$(cat greeting.txt 2>/dev/null)\" = 'hello world'"},
    {"id": "tb_count_errors",
     "instruction": "The file log.txt exists in the current directory. Count how many "
                    "of its lines contain the word 'ERROR' and write ONLY that number "
                    "to a file named count.txt.",
     "setup": "printf 'INFO start\\nERROR disk\\nWARN slow\\nERROR net\\nINFO done\\n' > log.txt",
     "canonical_solution": "grep -c ERROR log.txt > count.txt",
     "verify": "test \"$(cat count.txt 2>/dev/null)\" = '2'"},
    {"id": "tb_sort_unique",
     "instruction": "The file input.txt exists. Write a file sorted_unique.txt "
                    "containing its lines de-duplicated and sorted in ascending order.",
     "setup": "printf 'banana\\napple\\ncherry\\napple\\nbanana\\n' > input.txt",
     "canonical_solution": "sort -u input.txt > sorted_unique.txt",
     "verify": "printf 'apple\\nbanana\\ncherry\\n' | diff -q - sorted_unique.txt"},
    {"id": "tb_json_port",
     "instruction": "The file config.json exists and contains a JSON object with an "
                    "integer field \"port\". Write ONLY that port number to port.txt.",
     "setup": "printf '{\"host\": \"localhost\", \"port\": 8080, \"tls\": true}' > config.json",
     "canonical_solution": "python3 -c \"import json;print(json.load(open('config.json'))['port'])\" > port.txt",
     "verify": "test \"$(cat port.txt 2>/dev/null)\" = '8080'"},
    {"id": "tb_sum_script",
     "instruction": "Write an executable script named compute.sh that prints the sum of "
                    "the integers from 1 to 100 (i.e. 5050). Run it and save its output "
                    "to sum.txt.",
     "setup": "",
     "canonical_solution": ("printf '#!/bin/bash\\nseq 1 100 | paste -sd+ | bc\\n' > compute.sh && "
                            "chmod +x compute.sh && ./compute.sh > sum.txt"),
     "verify": "test \"$(cat sum.txt 2>/dev/null)\" = '5050'"},
]

_FENCED = re.compile(r"```(?:bash|sh|shell|console)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_script(response: str) -> str:
    """Pull the shell solution from a model response: prefer a fenced block, else raw."""
    text = response or ""
    blocks = _FENCED.findall(text)
    if blocks:
        return blocks[-1].strip()
    return text.strip()


def build_program(case: dict, script: str) -> str:
    """Assemble the full sandbox program: fresh dir → setup → run solution → verify.

    The solution is base64-embedded so arbitrary shell content can't break the wrapper
    or escape the working dir. Prints ``__TB_OK__`` iff the verify command succeeds.
    """
    sol_b64 = base64.b64encode((script or "").encode()).decode()
    setup = case.get("setup") or "true"
    verify = case.get("verify") or "false"
    return (
        'work=$(mktemp -d) || exit 3\n'
        'cd "$work" || exit 3\n'
        f'( {setup} ) >/dev/null 2>&1\n'
        f"printf %s '{sol_b64}' | base64 -d > __solution.sh\n"
        'bash __solution.sh >/dev/null 2>&1\n'
        'rm -f __solution.sh\n'
        f'if ( {verify} ) >/dev/null 2>&1; then echo __TB_OK__; fi\n'
        'rc=$?\n'
        'cd / && rm -rf "$work"\n'
        'echo "__TB_EXIT__$rc"\n'
    )


def _sandbox_executor(program: str, timeout: int = 60) -> dict:
    """Default executor — run *program* with bash in the sandbox container.

    Returns ``{passed, output}``; degrades to a clear message when the sandbox is
    absent. Mirrors the HumanEval sandbox executor (base64 the whole program in, so no
    quoting can break the transport).
    """
    from prax.services.sandbox_bridge import configured_client as get_client
    from prax.settings import settings
    if not settings.sandbox_available:
        return {"passed": False, "output": "sandbox disabled — Terminal-Bench needs a terminal"}
    b64 = base64.b64encode(program.encode()).decode()
    cmd = f'printf %s {b64} | base64 -d | bash'
    try:
        result = get_client().run_shell(cmd, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Terminal-Bench sandbox exec failed: %s", exc)
        return {"passed": False, "output": f"sandbox exec error: {exc}"}
    if "error" in result:
        return {"passed": False, "output": f"sandbox error: {result['error']}"}
    out = (result.get("stdout", "") or "") + (result.get("stderr", "") or "")
    passed = "__TB_OK__" in out
    return {"passed": passed, "output": out[:2000]}


class TerminalBenchAdapter:
    name = "terminal_bench"
    variant = "terminal task, hidden-verify execution in sandbox (Prax-run, not official tbench harness)"

    def __init__(self, cases: list[dict] | None = None, executor=None, full: bool = False):
        from prax.eval.benchmarks.datasets import cases_for
        self._cases = cases if cases is not None else cases_for("terminal_bench", SEED_CASES, full=full)
        # executor(program:str, timeout:int) -> {"passed": bool, "output": str}
        self._executor = executor or _sandbox_executor

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return (
            "You are working in a Linux terminal in the current directory. Complete "
            "this task:\n\n"
            f"{case['instruction']}\n\n"
            "Respond with a single self-contained bash script that accomplishes the "
            "task when run in that directory. Return ONLY the script in one ```bash "
            "block — no prose, no explanation."
        )

    def score(self, case: dict, response: str) -> dict:
        script = extract_script(response)
        program = build_program(case, script)
        result = self._executor(program)
        ok = bool(result.get("passed"))
        return {"passed": ok, "score": 1.0 if ok else 0.0,
                "checks": {"task": case["id"], "output": (result.get("output") or "")[:500]}}
