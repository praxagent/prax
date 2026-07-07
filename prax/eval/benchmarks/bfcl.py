"""BFCL — Berkeley Function Calling Leaderboard (AST-match tool selection).

BFCL is the de-facto function-calling standard: given a query + available function
schemas, does the model emit the CORRECT call(s) — right function, right arguments
— and does it correctly emit NOTHING when no function applies (relevance)? Grading
is **AST / structural match**, fully deterministic — no LLM judge, no network. It's
the cleanest regression detector for Prax's tool-binding / structured-output path.

This module ships the deterministic matcher + an inline keyless seed set covering
BFCL's core categories: simple, multiple-choice, parallel, and irrelevance. The
full Gorilla set can be layered on via a gated loader; the matcher is the reusable
part.
"""
from __future__ import annotations

import json
import re


def _extract_calls(response: str) -> list[dict]:
    """Pull function call(s) out of a model response as ``[{name, arguments}]``.

    Tolerant of code fences and surrounding prose — finds the first JSON array or
    object and normalizes it.
    """
    if not response:
        return []
    s = re.sub(r"```(?:json)?", "", response).strip().strip("`").strip()
    parsed = _try_json(s)
    if parsed is None:
        m = re.search(r"(\[.*\]|\{.*\})", s, re.DOTALL)
        parsed = _try_json(m.group(1)) if m else None
    if parsed is None:
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    out: list[dict] = []
    for c in parsed:
        if isinstance(c, dict) and c.get("name"):
            out.append({"name": c["name"], "arguments": c.get("arguments") or c.get("args") or {}})
    return out


def _try_json(s):
    try:
        return json.loads(s)
    except Exception:
        return None


def _call_match(expected: dict, actual: dict) -> bool:
    if expected.get("name") != actual.get("name"):
        return False
    act_args = actual.get("arguments") or {}
    for key, val in (expected.get("arguments") or {}).items():
        got = act_args.get(key)
        acceptable = val if isinstance(val, list) else [val]
        if str(got).strip().lower() not in [str(a).strip().lower() for a in acceptable]:
            return False
    return True


def match(expected_calls: list[dict], actual_calls: list[dict]) -> bool:
    """AST match: every expected call is satisfied by a distinct actual call, and
    there are no extra spurious calls. ``expected_calls == []`` means "no call"
    (BFCL relevance) — satisfied iff the model emitted none."""
    if len(actual_calls) != len(expected_calls):
        return False
    remaining = list(actual_calls)
    for exp in expected_calls:
        hit = next((a for a in remaining if _call_match(exp, a)), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return True


# --- inline seed set --------------------------------------------------------

_WEATHER = {"name": "get_weather", "description": "current weather for a city",
            "parameters": {"city": "string"}}

SEED_CASES: list[dict] = [
    {"id": "bfcl_simple", "category": "simple",
     "query": "What's the weather in Paris?", "functions": [_WEATHER],
     "expected": [{"name": "get_weather", "arguments": {"city": "Paris"}}]},
    {"id": "bfcl_args", "category": "simple",
     "query": "Add 3 and 5.",
     "functions": [{"name": "add", "parameters": {"a": "int", "b": "int"}}],
     "expected": [{"name": "add", "arguments": {"a": 3, "b": 5}}]},
    {"id": "bfcl_multiple", "category": "multiple",
     "query": "Book a flight from NYC to LA.",
     "functions": [_WEATHER,
                   {"name": "book_flight", "parameters": {"origin": "string", "dest": "string"}}],
     "expected": [{"name": "book_flight", "arguments": {"origin": "NYC", "dest": "LA"}}]},
    {"id": "bfcl_parallel", "category": "parallel",
     "query": "What's the weather in Paris and in Tokyo?", "functions": [_WEATHER],
     "expected": [{"name": "get_weather", "arguments": {"city": "Paris"}},
                  {"name": "get_weather", "arguments": {"city": "Tokyo"}}]},
    {"id": "bfcl_acceptable", "category": "simple",
     "query": "Convert 20 degrees Celsius to Fahrenheit.",
     "functions": [{"name": "convert_temp",
                    "parameters": {"value": "number", "from_unit": "string", "to_unit": "string"}}],
     "expected": [{"name": "convert_temp",
                   "arguments": {"value": 20, "from_unit": ["C", "celsius"],
                                 "to_unit": ["F", "fahrenheit"]}}]},
    {"id": "bfcl_irrelevance", "category": "irrelevance",
     "query": "Tell me a fun fact about otters.", "functions": [_WEATHER],
     "expected": []},  # no function applies — the model must NOT call one
]


def build_prompt(case: dict) -> str:
    fns = json.dumps(case["functions"])
    return (
        f"{case['query']}\n\n"
        f"Available functions (JSON): {fns}\n\n"
        "Respond with ONLY a JSON array of the function call(s) to make, e.g. "
        '[{"name": "fn", "arguments": {"x": 1}}]. '
        "If no available function applies, respond with exactly []."
    )


def score(case: dict, response: str) -> dict:
    actual = _extract_calls(response)
    ok = match(case["expected"], actual)
    return {"passed": ok, "score": 1.0 if ok else 0.0,
            "checks": {"category": case.get("category"), "n_calls": len(actual)}}


class BFCLAdapter:
    name = "bfcl"

    def __init__(self, cases: list[dict] | None = None):
        self._cases = cases if cases is not None else SEED_CASES

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return build_prompt(case)

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
