"""Security regression: the source_* tools must never expose secrets/credentials.

Live-found 2026-07-20: source_grep took a model-controlled file_glob with no
secret exclusion, so `source_grep(pattern, ".env")` read the real .env and
returned the operator's API keys. source_read blocked .env by extension but a
secrets-bearing .json would have slipped through. These tests pin the guard.

Keyless: the secret check fires BEFORE the file-exists check, so it holds even
where no real .env is present (CI).
"""
from __future__ import annotations

from prax.agent import plugin_tools as pt


def test_is_secret_file_classification():
    for secret in (".env", ".env.local", ".env.prod", "identity.db",
                   "conversations.db", "identity.db.bak2-2026", "foo.sqlite",
                   "server.key", "cert.pem", "backup.bak", "id_rsa",
                   "id_ed25519.pub", "credentials.json"):
        assert pt._is_secret_file(secret) is True, secret
    for ok in (".env-example", "tools.py", "README.md", "config.yaml",
               "data.json", "settings.toml", "Makefile"):
        assert pt._is_secret_file(ok) is False, ok


def test_source_read_blocks_secrets_before_existence():
    # The secret guard returns "Blocked" regardless of whether the file exists.
    for secret in (".env", "identity.db", "some/dir/server.key", "app.pem"):
        out = pt.source_read.invoke({"path": secret})
        assert "Blocked" in out and "secrets" in out.lower(), (secret, out)


def test_source_read_still_allows_safe_template():
    # .env-example is a safe committed template — must stay readable.
    out = pt.source_read.invoke({"path": ".env-example"})
    assert "FLASK_SECRET_KEY" in out  # the template content, not a real secret


def test_source_grep_command_excludes_secret_globs(monkeypatch):
    captured = {}

    class _Res:
        returncode = 1
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res()

    monkeypatch.setattr(pt.subprocess if hasattr(pt, "subprocess") else __import__("subprocess"),
                        "run", fake_run)
    # Even asking to grep the .env directly must wire in the exclusions.
    pt.source_grep.invoke({"pattern": "KEY", "file_glob": ".env"})
    cmd = captured["cmd"]
    assert "--exclude" in cmd
    # every secret glob is excluded; grep's --exclude wins over --include
    assert ".env" in cmd and "*.db" in cmd and "*.key" in cmd and "*.pem" in cmd


def test_source_grep_still_greps_normal_source(monkeypatch):
    captured = {}

    class _Res:
        returncode = 0
        stdout = "./prax/agent/agent_loop.py:37:def build_agent_loop("
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res()

    monkeypatch.setattr(__import__("subprocess"), "run", fake_run)
    out = pt.source_grep.invoke({"pattern": "def build_agent_loop", "file_glob": "*.py"})
    assert "agent_loop.py" in out
    assert "--include" in captured["cmd"] and "*.py" in captured["cmd"]
