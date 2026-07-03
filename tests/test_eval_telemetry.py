"""Key-free tests for eval telemetry (prax.eval.telemetry)."""
from __future__ import annotations

from prax.eval.telemetry import UsageCollector, _extract_usage


class _FakeMsg:
    def __init__(self, usage_metadata):
        self.usage_metadata = usage_metadata


class _FakeGen:
    def __init__(self, usage_metadata):
        self.message = _FakeMsg(usage_metadata)


class _FakeResult:
    def __init__(self, llm_output=None, generations=None):
        self.llm_output = llm_output
        self.generations = generations or []


def test_extract_usage_openai_llm_output_shape():
    r = _FakeResult(llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    assert _extract_usage(r) == (10, 5)


def test_extract_usage_usage_metadata_shape():
    # The shape vLLM / Ollama / ds4 emit via LangChain.
    r = _FakeResult(generations=[[_FakeGen({"input_tokens": 7, "output_tokens": 3})]])
    assert _extract_usage(r) == (7, 3)


def test_extract_usage_empty_is_zero():
    assert _extract_usage(_FakeResult()) == (0, 0)


class _OllamaGen:
    """Community ChatOllama: no usage_metadata, counts in generation_info."""

    def __init__(self, generation_info):
        self.message = None
        self.generation_info = generation_info


def test_extract_usage_ollama_generation_info_shape():
    # Regression: without the Shape-3 fallback an Ollama run records ZERO tokens.
    r = _FakeResult(generations=[[_OllamaGen({"prompt_eval_count": 11, "eval_count": 4})]])
    assert _extract_usage(r) == (11, 4)


def test_collector_accumulates_tokens_and_calls():
    c = UsageCollector()
    c.add(10, 5)
    c.add(2, 1)
    snap = c.snapshot()
    assert snap["prompt_tokens"] == 12
    assert snap["completion_tokens"] == 6
    assert snap["total_tokens"] == 18
    assert snap["llm_calls"] == 2
    assert "wall_time_s" in snap


def test_spokes_derived_from_delegate_tools():
    c = UsageCollector()
    for t in ["workspace_save", "delegate_research", "delegate_research", "delegate_browser"]:
        c.add_tool(t)
    assert c.spokes() == ["research", "browser"]
    snap = c.snapshot()
    assert snap["tools"].count("delegate_research") == 2
    assert snap["spokes"] == ["research", "browser"]
