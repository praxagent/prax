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

    assert llm_module.build_llm() == ('openai', {'model': 'gpt-test', 'api_key': 'sk-test', 'temperature': 0.2})
    assert llm_module.build_llm(provider='anthropic') == ('anthropic', {'model': 'gpt-test', 'api_key': 'ant-test', 'temperature': 0.2})
    assert llm_module.build_llm(provider='google') == ('vertex', {'model': 'gpt-test', 'temperature': 0.2, 'project': 'proj', 'location': 'loc'})
    assert llm_module.build_llm(provider='ollama') == ('ollama', {'model': 'gpt-test', 'temperature': 0.2})


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
