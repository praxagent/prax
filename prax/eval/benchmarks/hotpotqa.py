"""HotpotQA — multi-hop reading comprehension (arXiv 1809.09600).

A marquee benchmark Prax had no coverage of: the question can ONLY be answered by
combining facts from two (or more) passages — a "bridge" (find X in passage A, then
look X up in passage B) or a "comparison" (contrast a property across passages).
Distractor passages are included so retrieval-of-the-right-evidence matters, not
just reading. Complements LoCoMo (multi-hop over a *conversation*) with multi-hop
over *documents*.

Deterministic grading: HotpotQA answers are short spans, so exact/substring match
(numbers compared numerically). No LLM judge. Inline keyless seed here; the full
dev set can layer on via the gated loader.
"""
from __future__ import annotations

import re

# Each case: passages (title → text; some are distractors), a question that needs
# ≥2 of them, and a short deterministic answer. type is diagnostic only.
SEED_CASES: list[dict] = [
    {
        "id": "hotpot_bridge_city",
        "type": "bridge",
        "passages": {
            "Blue Harvest (film)": "Blue Harvest is a 2019 drama film directed by Marta Ostrowski. "
                                   "It premiered at the Toronto festival.",
            "Marta Ostrowski": "Marta Ostrowski is a filmmaker born in 1980 in Kraków, Poland. "
                               "She studied at the national film school.",
            "Toronto": "Toronto is the most populous city in Canada and the capital of Ontario.",
        },
        "question": "In which city was the director of the film 'Blue Harvest' born?",
        "answer": "Kraków",
    },
    {
        "id": "hotpot_comparison_taller",
        "type": "comparison",
        "passages": {
            "Aster Tower": "Aster Tower is an office skyscraper that rises 312 metres above the city centre.",
            "Cobalt Spire": "The Cobalt Spire is a residential high-rise with a roof height of 268 metres.",
            "Union Bridge": "Union Bridge is a suspension bridge with towers 90 metres tall.",
        },
        "question": "Which is taller, Aster Tower or Cobalt Spire? Answer with the building's name.",
        "answer": "Aster Tower",
    },
    {
        "id": "hotpot_bridge_year",
        "type": "bridge",
        "passages": {
            "The Meridian Prize": "The Meridian Prize for chemistry was awarded in 2016 to Dr. Lena Fyodorova "
                                  "for her work on catalysis.",
            "Lena Fyodorova": "Lena Fyodorova is a chemist who earned her PhD in 2004 from a university in Geneva.",
            "Geneva": "Geneva is a city in Switzerland known for diplomacy and research institutions.",
        },
        "question": "In what year did the 2016 Meridian Prize winner earn her PhD? Answer with just the year.",
        "answer": "2004",
    },
    {
        "id": "hotpot_comparison_earlier",
        "type": "comparison",
        "passages": {
            "Larkfield Records": "Larkfield Records was founded in 1971 and specialised in folk music.",
            "Denton Sound": "Denton Sound, a rival label, was established in 1965.",
            "Vinyl": "A vinyl record is an analog sound-storage medium.",
        },
        "question": "Which record label was founded earlier, Larkfield Records or Denton Sound?",
        "answer": "Denton Sound",
    },
    {
        "id": "hotpot_bridge_nationality",
        "type": "bridge",
        "passages": {
            "Project Halcyon": "Project Halcyon, a deep-sea mapping mission, was led by oceanographer Priya Nair.",
            "Priya Nair": "Priya Nair is an Indian oceanographer known for mapping the Sunda Trench.",
            "Sunda Trench": "The Sunda Trench is an oceanic trench in the Indian Ocean.",
        },
        "question": "What is the nationality of the person who led Project Halcyon?",
        "answer": "Indian",
    },
]

_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def score(case: dict, response: str) -> dict:
    resp = (response or "")
    ans = str(case["answer"])
    if ans.replace(".", "").isdigit():                    # numeric span (e.g. a year)
        nums = _NUM.findall(resp)
        got = nums[-1].replace(",", "").rstrip(".") if nums else None
        try:
            ok = got is not None and float(got) == float(ans)
        except ValueError:
            ok = False
        return {"passed": ok, "score": 1.0 if ok else 0.0,
                "checks": {"predicted": got, "answer": ans}}
    ok = ans.lower() in resp.lower()                       # short span — substring match
    return {"passed": ok, "score": 1.0 if ok else 0.0,
            "checks": {"answer": ans, "found": ok}}


def _render(passages: dict) -> str:
    return "\n\n".join(f"[{title}] {text}" for title, text in passages.items())


class HotpotQAAdapter:
    name = "hotpotqa"
    variant = "multi-hop (bridge + comparison) with distractors, exact/substring match"

    def __init__(self, cases: list[dict] | None = None, full: bool = False):
        from prax.eval.benchmarks.datasets import cases_for
        self._cases = cases if cases is not None else cases_for("hotpotqa", SEED_CASES, full=full)

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return (
            "Use the passages below to answer the question. The answer requires "
            "combining information from more than one passage; some passages are "
            "distractors. Answer concisely with just the fact requested.\n\n"
            f"{_render(case['passages'])}\n\n"
            f"Question: {case['question']}"
        )

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
