"""Runtime LLM-config changes persist to a gitignored overlay, never the seed."""
from __future__ import annotations

import yaml

import prax.plugins.llm_config as cfg


def _seed(tmp_path, data):
    (tmp_path / "llm_routing.yaml").write_text(yaml.dump(data))
    return tmp_path / "llm_routing.yaml"


def test_update_writes_runtime_not_seed(monkeypatch, tmp_path):
    seed = _seed(tmp_path, {"components": {"orchestrator": {"tier": "medium"}}})
    monkeypatch.setattr(cfg, "_CONFIG_PATH", seed)

    cfg.update_component_config("orchestrator", tier="high")

    # seed is byte-for-byte untouched
    assert yaml.safe_load(seed.read_text()) == {"components": {"orchestrator": {"tier": "medium"}}}
    # runtime overlay holds the change
    runtime = tmp_path / "llm_routing.runtime.yaml"
    assert runtime.is_file()
    assert yaml.safe_load(runtime.read_text())["components"]["orchestrator"]["tier"] == "high"


def test_effective_config_merges_runtime_over_seed(monkeypatch, tmp_path):
    seed = _seed(tmp_path, {"defaults": {"provider": "openai"},
                            "components": {"orchestrator": {"tier": "medium", "temperature": 0.7}}})
    monkeypatch.setattr(cfg, "_CONFIG_PATH", seed)

    cfg.update_component_config("orchestrator", tier="high")  # override one key
    eff = cfg.get_component_config("orchestrator")
    assert eff["tier"] == "high"            # runtime override wins
    assert eff["temperature"] == 0.7        # seed key preserved (not clobbered)
    assert eff["provider"] == "openai"      # seed default preserved


def test_no_runtime_file_reads_seed(monkeypatch, tmp_path):
    seed = _seed(tmp_path, {"components": {"orchestrator": {"tier": "medium"}}})
    monkeypatch.setattr(cfg, "_CONFIG_PATH", seed)
    assert cfg.get_component_config("orchestrator")["tier"] == "medium"


def test_env_override_still_wins_over_runtime(monkeypatch, tmp_path):
    seed = _seed(tmp_path, {"components": {"orchestrator": {"tier": "medium"}}})
    monkeypatch.setattr(cfg, "_CONFIG_PATH", seed)
    cfg.update_component_config("orchestrator", tier="high")
    monkeypatch.setenv("ORCHESTRATOR_TIER", "pro")
    assert cfg.get_component_config("orchestrator")["tier"] == "pro"  # env > runtime > seed
