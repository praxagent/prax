"""Long-context retrieval — needle-in-a-haystack (MRCR-style), fully synthetic.

The external review (2026-07-17) flagged long-context/memory as a missing HIGH-
priority axis: an agent that loses track of buried context fails in production.
This plants a unique fact ("needle") at a controlled depth inside a long filler
"haystack" and asks for it back; scoring is **deterministic** (does the response
contain the needle value?).

Fully **self-contained and keyless** — the haystack is generated deterministically
from a seed (`PRAX_EVAL_SAMPLE_SEED`), so there's no dataset to fetch and no
contamination surface, and runs are reproducible. Cases span several context
lengths so the summary shows *degradation with length*, not a single number.
"""
from __future__ import annotations

import random

# Filler pool — bland, non-distinctive sentences so the needle is the only
# retrievable fact (not memorizable trivia).
_FILLER = [
    "The maintenance team reviewed the quarterly logs without incident.",
    "Ambient temperature in the archive room stayed within nominal range.",
    "Routine backups completed and were verified against the checksum manifest.",
    "The shuttle schedule was unchanged from the previous operating week.",
    "Inventory counts matched the ledger after the second reconciliation pass.",
    "Network latency remained flat across all monitored regions overnight.",
    "The committee deferred the agenda item to a later working session.",
    "Sensor calibration drifted negligibly and required no adjustment.",
    "Documentation was filed under the standard retention policy.",
    "The visitor log recorded no entries outside business hours.",
]

# (label, approx haystack length in sentences) — spans short→long.
_LENGTHS = [("short", 40), ("medium", 200), ("long", 700), ("xlong", 1600)]


def _build_case(idx: int, label: str, n_sentences: int, seed: int) -> dict:
    rng = random.Random(seed * 1000 + idx)
    code = f"{rng.randint(100, 999)}-{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.randint(10, 99)}"
    token = rng.choice(["orion", "vesper", "quartz", "meridian", "cobalt", "harbor"])
    needle = f"IMPORTANT: the secret access code for project {token} is {code}."
    sentences = [rng.choice(_FILLER) for _ in range(n_sentences)]
    pos = rng.randint(1, max(1, n_sentences - 1))   # bury it (not first/last)
    sentences.insert(pos, needle)
    return {
        "id": f"needle_{label}_{idx}",
        "haystack": " ".join(sentences),
        "token": token,
        "answer": code,
        "depth_frac": round(pos / max(1, len(sentences)), 2),
        "length_label": label,
    }


def _cases(seed: int) -> list[dict]:
    cases = []
    for label, n in _LENGTHS:
        for i in range(2):                       # 2 depths per length
            cases.append(_build_case(len(cases), label, n, seed + i))
    return cases


def score(case: dict, response: str) -> dict:
    got = case["answer"] in (response or "")
    return {"passed": got, "score": 1.0 if got else 0.0,
            "checks": {"answer": case["answer"], "found": got,
                       "length": case["length_label"], "depth": case["depth_frac"]}}


class LongContextAdapter:
    name = "longcontext"
    variant = "synthetic needle-in-haystack; exact-substring retrieval; multiple lengths"

    def __init__(self, cases: list[dict] | None = None, full: bool = False):
        from prax.eval.benchmarks.datasets import sample_seed
        self._cases = cases if cases is not None else _cases(sample_seed())

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return (
            "Read the following document carefully, then answer the question at the "
            "end using ONLY information stated in the document.\n\n"
            f"--- DOCUMENT START ---\n{case['haystack']}\n--- DOCUMENT END ---\n\n"
            f"Question: What is the secret access code for project {case['token']}? "
            "Answer with just the code."
        )

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
