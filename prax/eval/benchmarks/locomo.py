"""LoCoMo — long-term conversational memory (arXiv 2402.17753).

The memory dimension Prax had no coverage of, and the weakest cell in the
self-improvement taxonomy audit (docs/research/self-improving-agents-survey.md).
A long, multi-session dialogue embeds facts, then asks questions that require
recalling them across distance — single-hop, multi-hop (combine two facts), a
temporal/update case (a fact changes; the *latest* state is correct), and an
adversarial case whose fact was never stated (the honest answer is "not
mentioned", NOT a fabrication).

Deterministic grading: recall questions match the ground-truth string; the
adversarial case passes only when the answer signals it wasn't stated AND does not
assert a specific fabricated value. No LLM judge.

Inline keyless seed set here (facts hand-placed with distractors between them); the
full LoCoMo set can layer on via the gated loader.
"""
from __future__ import annotations

import re

# Each case: a multi-session transcript (facts spread out, distractors between),
# a question, and a deterministic answer. category is diagnostic only.
SEED_CASES: list[dict] = [
    {
        "id": "locomo_single_hop",
        "category": "single-hop",
        "conversation": [
            (1, "Alice", "Hey! Big news — I finally adopted a dog this weekend. His name is Biscuit."),
            (1, "Bob", "Aww congrats! What breed?"),
            (1, "Alice", "A beagle. He's a rescue, about two years old."),
            (2, "Bob", "How was the concert last night?"),
            (2, "Alice", "So loud but amazing. My ears are still ringing."),
            (3, "Bob", "Did you get that report finished for work?"),
            (3, "Alice", "Yeah, submitted it Friday. Glad it's off my plate."),
        ],
        "question": "What is the name of Alice's dog?",
        "answer": "Biscuit",
    },
    {
        "id": "locomo_multi_hop",
        "category": "multi-hop",
        "conversation": [
            (1, "Carol", "I just started a new job at a bakery — I'm on the early shift."),
            (1, "Dan", "Nice, fresh bread every day!"),
            (2, "Dan", "How's the weather where you are?"),
            (2, "Carol", "Rainy as always. Portland never changes."),
            (3, "Dan", "Do you like the new job so far?"),
            (3, "Carol", "Love it. The bakery here is tiny but the team is great."),
        ],
        "question": "In which city does Carol work? Answer with just the city name.",
        "answer": "Portland",
    },
    {
        "id": "locomo_temporal_update",
        "category": "temporal-update",
        "conversation": [
            (1, "Eve", "I've been saving up for a car for months now."),
            (2, "Frank", "Any weekend plans?"),
            (2, "Eve", "Just errands. Boring adult stuff."),
            (4, "Eve", "Update: I finally bought the car! Now I'm saving for a boat, believe it or not."),
            (5, "Frank", "A boat?! Big dreams."),
            (5, "Eve", "Ha, someday. It'll take a while."),
        ],
        "question": "What is Eve currently saving up for? Answer with a single word.",
        "answer": "boat",
    },
    {
        "id": "locomo_multi_hop_numeric",
        "category": "multi-hop-numeric",
        "conversation": [
            (1, "Grace", "My three kids keep me busy — soccer, piano, the whole thing."),
            (2, "Heidi", "How do you manage allowances?"),
            (2, "Grace", "Each of them gets five dollars a week. Keeps it simple."),
            (3, "Heidi", "Smart. Consistency helps."),
        ],
        "question": "How many dollars of allowance does Grace pay out in total per week? Answer with just the number.",
        "answer": "15",
    },
    {
        "id": "locomo_adversarial_absent",
        "category": "adversarial",
        "conversation": [
            (1, "Ivan", "I moved to a new apartment last month. Loving the extra space."),
            (2, "Judy", "Any pets in the new place?"),
            (2, "Ivan", "Not yet, maybe later. For now it's just me."),
            (3, "Judy", "What's the neighborhood like?"),
            (3, "Ivan", "Quiet, lots of trees. Good for morning runs."),
        ],
        # Ivan never stated his rent. The honest answer is that it wasn't mentioned.
        "question": "How much is Ivan's monthly rent?",
        "answer": None,
    },
]

_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# Signals the model correctly declined to answer an un-stated fact. Covers the
# common negations (not/never/wasn't/isn't/didn't/don't) near a say/mention/know verb.
_UNCERTAIN = re.compile(
    r"(wasn'?t|isn'?t|weren'?t|aren'?t|hasn'?t|haven'?t|never|not|didn'?t|doesn'?t|don'?t)\s+"
    r"(mention\w*|state\w*|said|say|specif\w*|know|given|provided|told|indicat\w*)"
    r"|no\s+(mention|information|indication|record)"
    r"|(unknown|unclear|can'?t tell|hard to say|isn'?t clear)",
    re.IGNORECASE,
)


def score(case: dict, response: str) -> dict:
    resp = response or ""
    if case.get("answer") is None:
        # Adversarial: must signal "not mentioned" AND must not assert a $ amount.
        declined = bool(_UNCERTAIN.search(resp))
        fabricated = bool(re.search(r"\$\s*\d|\b\d[\d,]*\s*(dollars|/mo|per month|a month)\b", resp, re.IGNORECASE))
        ok = declined and not fabricated
        return {"passed": ok, "score": 1.0 if ok else 0.0,
                "checks": {"declined": declined, "fabricated_amount": fabricated}}
    ans = str(case["answer"])
    if ans.replace(".", "").isdigit():           # numeric recall — exact final number
        got = _NUM.findall(resp)
        got = got[-1].replace(",", "").rstrip(".") if got else None
        try:
            ok = got is not None and float(got) == float(ans)
        except ValueError:
            ok = False
        return {"passed": ok, "score": 1.0 if ok else 0.0,
                "checks": {"predicted": got, "answer": ans}}
    ok = ans.lower() in resp.lower()             # string recall — substring match
    return {"passed": ok, "score": 1.0 if ok else 0.0,
            "checks": {"answer": ans, "found": ok}}


def _render(conversation: list) -> str:
    lines, cur = [], None
    for session, speaker, text in conversation:
        if session != cur:
            lines.append(f"\n--- Session {session} ---")
            cur = session
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines).strip()


class LoCoMoAdapter:
    name = "locomo"
    variant = "multi-session recall, deterministic (single/multi-hop, temporal, adversarial)"

    def __init__(self, cases: list[dict] | None = None, full: bool = False):
        from prax.eval.benchmarks.datasets import cases_for
        self._cases = cases if cases is not None else cases_for("locomo", SEED_CASES, full=full)

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return (
            "Read this multi-session conversation, then answer the question using ONLY "
            "what was actually said. If the answer was never stated, say it was not "
            "mentioned — do not guess.\n\n"
            f"{_render(case['conversation'])}\n\n"
            f"Question: {case['question']}"
        )

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
