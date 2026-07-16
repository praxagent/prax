"""GSM8K — grade-school math word problems (arXiv 2110.14168).

The reasoning floor everyone reports, and Prax had zero coverage of it. Multi-step
arithmetic with a single numeric answer, so grading is deterministic: extract the
final number and compare. No LLM judge.

Inline keyless seed set here (answers hand-verified); the full 1,319-problem test
split can be layered on via a gated loader.
"""
from __future__ import annotations

import re

SEED_CASES: list[dict] = [
    {"id": "gsm_clips",
     "question": "Natalia sold clips to 48 friends in April, then sold half as many "
                 "clips in May. How many clips did she sell altogether in April and May?",
     "answer": "72"},   # 48 + 24
    {"id": "gsm_robe",
     "question": "A robe takes 2 bolts of blue fiber and half that much white fiber. "
                 "How many bolts in total does it take?",
     "answer": "3"},    # 2 + 1
    {"id": "gsm_weng",
     "question": "Weng earns $12 an hour for babysitting. Yesterday she babysat for "
                 "50 minutes. How many dollars did she earn?",
     "answer": "10"},   # 12 * 50/60
    {"id": "gsm_betty",
     "question": "Betty has only half the money she needs for a $100 wallet. Her "
                 "parents give her $15 and her grandparents give twice as much as her "
                 "parents. How many more dollars does Betty need to buy the wallet?",
     "answer": "5"},     # 100 - (50 + 15 + 30)
    {"id": "gsm_trees",
     "question": "There are 15 trees in the grove. Workers will plant more today. "
                 "After they are done there will be 21 trees. How many did they plant?",
     "answer": "6"},     # 21 - 15
]

_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _final_number(response: str) -> str | None:
    nums = _NUM.findall(response or "")
    return nums[-1].replace(",", "").rstrip(".") if nums else None


def score(case: dict, response: str) -> dict:
    got = _final_number(response)
    try:
        ok = got is not None and float(got) == float(case["answer"])
    except ValueError:
        ok = False
    return {"passed": ok, "score": 1.0 if ok else 0.0,
            "checks": {"predicted": got, "answer": case["answer"]}}


class GSM8KAdapter:
    name = "gsm8k"

    def __init__(self, cases: list[dict] | None = None, full: bool = False):
        from prax.eval.benchmarks.datasets import cases_for
        self._cases = cases if cases is not None else cases_for("gsm8k", SEED_CASES, full=full)

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return (f"{case['question']}\n\n"
                "Solve step by step, then give the final numeric answer on the last "
                "line as just the number.")

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
