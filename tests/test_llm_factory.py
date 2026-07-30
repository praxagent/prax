import importlib
from types import SimpleNamespace

import pytest


def test_build_llm_for_each_provider(monkeypatch):
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-test',
        agent_temperature=0.2,
        llm_request_timeout=300,
        openai_key='sk-test',
        openai_base_url=None,
        anthropic_key='ant-test',
        google_vertex_project='proj',
        google_vertex_location='loc'
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)

    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))
    monkeypatch.setattr(llm_module, 'ChatAnthropic', lambda **kwargs: ('anthropic', kwargs))
    monkeypatch.setattr(llm_module, 'ChatVertexAI', lambda **kwargs: ('vertex', kwargs))
    monkeypatch.setattr(llm_module, 'ChatOllama', lambda **kwargs: ('ollama', kwargs))

    _, openai_kw = llm_module.build_llm()
    assert openai_kw['model'] == 'gpt-test'
    assert openai_kw['api_key'] == 'sk-test'
    assert openai_kw['temperature'] == 0.2
    assert 'callbacks' in openai_kw
    # Default: OpenAI, so base_url is None and native logprobs are requested.
    assert openai_kw['base_url'] is None
    assert openai_kw['model_kwargs'] == {"logprobs": True, "top_logprobs": 5}

    _, anthro_kw = llm_module.build_llm(provider='anthropic')
    assert anthro_kw['model'] == 'gpt-test'
    assert anthro_kw['api_key'] == 'ant-test'

    _, vertex_kw = llm_module.build_llm(provider='google')
    assert vertex_kw['model'] == 'gpt-test'
    assert vertex_kw['project'] == 'proj'
    assert vertex_kw['location'] == 'loc'

    _, ollama_kw = llm_module.build_llm(provider='ollama')
    assert ollama_kw['model'] == 'gpt-test'
    assert ollama_kw['temperature'] == 0.2


def test_build_llm_openai_base_url_passthrough(monkeypatch):
    """OPENAI_BASE_URL routes to a third-party OpenAI-compatible provider and
    suppresses OpenAI-proprietary features (Responses API + logprobs)."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    dummy_settings = SimpleNamespace(
        default_llm_provider='openai', base_model='gpt-test', agent_temperature=0.2,
        llm_request_timeout=300, openai_key='or-key',
        openai_base_url='https://openrouter.ai/api/v1',
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))

    _, kw = llm_module.build_llm()
    assert kw['base_url'] == 'https://openrouter.ai/api/v1'
    assert kw['api_key'] == 'or-key'
    # Third-party endpoints don't support logprobs or the Responses API.
    assert kw['model_kwargs'] == {}
    assert kw['use_responses_api'] is False


def test_build_llm_openrouter_provider(monkeypatch):
    """The dedicated openrouter provider uses OpenRouter's base_url + key and
    no OpenAI-proprietary features."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    dummy_settings = SimpleNamespace(
        default_llm_provider='openai', base_model='deepseek/deepseek-v4-flash',
        agent_temperature=0.2, llm_request_timeout=300,
        openrouter_api_key='or-secret',
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))

    tag, kw = llm_module.build_llm(provider='openrouter')
    assert tag == 'openai'  # OpenRouter is served via the OpenAI-compatible client
    assert kw['base_url'] == 'https://openrouter.ai/api/v1'
    assert kw['api_key'] == 'or-secret'
    assert kw['model'] == 'deepseek/deepseek-v4-flash'
    # No Responses API / logprobs on OpenRouter.
    assert 'model_kwargs' not in kw or kw.get('model_kwargs') in ({}, None)
    assert 'use_responses_api' not in kw


def test_build_llm_openrouter_requires_key(monkeypatch):
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    dummy_settings = SimpleNamespace(
        default_llm_provider='openai', base_model='m', agent_temperature=0.2,
        llm_request_timeout=300, openrouter_api_key=None,
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    with pytest.raises(ValueError):
        llm_module.build_llm(provider='openrouter')


def test_build_llm_missing_openai_base_url_attr_defaults_none(monkeypatch):
    """getattr fallback: settings without the attribute must not crash."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    dummy_settings = SimpleNamespace(
        default_llm_provider='openai', base_model='gpt-test', agent_temperature=0.2,
        llm_request_timeout=300, openai_key='sk-test',  # no openai_base_url attr
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))
    _, kw = llm_module.build_llm()
    assert kw['base_url'] is None


def test_build_llm_with_tier(monkeypatch):
    """Tier parameter resolves to a concrete model name."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-test',
        agent_temperature=0.2,
        llm_request_timeout=300,
        openai_key='sk-test',
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))

    # Mock resolve_model to return a known value.
    import prax.agent.model_tiers as tiers_mod
    monkeypatch.setattr(tiers_mod, 'resolve_model', lambda tier: f'resolved-{tier}')

    _, kw = llm_module.build_llm(tier='medium')
    assert kw['model'] == 'resolved-medium'
    assert kw['api_key'] == 'sk-test'
    assert kw['temperature'] == 0.2


def test_build_llm_model_overrides_tier(monkeypatch):
    """Explicit model takes precedence over tier."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-test',
        agent_temperature=0.2,
        llm_request_timeout=300,
        openai_key='sk-test',
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))

    _, kw = llm_module.build_llm(model='explicit-model', tier='high')
    assert kw['model'] == 'explicit-model'
    assert kw['api_key'] == 'sk-test'


def test_build_llm_records_tier_choice(monkeypatch):
    """build_llm() records tier choices in the global ledger."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-test',
        agent_temperature=0.2,
        llm_request_timeout=300,
        openai_key='sk-test',
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))

    import prax.agent.model_tiers as tiers_mod
    monkeypatch.setattr(tiers_mod, 'resolve_model', lambda tier: f'resolved-{tier}')

    # Clear any stale entries
    llm_module.drain_tier_choices()

    llm_module.build_llm(tier='medium')
    llm_module.build_llm(tier='high')

    choices = llm_module.drain_tier_choices()
    assert len(choices) == 2
    assert choices[0]['tier_requested'] == 'medium'
    assert choices[0]['model'] == 'resolved-medium'
    assert choices[0]['provider'] == 'openai'
    assert choices[1]['tier_requested'] == 'high'
    assert choices[1]['model'] == 'resolved-high'

    # Drain clears the log
    assert llm_module.drain_tier_choices() == []


def test_peek_tier_choices_does_not_clear(monkeypatch):
    """peek_tier_choices() returns snapshot without clearing."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-test',
        agent_temperature=0.2,
        llm_request_timeout=300,
        openai_key='sk-test',
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))

    llm_module.drain_tier_choices()
    llm_module.build_llm(tier='low')

    peeked = llm_module.peek_tier_choices()
    assert len(peeked) == 1

    # Peek again — still there
    assert len(llm_module.peek_tier_choices()) == 1

    # Drain clears it
    llm_module.drain_tier_choices()
    assert len(llm_module.peek_tier_choices()) == 0


def test_tier_choice_records_default_when_no_tier(monkeypatch):
    """When no tier is specified, tier_requested should be 'default'."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-fallback',
        agent_temperature=0.2,
        llm_request_timeout=300,
        openai_key='sk-test',
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))

    llm_module.drain_tier_choices()
    llm_module.build_llm()  # no tier

    choices = llm_module.drain_tier_choices()
    assert len(choices) == 1
    assert choices[0]['tier_requested'] == 'default'
    assert choices[0]['model'] == 'gpt-fallback'


def test_build_llm_requires_keys(monkeypatch):
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-test',
        agent_temperature=0.2,
        llm_request_timeout=300,
        openai_key=None,
        anthropic_key=None,
        google_vertex_project=None,
        google_vertex_location=None
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)

    with pytest.raises(ValueError):
        llm_module.build_llm()

    with pytest.raises(ValueError):
        llm_module.build_llm(provider='anthropic')

    with pytest.raises(ValueError):
        llm_module.build_llm(provider='google')

    with pytest.raises(ValueError):
        llm_module.build_llm(provider='unknown')


def _mk(monkeypatch, llm_module, **overrides):
    base = dict(
        default_llm_provider='openai', base_model='gpt-test', agent_temperature=0.2,
        llm_request_timeout=300, openai_key='proxy-token',
    )
    base.update(overrides)
    monkeypatch.setattr(llm_module, 'settings', SimpleNamespace(**base), raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))


def test_base_url_is_openai_restores_responses_api_for_responses_only_models(monkeypatch):
    """Keyless mode (secrets proxy fronting real OpenAI) must not demote
    responses-only models to chat-completions — that 404s them."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    _mk(monkeypatch, llm_module,
        openai_base_url='https://127.0.0.1:8785/openai',
        openai_base_url_is_openai=True)
    _, kw = llm_module.build_llm(model='o3-mini')
    assert kw['use_responses_api'] is True
    assert kw['base_url'] == 'https://127.0.0.1:8785/openai'
    # Responses-family models still reject logprobs regardless of routing.
    assert kw['model_kwargs'] == {}


def test_base_url_is_openai_restores_logprobs_for_chat_models(monkeypatch):
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    _mk(monkeypatch, llm_module,
        openai_base_url='https://127.0.0.1:8785/openai',
        openai_base_url_is_openai=True)
    _, kw = llm_module.build_llm(model='gpt-5.4-mini')
    assert kw['model_kwargs'] == {"logprobs": True, "top_logprobs": 5}
    assert kw['use_responses_api'] is False


def test_third_party_base_url_still_demotes_even_with_retain_flag(monkeypatch):
    """OpenRouter-style endpoints have no Responses API: the retain flag must
    not force chaining onto a provider that cannot honour it."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    _mk(monkeypatch, llm_module,
        openai_base_url='https://openrouter.ai/api/v1',
        openai_base_url_is_openai=False,
        openai_retain_reasoning=True)
    _, kw = llm_module.build_llm(model='o3-mini')
    assert kw['use_responses_api'] is False
    assert kw['use_previous_response_id'] is False
    assert kw['model_kwargs'] == {}


def test_retain_reasoning_chains_previous_response_id(monkeypatch):
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    _mk(monkeypatch, llm_module,
        openai_base_url='https://127.0.0.1:8785/openai',
        openai_base_url_is_openai=True,
        openai_retain_reasoning=True)
    _, kw = llm_module.build_llm(model='gpt-5.5-pro')
    assert kw['use_responses_api'] is True
    assert kw['use_previous_response_id'] is True


def test_retain_reasoning_defaults_off(monkeypatch):
    """Prior behavior with no flags: direct OpenAI, responses-only model,
    Responses API on, NO previous_response_id chaining."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    _mk(monkeypatch, llm_module, openai_base_url=None)
    _, kw = llm_module.build_llm(model='o3-mini')
    assert kw['use_responses_api'] is True
    assert kw['use_previous_response_id'] is False


def test_retain_reasoning_works_without_base_url(monkeypatch):
    """The retain flag is independent of keyless mode — direct OpenAI too."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))
    _mk(monkeypatch, llm_module, openai_base_url=None, openai_retain_reasoning=True)
    _, kw = llm_module.build_llm(model='o3-mini')
    assert kw['use_previous_response_id'] is True
