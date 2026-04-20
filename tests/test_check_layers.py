"""Tests for scripts/check_layers.py — the architectural linter.

Black-box: run the linter on the real repo and on synthetic trees to
verify it catches the rule violations we care about and doesn't
flag legitimate code.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_layers.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_layers", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_layers"] = module
    spec.loader.exec_module(module)
    return module


def test_repo_passes_layer_check():
    """The real repo must pass — new work cannot regress the layer rules."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"Layer check failed on main repo:\n{result.stderr}"
    )
    assert "Layer check OK" in result.stdout


def test_detects_plugin_importing_services(tmp_path, monkeypatch):
    """Synthetic: plugin code that imports prax.services must be flagged."""
    module = _load_module()
    # Build a fake tree: prax/plugins/tools/bad/plugin.py imports prax.services.
    fake_root = tmp_path / "prax"
    (fake_root / "plugins" / "tools" / "bad").mkdir(parents=True)
    (fake_root / "plugins" / "tools" / "bad" / "plugin.py").write_text(
        "from prax.services import workspace_service\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "PRAX_ROOT", fake_root)
    monkeypatch.setattr(module, "ALLOWLIST", set())
    violations = module.scan()
    rules = {v.rule for v in violations}
    assert "plugin_imports_services" in rules


def test_detects_service_importing_agent(tmp_path, monkeypatch):
    module = _load_module()
    fake_root = tmp_path / "prax"
    (fake_root / "services").mkdir(parents=True)
    (fake_root / "services" / "bad.py").write_text(
        "from prax.agent.orchestrator import ConversationAgent\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "PRAX_ROOT", fake_root)
    monkeypatch.setattr(module, "ALLOWLIST", set())
    violations = module.scan()
    rules = {v.rule for v in violations}
    assert "services_imports_agent" in rules


def test_carve_out_allows_llm_factory(tmp_path, monkeypatch):
    """llm_factory and user_context are intentional carve-outs."""
    module = _load_module()
    fake_root = tmp_path / "prax"
    (fake_root / "services").mkdir(parents=True)
    (fake_root / "services" / "ok.py").write_text(
        "from prax.agent.llm_factory import build_llm\n"
        "from prax.agent.user_context import current_user_id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "PRAX_ROOT", fake_root)
    monkeypatch.setattr(module, "ALLOWLIST", set())
    violations = module.scan()
    assert violations == []


def test_detects_service_importing_blueprints(tmp_path, monkeypatch):
    module = _load_module()
    fake_root = tmp_path / "prax"
    (fake_root / "services").mkdir(parents=True)
    (fake_root / "services" / "bad.py").write_text(
        "from prax.blueprints.teamwork_routes import something\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "PRAX_ROOT", fake_root)
    monkeypatch.setattr(module, "ALLOWLIST", set())
    violations = module.scan()
    assert any(v.rule == "services_imports_blueprints" for v in violations)


def test_allowlist_hides_known_violation(tmp_path, monkeypatch):
    module = _load_module()
    fake_root = tmp_path / "prax"
    (fake_root / "services").mkdir(parents=True)
    svc = fake_root / "services" / "bad.py"
    svc.write_text("from prax.agent.trace import foo\n", encoding="utf-8")
    rel = svc.relative_to(tmp_path).as_posix()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "PRAX_ROOT", fake_root)
    monkeypatch.setattr(module, "ALLOWLIST", {f"{rel}:1 -> prax.agent.trace"})
    violations = module.scan()
    # Still flagged in the scan, but main() treats it as allow-listed.
    assert len(violations) == 1
    keys = {v.key() for v in violations}
    assert keys <= set(module.ALLOWLIST)
