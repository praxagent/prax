"""GSM8K — grade-school math word problems (arXiv 2110.14168).

The reasoning floor everyone reports, and Prax had zero coverage of it. Multi-step
arithmetic with a single numeric answer, so grading is deterministic: extract the
final number and compare. No LLM judge.

Inline keyless seed set here — problems AUTHORED for this repo, neither copied
nor paraphrased from the dataset (a public repo must never carry benchmark
items: contamination firewall).  Each is a multi-step word problem with a single
numeric answer; the answer is hand-verified in the trailing comment and checked
independently by ``tests/test_gsm8k.py``.  The real test split is layered on
via ``datasets.cases_for`` when ``PRAX_EVAL_FULL_DATASETS`` is set.
"""
from __future__ import annotations

import re

SEED_CASES: list[dict] = [
    {"id": "gsm_seedlings",
     "question": "A garden centre sells seedling trays for $7 each. Lena buys 9 trays "
                 "and hands over a $12 voucher at the till. How many dollars does she "
                 "pay?",
     "answer": "51"},   # 9 * 7 = 63; 63 - 12 = 51
    {"id": "gsm_sprint_avg",
     "question": "Over four practice runs a sprinter clocked 13, 12, 14 and 13 seconds. "
                 "Her personal best is 11 seconds. How many seconds slower than her "
                 "personal best was her average practice time?",
     "answer": "2"},    # (13 + 12 + 14 + 13) / 4 = 52 / 4 = 13; 13 - 11 = 2
    {"id": "gsm_trough",
     "question": "A hose delivers 12 litres of water a minute. Farida runs it into an "
                 "empty 250-litre trough for a quarter of an hour. How many more litres "
                 "are needed to fill the trough?",
     "answer": "70"},   # quarter hour = 15 min; 12 * 15 = 180; 250 - 180 = 70
    {"id": "gsm_chairs",
     "question": "A school ordered 240 chairs. 25% arrived damaged and were sent back, "
                 "and the school then lent 30 of the remaining chairs to a neighbouring "
                 "school. How many chairs did the school keep?",
     "answer": "150"},  # 25% of 240 = 60; 240 - 60 = 180; 180 - 30 = 150
    {"id": "gsm_bill_split",
     "question": "Three friends split a $96 restaurant bill in the ratio 1 : 2 : 5, "
                 "according to what each ordered. How many dollars does the friend "
                 "who ordered the most pay?",
     "answer": "60"},   # 1 + 2 + 5 = 8 shares; 96 / 8 = 12; 5 * 12 = 60
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
    variant = "test split, exact final-answer match"

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
