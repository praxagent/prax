"""IFEval — verifiable instruction-following (Google, arXiv 2311.07911).

IFEval grades whether a model obeys *mechanically checkable* instructions ("reply
in all lowercase", "use the word X at least 3 times", "output valid JSON") — the
purest "verifiable beats judgeable" benchmark: zero LLM judge, zero network, runs
on CPU in milliseconds. It grades Prax's **system-prompt / instruction adherence**
directly, which is why it's first off the do-first shortlist.

This module ships the deterministic **verifier library** plus a small inline seed
set so it runs keyless in CI. The full 500-prompt Google set can be layered on
later via a gated loader (like GAIA); the verifiers are the reusable part.
"""
from __future__ import annotations

import json as _json
import re


def _words(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text)


# --- deterministic verifiers: (response, **kwargs) -> bool -------------------

def all_lowercase(text: str) -> bool:
    return any(c.isalpha() for c in text) and not any(c.isupper() for c in text)


def all_uppercase(text: str) -> bool:
    return any(c.isalpha() for c in text) and not any(c.islower() for c in text)


def no_commas(text: str) -> bool:
    return "," not in text


def min_words(text: str, n: int) -> bool:
    return len(_words(text)) >= n


def max_words(text: str, n: int) -> bool:
    return len(_words(text)) <= n


def keyword_present(text: str, keyword: str) -> bool:
    return keyword.lower() in text.lower()


def keyword_frequency(text: str, keyword: str, n: int, relation: str = "at least") -> bool:
    count = len(re.findall(re.escape(keyword.lower()), text.lower()))
    if relation == "at most":
        return count <= n
    if relation == "exactly":
        return count == n
    return count >= n


def num_sentences(text: str, n: int, relation: str = "at least") -> bool:
    count = len([s for s in re.split(r"[.!?]+", text) if s.strip()])
    if relation == "at most":
        return count <= n
    if relation == "exactly":
        return count == n
    return count >= n


def num_bullets(text: str, n: int) -> bool:
    count = len([ln for ln in text.splitlines() if re.match(r"\s*[\*\-]\s+\S", ln)])
    return count == n


def json_format(text: str) -> bool:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()
    try:
        _json.loads(s)
        return True
    except Exception:
        return False


def end_with(text: str, phrase: str) -> bool:
    return text.strip().endswith(phrase)


def start_with(text: str, phrase: str) -> bool:
    return text.strip().startswith(phrase)


def has_title(text: str) -> bool:
    # IFEval "title" instruction: a title wrapped in double angle brackets.
    return bool(re.search(r"<<[^>]+>>", text))


def postscript(text: str) -> bool:
    return bool(re.search(r"(?im)^\s*p\.?\s*s\.?", text))


_VERIFIERS = {
    "all_lowercase": all_lowercase, "all_uppercase": all_uppercase,
    "no_commas": no_commas, "min_words": min_words, "max_words": max_words,
    "keyword_present": keyword_present, "keyword_frequency": keyword_frequency,
    "num_sentences": num_sentences, "num_bullets": num_bullets,
    "json_format": json_format, "end_with": end_with, "start_with": start_with,
    "has_title": has_title, "postscript": postscript,
}


# --- inline seed set (keyless) ----------------------------------------------

SEED_CASES: list[dict] = [
    {"id": "if_lowercase", "base": "Explain what a hash map is.",
     "text": "Respond in all lowercase letters — no capital letters at all.",
     "instructions": [{"fn": "all_lowercase"}]},
    {"id": "if_keyword_freq", "base": "Write a few lines about the sea.",
     "text": 'Use the word "wave" at least three times.',
     "instructions": [{"fn": "keyword_frequency", "keyword": "wave", "n": 3, "relation": "at least"}]},
    {"id": "if_min_words", "base": "Describe a sunset over the mountains.",
     "text": "Write at least 50 words.",
     "instructions": [{"fn": "min_words", "n": 50}]},
    {"id": "if_json_only", "base": "List the three primary additive colors.",
     "text": "Output ONLY valid JSON, with no prose before or after.",
     "instructions": [{"fn": "json_format"}]},
    {"id": "if_three_bullets", "base": "Give tips for focused studying.",
     "text": "Answer with exactly three markdown bullet points (lines starting with * or -).",
     "instructions": [{"fn": "num_bullets", "n": 3}]},
    {"id": "if_no_comma", "base": "Explain gravity in one short paragraph.",
     "text": "Do not use any commas anywhere in your response.",
     "instructions": [{"fn": "no_commas"}]},
    {"id": "if_end_phrase", "base": "Give one piece of advice to a new runner.",
     "text": 'End your entire response with the exact phrase: Happy running!',
     "instructions": [{"fn": "end_with", "phrase": "Happy running!"}]},
    {"id": "if_title", "base": "Write two sentences about honeybees.",
     "text": "Include a title wrapped in double angle brackets, e.g. <<Bees>>.",
     "instructions": [{"fn": "has_title"}]},
    {"id": "if_combo", "base": "Recommend a book and say why.",
     "text": "Respond in all lowercase AND use no commas.",
     "instructions": [{"fn": "all_lowercase"}, {"fn": "no_commas"}]},
]


def score(case: dict, response: str) -> dict:
    """Deterministically score a response against a case's instructions.

    ``passed`` requires ALL instructions satisfied (IFEval "strict" prompt-level);
    ``score`` is the fraction satisfied (instruction-level).
    """
    response = response or ""
    checks: dict[str, bool] = {}
    for ins in case.get("instructions", []):
        fn = _VERIFIERS.get(ins["fn"])
        if fn is None:
            checks[ins["fn"]] = False
            continue
        kwargs = {k: v for k, v in ins.items() if k != "fn"}
        try:
            checks[ins["fn"]] = bool(fn(response, **kwargs))
        except Exception:
            checks[ins["fn"]] = False
    n = len(checks)
    ok = sum(checks.values())
    return {"passed": n > 0 and ok == n, "score": round(ok / n, 3) if n else 0.0, "checks": checks}


class IFEvalAdapter:
    """BenchmarkAdapter over the inline seed set (extend `cases` with a gated
    loader for the full Google set)."""

    name = "ifeval"

    def __init__(self, cases: list[dict] | None = None):
        self._cases = cases if cases is not None else SEED_CASES

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return f"{case['base']}\n\n{case['text']}"

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
