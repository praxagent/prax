import importlib
import sys

import pytest

TEST_ENV = {
    "FLASK_SECRET_KEY": "test-secret",
    "SESSION_TYPE": "filesystem",
    "NGROCK_URL": "https://ngrok.test",
    "DEBUG": "False",
    "LOG_PATH": "test.log",
    "PORT": "5000",
    "DATABASE_NAME": "test.db",
    "OPENAI_KEY": "sk-test",
    "ANTHROPIC_KEY": "sk-ant-test",
    "GOOGLE_API_KEY": "g-test",
    "GOOGLE_CSE_ID": "cx-test",
    "GOOGLE_VERTEX_PROJECT": "vertex-proj",
    "GOOGLE_VERTEX_LOCATION": "us-central1",
    "ELEVENLABS_API_KEY": "eleven-test",
    "TWILIO_ACCOUNT_SID": "ACXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    "TWILIO_AUTH_TOKEN": "auth-token",
    "BASE_MODEL": "gpt-test",
    "LLM_PROVIDER": "openai",
    "AGENT_NAME": "Prax",
    "AGENT_TEMPERATURE": "0.3",
    "PHONE_TO_NAME_MAP": '{"+10000000000": "Tester"}',
    "PHONE_TO_EMAIL_MAP": '{"+10000000000": "tester@example.com"}',
    "PHONE_TO_GREETING_MAP": '{"+10000000000": "greeting.mp3"}',
    "WORKSPACE_DIR": "/tmp/test_workspaces",
    "SANDBOX_IMAGE": "prax-sandbox:latest",
    "SANDBOX_TIMEOUT": "1800",
    "SANDBOX_MAX_CONCURRENT": "5",
    "SANDBOX_DEFAULT_MODEL": "anthropic/claude-sonnet-4-5",
    "SANDBOX_MEM_LIMIT": "1g",
    "SANDBOX_CPU_LIMIT": "2000000000",
    "SANDBOX_MAX_ROUNDS": "10",
    # Fine-tuning (disabled in tests by default)
    "FINETUNE_ENABLED": "false",
    "VLLM_BASE_URL": "http://localhost:8000/v1",
    "LOCAL_MODEL": "Qwen/Qwen3-8B",
    "FINETUNE_BASE_MODEL": "unsloth/Qwen3-8B-unsloth-bnb-4bit",
    "FINETUNE_OUTPUT_DIR": "/tmp/test_adapters",
    # Browser
    "BROWSER_HEADLESS": "true",
    "BROWSER_TIMEOUT": "10000",
    "BROWSER_VNC_ENABLED": "false",
    "BROWSER_VNC_BASE_PORT": "5900",
    # Self-improvement (disabled in tests by default)
    "SELF_IMPROVE_ENABLED": "false",
    # Discord (disabled in tests by default)
    "DISCORD_BOT_TOKEN": "",
    "DISCORD_ALLOWED_USERS": '{"999000000000000001": "TestUser"}',
    "DISCORD_ALLOWED_CHANNELS": "",
    "DISCORD_TO_PHONE_MAP": "",
}


@pytest.fixture(autouse=True)
def configure_test_env(monkeypatch, tmp_path):
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)

    # Use a per-test temp dir so no test writes to ./workspaces.
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "ws"))

    import prax.settings as settings_mod

    importlib.reload(settings_mod)

    # Modules that did `from prax.settings import settings` hold a stale
    # reference after the reload above.  Patch the live settings on every
    # already-imported service module so cross-module calls stay consistent.
    new_settings = settings_mod.settings
    for mod_name, mod in list(sys.modules.items()):
        if (
            mod is not None
            and mod_name.startswith("prax.")
            and hasattr(mod, "settings")
            and mod is not settings_mod
        ):
            try:
                monkeypatch.setattr(mod, "settings", new_settings)
            except Exception:
                pass
    yield
