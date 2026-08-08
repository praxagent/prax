"""Cached input tokens must be visible, and must not disturb existing totals.

An agent turn is a loop of model calls that each re-send the system prompt, the
tool definitions and the whole accumulated conversation. Whether the provider
served that prefix from cache is the largest single cost lever available, and
`tokens_in` alone cannot tell you — a cached prefix and an uncached one look
identical in that number.

Prax previously read only `input_tokens`/`output_tokens` from
`AIMessage.usage_metadata`, so even where caching was already happening
server-side it was invisible and priced at full rate.

These tests are the measurement half of task #59. They deliberately do NOT
assert that caching occurs — that is a provider behaviour to be measured, not
a property to be tested.
"""

from prax.agent.trace import ExecutionGraph, SpanNode
from prax.observability.callbacks import _cache_tokens_from_message


class _Msg:
    def __init__(self, usage_metadata):
        self.usage_metadata = usage_metadata


class _Gen:
    def __init__(self, message):
        self.message = message


class _Resp:
    def __init__(self, usage_metadata):
        self.generations = [[_Gen(_Msg(usage_metadata))]]


# --------------------------------------------------------------------------- #
# Reading the provider's report
# --------------------------------------------------------------------------- #

class TestCacheTokenExtraction:
    def test_reads_normalised_input_token_details(self):
        r = _Resp({"input_tokens": 5000, "output_tokens": 120,
                   "input_token_details": {"cache_read": 4800, "cache_creation": 0}})
        assert _cache_tokens_from_message(r) == (4800, 0)

    def test_reads_cache_creation(self):
        r = _Resp({"input_tokens": 5000, "output_tokens": 120,
                   "input_token_details": {"cache_read": 0, "cache_creation": 4800}})
        assert _cache_tokens_from_message(r) == (0, 4800)

    def test_absent_details_are_zero_not_an_error(self):
        """A provider that reports nothing must be indistinguishable from a
        provider that reports zero — and must never raise. Accounting cannot
        break a call."""
        assert _cache_tokens_from_message(_Resp({"input_tokens": 10})) == (0, 0)
        assert _cache_tokens_from_message(_Resp({})) == (0, 0)

    def test_malformed_response_is_zero(self):
        class Broken:
            generations = None
        assert _cache_tokens_from_message(Broken()) == (0, 0)
        assert _cache_tokens_from_message(object()) == (0, 0)


# --------------------------------------------------------------------------- #
# Attribution to the trace
# --------------------------------------------------------------------------- #

def _graph_with_span():
    g = ExecutionGraph("t1")
    g.add_node(SpanNode(span_id="s1", trace_id="t1", name="orchestrator",
                        parent_id=None, spoke_or_category="orchestrator"))
    return g


class TestTraceAttribution:
    def test_cached_tokens_are_a_subset_of_tokens_in(self):
        """The load-bearing invariant. Cached input is already counted in
        tokens_in and must never be added to it — otherwise every existing
        total and every cost figure built on one silently inflates the moment a
        provider starts reporting cache details."""
        g = _graph_with_span()
        g.add_llm_usage("s1", "m", 5000, 120, 4800, 0)
        node = g._nodes["s1"]
        assert node.tokens_in == 5000
        assert node.cached_tokens_in == 4800
        assert node.cached_tokens_in <= node.tokens_in

    def test_totals_match_the_pre_cache_behaviour(self):
        """Calling without cache arguments must produce exactly what it did
        before the fields existed."""
        g = _graph_with_span()
        g.add_llm_usage("s1", "m", 900, 40)
        node = g._nodes["s1"]
        assert (node.tokens_in, node.tokens_out) == (900, 40)
        assert node.cached_tokens_in == 0 and node.cache_write_tokens == 0

    def test_accumulates_across_calls(self):
        g = _graph_with_span()
        g.add_llm_usage("s1", "m", 5000, 100, 0, 4800)     # first call writes
        g.add_llm_usage("s1", "m", 5200, 90, 4800, 0)      # later calls read
        g.add_llm_usage("s1", "m", 5400, 95, 4800, 0)
        node = g._nodes["s1"]
        assert node.tokens_in == 15600
        assert node.cached_tokens_in == 9600
        assert node.cache_write_tokens == 4800

    def test_unknown_span_is_ignored(self):
        g = _graph_with_span()
        g.add_llm_usage("nope", "m", 100, 10, 50, 0)  # must not raise
        assert g._nodes["s1"].tokens_in == 0


# --------------------------------------------------------------------------- #
# What a person actually reads
# --------------------------------------------------------------------------- #

class TestSerialisation:
    def test_cache_fields_surface_in_the_trace(self):
        g = _graph_with_span()
        g.add_llm_usage("s1", "m", 5000, 120, 4800, 0)
        d = g.to_dict()
        assert d["cached_tokens_in"] == 4800
        assert d["nodes"][0]["cached_tokens_in"] == 4800

    def test_zero_cache_is_omitted_entirely(self):
        """A provider that reports nothing must not produce a `0` that reads
        like a measured 'no caching happened'. Absent and zero are different
        claims — the same rule cost_estimate_usd already follows."""
        g = _graph_with_span()
        g.add_llm_usage("s1", "m", 900, 40)
        d = g.to_dict()
        assert "cached_tokens_in" not in d
        assert "cached_tokens_in" not in d["nodes"][0]

    def test_existing_trace_keys_are_unchanged(self):
        g = _graph_with_span()
        g.add_llm_usage("s1", "m", 900, 40, 300, 0)
        d = g.to_dict()
        assert d["tokens_in"] == 900 and d["tokens_out"] == 40
        assert d["nodes"][0]["llm_calls"] == 1


# --------------------------------------------------------------------------- #
# The behaviour half — marking a cacheable prefix
# --------------------------------------------------------------------------- #

class _SysMsg:
    type = "system"

    def __init__(self, content):
        self.content = content


class _HumanMsg:
    type = "human"

    def __init__(self, content):
        self.content = content


class _AnthropicModel:  # name is what the check keys on
    pass


class ChatAnthropic(_AnthropicModel):
    pass


class ChatOpenAI:
    pass


class _Req:
    def __init__(self, model, messages):
        self.model = model
        self.messages = messages


class TestPromptPrefixCache:
    def _mark(self, model, messages):
        from prax.agent.loop_middleware import PromptPrefixCache
        req = _Req(model, messages)
        PromptPrefixCache._mark(req)
        return req

    def test_marks_the_anthropic_system_prompt(self):
        sys = _SysMsg("You are Prax.")
        self._mark(ChatAnthropic(), [sys, _HumanMsg("hi")])
        assert sys.content == [{
            "type": "text", "text": "You are Prax.",
            "cache_control": {"type": "ephemeral"},
        }]

    def test_leaves_non_anthropic_untouched(self):
        """OpenAI-compatible endpoints cache automatically; enabling the flag
        there must be a no-op, not a mangled request."""
        sys = _SysMsg("You are Prax.")
        self._mark(ChatOpenAI(), [sys, _HumanMsg("hi")])
        assert sys.content == "You are Prax."

    def test_does_not_touch_conversation_messages(self):
        human = _HumanMsg("hi")
        self._mark(ChatAnthropic(), [_SysMsg("sys"), human])
        assert human.content == "hi"

    def test_marks_only_the_last_block_of_a_block_list(self):
        sys = _SysMsg([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
        self._mark(ChatAnthropic(), [sys])
        assert "cache_control" not in sys.content[0]
        assert sys.content[1]["cache_control"] == {"type": "ephemeral"}

    def test_is_idempotent(self):
        """A retry re-entering the same request must not burn a second
        breakpoint — Anthropic allows only a handful."""
        sys = _SysMsg([{"type": "text", "text": "a",
                        "cache_control": {"type": "ephemeral"}}])
        self._mark(ChatAnthropic(), [sys])
        assert len(sys.content) == 1
        assert sys.content[0]["cache_control"] == {"type": "ephemeral"}

    def test_empty_system_prompt_is_left_alone(self):
        sys = _SysMsg("   ")
        self._mark(ChatAnthropic(), [sys])
        assert sys.content == "   "

    def test_never_raises_on_a_malformed_request(self):
        from prax.agent.loop_middleware import PromptPrefixCache
        PromptPrefixCache._mark(object())
        PromptPrefixCache._mark(_Req(ChatAnthropic(), None))
        PromptPrefixCache._mark(_Req(None, []))

    def test_absent_from_the_stack_by_default(self):
        from prax.agent.loop_middleware import PromptPrefixCache, default_middleware
        assert not any(isinstance(m, PromptPrefixCache)
                       for m in default_middleware())
