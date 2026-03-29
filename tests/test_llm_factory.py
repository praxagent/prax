import importlib
from types import SimpleNamespace

import pytest


def test_build_llm_for_each_provider(monkeypatch):
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-test',
        agent_temperature=0.2,
        openai_key='sk-test',
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


def test_build_llm_with_tier(monkeypatch):
    """Tier parameter resolves to a concrete model name."""
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-test',
        agent_temperature=0.2,
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
        openai_key='sk-test',
    )
    monkeypatch.setattr(llm_module, 'settings', dummy_settings, raising=False)
    monkeypatch.setattr(llm_module, 'ChatOpenAI', lambda **kwargs: ('openai', kwargs))

    _, kw = llm_module.build_llm(model='explicit-model', tier='high')
    assert kw['model'] == 'explicit-model'
    assert kw['api_key'] == 'sk-test'


def test_build_llm_requires_keys(monkeypatch):
    llm_module = importlib.reload(importlib.import_module('prax.agent.llm_factory'))

    dummy_settings = SimpleNamespace(
        default_llm_provider='openai',
        base_model='gpt-test',
        agent_temperature=0.2,
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
