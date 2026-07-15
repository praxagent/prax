"""GPQA — Graduate-level Google-Proof Q&A in biology, chemistry, physics
(arXiv 2311.12022).

The prestige "hard reasoning" benchmark: expert-written multiple-choice questions
(4 options) that domain PhDs get right but skilled non-experts with web access
mostly miss. Deterministic grading — extract the chosen letter, compare — no judge.
The headline "Diamond" subset is the hardest, unanimously-expert-validated split.

Inline keyless seed set here (hand-authored representative science items, answers
verified — NOT the held-out Diamond questions, which are deliberately kept off the
open web); the real gated set loads from PRAX_EVAL_DIR (never committed —
benchmark-contamination firewall).
"""
from __future__ import annotations

import re

SEED_CASES: list[dict] = [
    {"id": "gpqa_qnum",
     "question": "Which quantum number determines the shape of an atomic orbital?",
     "choices": ["Principal quantum number (n)",
                 "Azimuthal (angular-momentum) quantum number (l)",
                 "Magnetic quantum number (m_l)",
                 "Spin quantum number (m_s)"],
     "answer": "B"},
    {"id": "gpqa_wien",
     "question": "For an ideal blackbody, the wavelength of peak spectral emission "
                 "is inversely proportional to absolute temperature. This relationship "
                 "is known as:",
     "choices": ["The Stefan–Boltzmann law", "Wien's displacement law",
                 "The Rayleigh–Jeans law", "Kirchhoff's law of thermal radiation"],
     "answer": "B"},
    {"id": "gpqa_rnapol",
     "question": "During transcription in a eukaryotic cell, which enzyme synthesizes "
                 "a messenger-RNA strand from a DNA template?",
     "choices": ["DNA polymerase III", "RNA polymerase II",
                 "Reverse transcriptase", "DNA ligase"],
     "answer": "B"},
    {"id": "gpqa_pauli",
     "question": "The Pauli exclusion principle states that no two identical fermions "
                 "within a quantum system can simultaneously occupy the same:",
     "choices": ["Energy eigenvalue", "Complete set of quantum numbers (quantum state)",
                 "Spatial orbital, regardless of spin", "Atom"],
     "answer": "B"},
    {"id": "gpqa_sn2",
     "question": "An SN2 nucleophilic substitution at a saturated carbon proceeds with:",
     "choices": ["Retention of configuration at the reacting carbon",
                 "Inversion of configuration at the reacting carbon (Walden inversion)",
                 "Racemization via a planar carbocation intermediate",
                 "No stereochemical change because the carbon is sp3"],
     "answer": "B"},
]

_LETTERS = "ABCD"


def _extract_letter(response: str) -> str | None:
    text = response or ""
    patterns = [
        r"answer\s*(?:is|:)?\s*\(?([ABCD])\)?\b",
        r"\boption\s*\(?([ABCD])\)?\b",
        r"\(([ABCD])\)",
        r"\b([ABCD])\b\s*$",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).upper()
    hits = re.findall(r"\b([ABCD])\b", text, re.IGNORECASE)
    return hits[-1].upper() if hits else None


def score(case: dict, response: str) -> dict:
    got = _extract_letter(response)
    ok = got is not None and got == str(case["answer"]).upper()
    return {"passed": ok, "score": 1.0 if ok else 0.0,
            "checks": {"predicted": got, "answer": case["answer"]}}


class GPQAAdapter:
    name = "gpqa"

    def __init__(self, cases: list[dict] | None = None):
        self._cases = cases if cases is not None else SEED_CASES

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        opts = "\n".join(f"{_LETTERS[i]}. {c}" for i, c in enumerate(case["choices"]))
        return (f"{case['question']}\n\n{opts}\n\n"
                "Think it through, then give the single letter of the correct option "
                "on the last line (e.g. 'Answer: B').")

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
