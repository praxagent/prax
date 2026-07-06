"""Key-free tests for the BFCL function-calling AST matcher + adapter."""
from __future__ import annotations

from prax.eval.benchmarks.bfcl import SEED_CASES, _extract_calls, match, score


def _case(cid):
    return next(c for c in SEED_CASES if c["id"] == cid)


def test_extract_calls_variants():
    assert _extract_calls('[{"name":"f","arguments":{"x":1}}]') == [{"name": "f", "arguments": {"x": 1}}]
    assert _extract_calls('{"name":"f","arguments":{}}') == [{"name": "f", "arguments": {}}]
    assert _extract_calls('```json\n[{"name":"g","args":{"a":2}}]\n```') == [{"name": "g", "arguments": {"a": 2}}]
    assert _extract_calls('Sure! [{"name":"h","arguments":{}}] done') == [{"name": "h", "arguments": {}}]
    assert _extract_calls("no json here") == []


def test_match_semantics():
    exp = [{"name": "get_weather", "arguments": {"city": "Paris"}}]
    assert match(exp, [{"name": "get_weather", "arguments": {"city": "paris"}}]) is True  # case-insensitive
    assert match(exp, [{"name": "get_weather", "arguments": {"city": "Tokyo"}}]) is False
    assert match(exp, [{"name": "other", "arguments": {}}]) is False
    assert match(exp, exp + [{"name": "x", "arguments": {}}]) is False  # extra spurious call
    assert match([], []) is True                                        # irrelevance satisfied
    assert match([], [{"name": "get_weather", "arguments": {}}]) is False


def test_acceptable_values_arg():
    c = _case("bfcl_acceptable")
    ok = score(c, '[{"name":"convert_temp","arguments":{"value":20,"from_unit":"celsius","to_unit":"F"}}]')
    assert ok["passed"] is True


def test_irrelevance_requires_no_call():
    c = _case("bfcl_irrelevance")
    assert score(c, "[]")["passed"] is True
    assert score(c, '[{"name":"get_weather","arguments":{"city":"?"}}]')["passed"] is False


def test_parallel_case():
    c = _case("bfcl_parallel")
    resp = ('[{"name":"get_weather","arguments":{"city":"Paris"}},'
            '{"name":"get_weather","arguments":{"city":"Tokyo"}}]')
    assert score(c, resp)["passed"] is True


def test_registry_has_bfcl():
    from prax.eval.benchmarks import ADAPTER_NAMES, get_adapter
    assert "bfcl" in ADAPTER_NAMES
    ad = get_adapter("bfcl")
    assert ad.name == "bfcl" and ad.cases()
