"""SimpleQA — short-fact factuality (OpenAI, 2024).

Complements TruthfulQA (which probes popular misconceptions) and HaluEval (which
probes detection): SimpleQA asks single, unambiguous factual questions with a short
reference answer, testing whether the model KNOWS the fact — and, ideally, abstains
when it doesn't. Grading here is deterministic: normalize both sides and check the
reference answer (or an accepted alias) appears in the response — no LLM judge.

Inline keyless seed set here (hand-authored unambiguous facts, answers verified);
the full graded set loads from PRAX_EVAL_DIR (never committed —
benchmark-contamination firewall). NOTE: the reference grader for the real set is a
model judge; this adapter uses a strict normalized-match so it stays keyless and
deterministic, which is stricter (it can undercount paraphrased-but-correct
answers) — an honest floor, documented as such.
"""
from __future__ import annotations

import re

# ``answers`` = accepted surface forms (first is canonical). Matching is
# normalized substring, so "The capital is Canberra." matches "canberra".
SEED_CASES: list[dict] = [
    {"id": "sqa_gold", "question": "What is the chemical symbol for the element gold?",
     "answers": ["Au"]},
    {"id": "sqa_moon", "question": "In what year did humans first land on the Moon?",
     "answers": ["1969"]},
    {"id": "sqa_canberra", "question": "What is the capital city of Australia?",
     "answers": ["Canberra"]},
    {"id": "sqa_romeo",
     "question": "Who wrote the play 'Romeo and Juliet'?",
     "answers": ["William Shakespeare", "Shakespeare"]},
    {"id": "sqa_jupiter",
     "question": "What is the largest planet in our solar system?",
     "answers": ["Jupiter"]},
    {"id": "sqa_speed",
     "question": "What is the speed of light in a vacuum, to three significant "
                 "figures, in metres per second?",
     "answers": ["299,000,000", "299000000", "2.99e8", "2.99 x 10^8", "3.00e8"]},
]


def _normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)          # drop punctuation
    s = re.sub(r"\b(the|a|an)\b", " ", s)   # drop articles
    return re.sub(r"\s+", " ", s).strip()


def score(case: dict, response: str) -> dict:
    norm_resp = _normalize(response)
    matched = None
    for ans in case["answers"]:
        if _normalize(ans) and _normalize(ans) in norm_resp:
            matched = ans
            break
    ok = matched is not None
    return {"passed": ok, "score": 1.0 if ok else 0.0,
            "checks": {"matched": matched, "answer": case["answers"][0]}}


class SimpleQAAdapter:
    name = "simpleqa"

    def __init__(self, cases: list[dict] | None = None):
        self._cases = cases if cases is not None else SEED_CASES

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return (f"{case['question']}\n\n"
                "Answer concisely with just the fact. If you are not confident, say "
                "you don't know rather than guessing.")

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
