"""The secrets-proxy needs OS-level networking vars (HTTPS_PROXY, CA bundle) in
os.environ, but Prax must NOT blanket-load .env (that would leak API keys into the
environment where the sandbox/agent could read them). So only a fixed non-secret
allowlist is exported. These tests pin exactly that: proxy vars in, secrets never.
"""
from __future__ import annotations

import os

from prax.settings import _export_proxy_env_from_dotenv


def test_exports_proxy_vars_but_never_secrets(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "HTTPS_PROXY=http://127.0.0.1:8786\n"
        "REQUESTS_CA_BUNDLE=/abs/bundle.pem\n"
        "NO_PROXY=localhost,127.0.0.1\n"
        "# a comment\n"
        "OPENAI_KEY=sk-REAL-SECRET\n"
        "SERPER_DEV_API_KEY=serper-REAL-SECRET\n"
        'ELEVENLABS_API_KEY="quoted-secret"\n'
    )
    for k in ("HTTPS_PROXY", "REQUESTS_CA_BUNDLE", "NO_PROXY", "OPENAI_KEY",
              "SERPER_DEV_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    _export_proxy_env_from_dotenv(str(env))

    # allow-listed networking vars ARE exported
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:8786"
    assert os.environ["REQUESTS_CA_BUNDLE"] == "/abs/bundle.pem"
    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1"
    # secrets are NEVER exported to the environment (keyless firewall)
    assert "OPENAI_KEY" not in os.environ
    assert "SERPER_DEV_API_KEY" not in os.environ
    assert "ELEVENLABS_API_KEY" not in os.environ


def test_does_not_override_an_already_set_var(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=http://from-dotenv:8786\n")
    monkeypatch.setenv("HTTPS_PROXY", "http://already-set:9999")  # Docker's env_file wins
    _export_proxy_env_from_dotenv(str(env))
    assert os.environ["HTTPS_PROXY"] == "http://already-set:9999"


def test_missing_env_file_is_a_noop(tmp_path):
    _export_proxy_env_from_dotenv(str(tmp_path / "nope.env"))  # must not raise
