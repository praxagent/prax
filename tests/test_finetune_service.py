"""Tests for finetune_service — all external calls (vLLM, subprocess) are mocked."""
import importlib
import json

import pytest


@pytest.fixture()
def ft_mod(monkeypatch, tmp_path):
    """Reload finetune_service with mocked settings."""
    monkeypatch.setenv("FINETUNE_ENABLED", "true")
    monkeypatch.setenv("FINETUNE_OUTPUT_DIR", str(tmp_path / "adapters"))
    monkeypatch.setenv("DATABASE_NAME", str(tmp_path / "test.db"))

    import prax.settings as settings_mod
    importlib.reload(settings_mod)

    module = importlib.reload(
        importlib.import_module("prax.services.finetune_service")
    )
    monkeypatch.setattr(module.settings, "finetune_enabled", True)
    monkeypatch.setattr(module.settings, "finetune_output_dir", str(tmp_path / "adapters"))
    monkeypatch.setattr(module.settings, "database_name", str(tmp_path / "test.db"))
    monkeypatch.setattr(module.settings, "finetune_base_model", "test-model")
    monkeypatch.setattr(module.settings, "finetune_max_steps", 10)
    monkeypatch.setattr(module.settings, "finetune_learning_rate", 2e-4)
    monkeypatch.setattr(module.settings, "finetune_lora_rank", 8)
    monkeypatch.setattr(module.settings, "vllm_base_url", "http://localhost:9999/v1")

    # Reset module state.
    module._training_process = None
    module._training_status = {"state": "idle"}

    return module


@pytest.fixture()
def ft_disabled(monkeypatch, tmp_path):
    """Reload with finetune disabled."""
    monkeypatch.setenv("FINETUNE_ENABLED", "false")
    import prax.settings as settings_mod
    importlib.reload(settings_mod)

    module = importlib.reload(
        importlib.import_module("prax.services.finetune_service")
    )
    monkeypatch.setattr(module.settings, "finetune_enabled", False)
    return module


# ---------- Feature flag ---------------------------------------------------

class TestFeatureFlag:
    def test_disabled_returns_empty(self, ft_disabled):
        assert ft_disabled.harvest_corrections() == []

    def test_disabled_training_blocked(self, ft_disabled):
        result = ft_disabled.start_training()
        assert "error" in result
        assert "disabled" in result["error"].lower()

    def test_disabled_status(self, ft_disabled):
        assert ft_disabled.get_training_status()["state"] == "disabled"


# ---------- Correction harvesting ------------------------------------------

class TestHarvesting:
    def _seed_db(self, ft_mod, tmp_path):
        """Create a test DB with a correction conversation."""
        import sqlite3
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, data TEXT)")
        conversation = [
            {"role": "system", "content": "You are Joanna."},
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "The capital of France is Berlin."},
            {"role": "user", "content": "No, that's wrong. It's Paris."},
            {"role": "assistant", "content": "You're right, the capital of France is Paris."},
        ]
        conn.execute(
            "INSERT OR REPLACE INTO conversations (id, data) VALUES (?, ?)",
            (10000000000, json.dumps(conversation)),
        )
        conn.commit()
        conn.close()

    def test_finds_corrections(self, ft_mod, tmp_path):
        self._seed_db(ft_mod, tmp_path)
        examples = ft_mod.harvest_corrections(since_hours=9999)
        assert len(examples) >= 1
        # The training example should contain the corrected response.
        assert any("Paris" in msg["content"] for ex in examples for msg in ex["messages"])

    def test_no_corrections(self, ft_mod):
        examples = ft_mod.harvest_corrections()
        assert examples == []


# ---------- Training data save --------------------------------------------

class TestSaveTrainingData:
    def test_saves_jsonl(self, ft_mod, tmp_path):
        examples = [
            {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}
        ]
        path = ft_mod.save_training_data(examples, "test_batch")
        assert "test_batch.jsonl" in path
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["messages"][0]["role"] == "user"


# ---------- Adapter registry -----------------------------------------------

class TestRegistry:
    def test_read_empty(self, ft_mod):
        reg = ft_mod._read_registry()
        assert reg["active_adapter"] is None
        assert reg["adapters"] == []

    def test_write_and_read(self, ft_mod, tmp_path):
        (tmp_path / "adapters").mkdir(exist_ok=True)
        ft_mod._write_registry({"active_adapter": "test", "previous_adapter": None, "adapters": []})
        reg = ft_mod._read_registry()
        assert reg["active_adapter"] == "test"


# ---------- Adapter management (mocked vLLM) --------------------------------

class TestAdapterManagement:
    def test_load_success(self, ft_mod, tmp_path, monkeypatch):
        adapter_dir = tmp_path / "adapters" / "test_adapter"
        adapter_dir.mkdir(parents=True)

        monkeypatch.setattr(ft_mod.requests, "post", lambda url, json, timeout: type("R", (), {"raise_for_status": lambda s: None})())
        result = ft_mod.load_adapter("test_adapter", str(adapter_dir))
        assert result["status"] == "loaded"

    def test_load_missing_dir(self, ft_mod):
        result = ft_mod.load_adapter("nonexistent")
        assert "error" in result

    def test_unload_success(self, ft_mod, monkeypatch):
        monkeypatch.setattr(ft_mod.requests, "post", lambda url, json, timeout: type("R", (), {"raise_for_status": lambda s: None})())
        result = ft_mod.unload_adapter("test_adapter")
        assert result["status"] == "unloaded"

    def test_promote_and_rollback(self, ft_mod, tmp_path, monkeypatch):
        (tmp_path / "adapters").mkdir(exist_ok=True)
        ft_mod.promote_adapter("adapter_v1")
        reg = ft_mod._read_registry()
        assert reg["active_adapter"] == "adapter_v1"

        ft_mod.promote_adapter("adapter_v2")
        reg = ft_mod._read_registry()
        assert reg["active_adapter"] == "adapter_v2"
        assert reg["previous_adapter"] == "adapter_v1"

    def test_list_adapters(self, ft_mod, tmp_path):
        (tmp_path / "adapters").mkdir(exist_ok=True)
        ft_mod.promote_adapter("adapter_v1")
        adapters = ft_mod.list_adapters()
        assert len(adapters) == 1
        assert adapters[0]["name"] == "adapter_v1"


# ---------- Verify adapter (mocked vLLM) -----------------------------------

class TestVerifyAdapter:
    def test_verify_pass(self, ft_mod, monkeypatch):
        def mock_post(url, json, timeout):
            content = "The answer is 4" if "2 + 2" in json["messages"][0]["content"] else "Bonjour!"
            return type("R", (), {
                "raise_for_status": lambda s: None,
                "json": lambda s: {"choices": [{"message": {"content": content}}]},
            })()

        monkeypatch.setattr(ft_mod.requests, "post", mock_post)
        result = ft_mod.verify_adapter("test")
        assert result["verdict"] == "pass"
        assert result["passed"] == 2

    def test_verify_fail(self, ft_mod, monkeypatch):
        def mock_post(url, json, timeout):
            return type("R", (), {
                "raise_for_status": lambda s: None,
                "json": lambda s: {"choices": [{"message": {"content": "I don't know"}}]},
            })()

        monkeypatch.setattr(ft_mod.requests, "post", mock_post)
        result = ft_mod.verify_adapter("test")
        assert result["verdict"] == "fail"


# ---------- Correction detection -------------------------------------------

class TestCorrectionDetection:
    def test_detects_corrections(self, ft_mod):
        assert ft_mod._is_correction("No, that's wrong")
        assert ft_mod._is_correction("try again please")
        assert ft_mod._is_correction("Actually, I meant something else")
        assert ft_mod._is_correction("nope")

    def test_ignores_normal(self, ft_mod):
        assert not ft_mod._is_correction("What is the weather?")
        assert not ft_mod._is_correction("Thank you!")
        assert not ft_mod._is_correction("Tell me more about that")
