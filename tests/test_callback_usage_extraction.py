"""Token-usage extraction in OTelLLMCallback.on_llm_end.

Regression cover: some providers send an explicit ``"token_usage": null``.
``.get(key, default)`` returns None for a key that EXISTS with value None, so
`usage` became None and the next line raised — aborting the callback before
BOTH the Prometheus metrics and the trace cost attribution. LangChain swallows
callback exceptions, so the only symptom was a log line and silently missing
accounting.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from prax.observability.callbacks import OTelLLMCallback


def _resp(llm_output=None, generations=None):
    return SimpleNamespace(llm_output=llm_output, generations=generations or [])


def _gen(generation_info):
    return [[SimpleNamespace(generation_info=generation_info)]]


@pytest.fixture
def cb():
    c = OTelLLMCallback()
    c._start_times[uuid4()] = 0.0
    return c


class TestUsageExtractionNeverRaises:
    """Every shape a provider has actually sent must survive."""

    @pytest.mark.parametrize("response", [
        _resp(llm_output={"token_usage": None}),                  # the crash
        _resp(llm_output={"token_usage": {}}),
        _resp(llm_output={}),
        _resp(llm_output=None),
        _resp(generations=_gen({"token_usage": None})),           # the crash
        _resp(generations=_gen({"token_usage": {}})),
        _resp(generations=_gen(None)),
        _resp(generations=[[]]),
        _resp(generations=[]),
        _resp(llm_output={"token_usage": {"prompt_tokens": None,
                                          "completion_tokens": None}}),
    ])
    def test_no_exception_for_any_provider_shape(self, cb, response):
        cb.on_llm_end(response, run_id=uuid4())

    def test_real_usage_still_extracted(self, cb, monkeypatch):
        seen = {}
        import prax.observability.callbacks as mod
        monkeypatch.setattr(mod, "_record_metrics",
                            lambda m, i, o, e: seen.update(model=m, i=i, o=o))
        cb.on_llm_end(
            _resp(llm_output={"token_usage": {"prompt_tokens": 11,
                                              "completion_tokens": 7},
                              "model_name": "test-model"}),
            run_id=uuid4(),
        )
        assert seen == {"model": "test-model", "i": 11, "o": 7}

    def test_null_usage_records_zeros_not_garbage(self, cb, monkeypatch):
        seen = {}
        import prax.observability.callbacks as mod
        monkeypatch.setattr(mod, "_record_metrics",
                            lambda m, i, o, e: seen.update(model=m, i=i, o=o))
        cb.on_llm_end(_resp(llm_output={"token_usage": None}), run_id=uuid4())
        assert seen == {"model": "unknown", "i": 0, "o": 0}
