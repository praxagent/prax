"""ARC-AGI-2 — abstract visual reasoning (grid input→output program induction).

Each task shows a few input→output *demonstration* grids of a hidden
transformation rule; the solver must infer the rule and produce the output grid
for a held-out test input. Grading is **deterministic**: exact grid match, with
**two attempts allowed** (pass@2 — the official ARC metric). No LLM judge.

This is a *benchmark of* the general "executable world-models" capability
(`docs/research/executable-world-models.md`): run through the full Prax harness,
Prax can write a `solve(grid)` program in its sandbox, verify it reproduces every
demonstration, and apply it to the test input — program-synthesis + verification,
which the research ranks as the top offline strategy.

Inline keyless seed task here (a hand-authored horizontal-mirror rule, NOT from
the real set) so keyless CI exercises the adapter; the real public tasks layer on
from PRAX_EVAL_DIR (never committed — contamination firewall) via
`scripts/fetch_eval_datasets.py arc_agi_2`. Convention: develop on the *training*
split, hold out the *evaluation* split — never train on either the eval split or
the hidden competition sets.
"""
from __future__ import annotations

import json
import re

Grid = list[list[int]]

# One representative seed task: reverse each row (horizontal mirror). Three
# demonstrations + one test, small grids. Hand-authored — not from the ARC set.
SEED_CASES: list[dict] = [
    {
        "id": "seed_hmirror",
        "train": [
            {"input": [[1, 0], [0, 2]], "output": [[0, 1], [2, 0]]},
            {"input": [[3, 3, 0], [0, 1, 2]], "output": [[0, 3, 3], [2, 1, 0]]},
            {"input": [[4, 0, 0, 5]], "output": [[5, 0, 0, 4]]},
        ],
        "test_input": [[7, 0, 8], [0, 6, 0]],
        "test_output": [[8, 0, 7], [0, 6, 0]],
    },
]


def _validate_grid(g: object) -> Grid | None:
    """Return *g* iff it is a non-empty rectangular list-of-lists of ints."""
    if not isinstance(g, list) or not g:
        return None
    if not all(isinstance(r, list) and r for r in g):
        return None
    width = len(g[0])
    for r in g:
        if len(r) != width or not all(isinstance(v, int) for v in r):
            return None
    return g


def _balanced_array(s: str, start: int = 0) -> tuple[object, int]:
    """Parse the first balanced ``[...]`` at/after *start*.

    Returns ``(parsed_or_None, next_index)``. Handles multi-line / pretty-printed
    arrays that a simple regex would miss. ``next_index < 0`` means no more.
    """
    i = s.find("[", start)
    if i < 0:
        return None, -1
    depth = 0
    for j in range(i, len(s)):
        c = s[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[i:j + 1]), j + 1
                except Exception:
                    return None, j + 1
    return None, -1


def _parse_answer_grids(text: str) -> list[Grid]:
    """Extract up to two candidate grids from a free-form response.

    Prefers grids after an explicit ``ANSWER:`` / ``ANSWER2:`` marker; falls back
    to the last two valid 2D int arrays anywhere in the text.
    """
    text = text or ""
    marked: list[Grid] = []
    for m in re.finditer(r"answer\s*2?\s*[:=]", text, re.IGNORECASE):
        g, _ = _balanced_array(text, m.end())
        vg = _validate_grid(g)
        if vg is not None:
            marked.append(vg)
    if marked:
        return marked[:2]

    found: list[Grid] = []
    pos = 0
    while True:
        g, nxt = _balanced_array(text, pos)
        if nxt < 0:
            break
        vg = _validate_grid(g)
        if vg is not None:
            found.append(vg)
        pos = nxt
    return found[-2:]


def score(case: dict, response: str) -> dict:
    """pass@2 exact-grid match against the test output."""
    target = case["test_output"]
    guesses = _parse_answer_grids(response)
    ok = any(g == target for g in guesses)
    return {
        "passed": ok,
        "score": 1.0 if ok else 0.0,
        "checks": {
            "n_guesses": len(guesses),
            "matched": ok,
            "target_shape": [len(target), len(target[0]) if target else 0],
        },
    }


def _render_grid(g: Grid) -> str:
    """Compact single-line JSON per row so the model reads structure cleanly."""
    return "[" + ",\n ".join(json.dumps(row) for row in g) + "]"


class ARCAGI2Adapter:
    name = "arc_agi_2"
    variant = "public evaluation split, exact grid match"
    attempts = "pass@2"

    def __init__(self, cases: list[dict] | None = None, full: bool = False):
        from prax.eval.benchmarks.datasets import cases_for
        self._cases = cases if cases is not None else cases_for("arc_agi_2", SEED_CASES, full=full)

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        pairs = []
        for i, ex in enumerate(case["train"], 1):
            pairs.append(
                f"Example {i}:\nInput:\n{_render_grid(ex['input'])}\n"
                f"Output:\n{_render_grid(ex['output'])}"
            )
        examples = "\n\n".join(pairs)
        return (
            "You are solving an ARC-AGI abstract-reasoning puzzle. Each grid is a "
            "2D array of integers 0–15 (colours). The input→output examples below "
            "all follow ONE hidden transformation rule. Infer the rule and apply "
            "it to the test input.\n\n"
            "Reliable method: WRITE a Python function `solve(grid)` implementing "
            "your hypothesised rule, RUN it on every example, and check it "
            "reproduces each output EXACTLY. If any example fails, revise the rule "
            "and retry. Only trust a rule that fits all examples, then apply it to "
            "the test input.\n\n"
            f"{examples}\n\n"
            f"Test input:\n{_render_grid(case['test_input'])}\n\n"
            "Give your final answer as a JSON 2D array (rows of integers) on its "
            "own line, prefixed exactly `ANSWER:`. You may give one alternative "
            "prefixed `ANSWER2:`."
        )

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
