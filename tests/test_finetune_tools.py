"""Tests for finetune_tools LangChain wrappers."""
import importlib


def test_finetune_harvest(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.finetune_tools"))
    svc = importlib.import_module("prax.services.finetune_service")

    monkeypatch.setattr(svc, "harvest_corrections", lambda since_hours=24: [{"messages": []}])
    monkeypatch.setattr(svc, "save_training_data", lambda ex, name=None: "/tmp/batch.jsonl")

    result = module.finetune_harvest.invoke({})
    assert "1 training example" in result


def test_finetune_start(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.finetune_tools"))
    svc = importlib.import_module("prax.services.finetune_service")

    monkeypatch.setattr(
        svc, "start_training",
        lambda data_path=None: {"status": "started", "adapter_name": "adapter_test", "data_path": "/tmp/d.jsonl", "pid": 1234},
    )

    result = module.finetune_start.invoke({})
    assert "started" in result.lower()
    assert "adapter_test" in result


def test_finetune_status(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.finetune_tools"))
    svc = importlib.import_module("prax.services.finetune_service")

    monkeypatch.setattr(svc, "get_training_status", lambda: {"state": "running", "step": 30, "max_steps": 60, "adapter_name": "test"})

    result = module.finetune_status.invoke({})
    assert "30" in result
    assert "60" in result


def test_finetune_list_adapters(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.finetune_tools"))
    svc = importlib.import_module("prax.services.finetune_service")

    monkeypatch.setattr(svc, "list_adapters", lambda: [{"name": "v1", "created_at": "2026-03-20"}])
    monkeypatch.setattr(svc, "get_active_adapter", lambda: "v1")

    result = module.finetune_list_adapters.invoke({})
    assert "v1" in result
    assert "ACTIVE" in result
