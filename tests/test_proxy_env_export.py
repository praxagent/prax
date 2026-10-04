"""The live server exports .env into os.environ, credentials excepted.

Pydantic reads .env itself; the environment is for what reads the process
environment instead — the proxy/TLS vars (HTTPS_PROXY, CA bundle), per-component
overrides like ORCHESTRATOR_TIER, OPENAI_BASE_URL for SDK clients built without
one, libraries' own settings. Credentials never go there: every process Prax
starts inherits its environment.

Until this export covered it, Flask's app.run() loaded the WHOLE .env into the
environment (load_dotenv defaults to true), keys included, while settings.py
said keys never enter it. These tests pin the export, and that app.py tells
Flask not to load .env itself.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from prax.settings import _export_dotenv_config, is_secret_env

SECRETS = ("OPENAI_KEY", "SERPER_DEV_API_KEY", "ELEVENLABS_API_KEY", "DISCORD_BOT_TOKEN",
           "PRAX_SSH_KEY_B64", "SENDGRID_API_KEY", "SOME_NEW_SERVICE_SECRET")
CONFIG = ("HTTPS_PROXY", "REQUESTS_CA_BUNDLE", "NO_PROXY", "ORCHESTRATOR_TIER", "GIT_AUTHOR_NAME",
          "OPENAI_BASE_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PRAX_SKIP_DOTENV_EXPORT", raising=False)
    for k in SECRETS + CONFIG:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in SECRETS + CONFIG:   # the export writes os.environ directly
        os.environ.pop(k, None)


def test_exports_config_and_proxy_vars_but_never_secrets(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "HTTPS_PROXY=http://127.0.0.1:8786\n"
        "REQUESTS_CA_BUNDLE=/abs/bundle.pem\n"
        "NO_PROXY=localhost,127.0.0.1\n"
        "# a comment\n"
        "ORCHESTRATOR_TIER=high\n"
        "GIT_AUTHOR_NAME='Prax Bot'\n"
        "OPENAI_BASE_URL=https://127.0.0.1:8785/v1\n"
        "OPENAI_KEY=sk-REAL-SECRET\n"
        "SERPER_DEV_API_KEY=serper-REAL-SECRET\n"
        'ELEVENLABS_API_KEY="quoted-secret"\n'
        "DISCORD_BOT_TOKEN=discord-REAL\n"
        "PRAX_SSH_KEY_B64=c3No\n"
        "export SENDGRID_API_KEY=sg-REAL\n"
        "SOME_NEW_SERVICE_SECRET=x\n"
    )
    withheld = _export_dotenv_config(str(env))

    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:8786"
    assert os.environ["REQUESTS_CA_BUNDLE"] == "/abs/bundle.pem"
    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1"
    assert os.environ["ORCHESTRATOR_TIER"] == "high"            # llm_config reads the environment
    assert os.environ["GIT_AUTHOR_NAME"] == "Prax Bot"          # registry: not a secret
    assert os.environ["OPENAI_BASE_URL"] == "https://127.0.0.1:8785/v1"
    for secret in SECRETS:
        assert secret not in os.environ, secret
    assert set(withheld) == set(SECRETS)


def test_does_not_override_an_already_set_var(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=http://from-dotenv:8786\n")
    monkeypatch.setenv("HTTPS_PROXY", "http://already-set:9999")  # Docker's env_file wins
    _export_dotenv_config(str(env))
    assert os.environ["HTTPS_PROXY"] == "http://already-set:9999"


def test_missing_env_file_is_a_noop(tmp_path):
    assert _export_dotenv_config(str(tmp_path / "nope.env")) == []  # must not raise


def test_tests_skip_it(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ORCHESTRATOR_TIER=high\n")
    monkeypatch.setenv("PRAX_SKIP_DOTENV_EXPORT", "1")
    assert _export_dotenv_config(str(env)) == []
    assert "ORCHESTRATOR_TIER" not in os.environ


def test_every_registered_credential_counts_as_secret():
    from prax.services.credential_registry import NON_CREDENTIAL_ALIASES, REGISTRY
    assert all(is_secret_env(c.env) for c in REGISTRY)
    assert not any(is_secret_env(n) for n in NON_CREDENTIAL_ALIASES)
    for config in ("ORCHESTRATOR_TIER", "NYT_COOKIES_FILE", "OPENAI_BASE_URL", "AUTO_GENERATE_COVER"):
        assert not is_secret_env(config), config


def test_app_tells_flask_not_to_load_dotenv():
    """Flask's app.run() loads .env into the environment unless told not to."""
    tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
    runs = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "run" and getattr(n.func.value, "id", None) == "app"]
    assert runs, "app.run(...) not found in app.py"
    for call in runs:
        kw = {k.arg: k.value for k in call.keywords}
        assert isinstance(kw.get("load_dotenv"), ast.Constant) and kw["load_dotenv"].value is False
