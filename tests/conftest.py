import importlib
import sys
from pathlib import Path

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
    # Link building is config-only in tests so generated URLs are deterministic
    # regardless of whether the test host happens to run Tailscale/ngrok.  The
    # auto-detect path (default ON in prod) is exercised in test_deployment_info.
    "PUBLIC_URL_AUTODETECT": "false",
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
    # Social-fetch / browser flags — pin to the shipped defaults so tests stay
    # hermetic even when the developer's .env opts in (flag-on behavior is
    # tested explicitly via monkeypatch, never ambiently).
    "TWITTER_THREAD_FETCH": "false",
    "URL_FETCH_SOURCE_TAGS": "false",
    "BROWSER_SANDBOX_ONLY": "false",
    "WEB_SEARCH_TIMEOUT_S": "0",
    "SEARCH_PROVIDER": "legacy",
    # Eval-gated flags flipped ON in the live .env — pin to shipped defaults so
    # local `make ci` matches keyless GitHub CI regardless of the dev's .env.
    "AGENT_MIDDLEWARE_ENABLED": "false",
    "PROMPT_SELECTIVITY_ENABLED": "false",
    "AUTO_TIER_ESCALATION": "false",
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

    # Safety net: guarantee the WORKSPACE_DIR override actually took effect, so
    # NO test can create workspaces in the real project tree (the leak that used
    # to leave stray usr_*/ dirs behind).  Any test that bypasses this isolation
    # — e.g. forgets the reload, or hardcodes a path — fails loudly here.
    project_root = Path(__file__).resolve().parents[2]  # /…/PRAX (above the repo)
    active_ws = Path(new_settings.workspace_dir).resolve()
    assert project_root != active_ws and project_root not in active_ws.parents, (
        f"Test workspace_dir {active_ws} is inside the project tree {project_root}; "
        "it must be a temp dir so tests never leak into the real workspaces/."
    )

    # Circuit breakers live in a process-global registry
    # (prax.agent.circuit_breaker._breakers).  Reset them before each test so
    # accumulated LLM-call failures from one test don't leave a breaker OPEN and
    # mask another test's assertions — e.g. build_llm()'s provider-validation
    # raising ConnectionError ("breaker OPEN") instead of ValueError.
    try:
        from prax.agent.circuit_breaker import reset_all as _reset_breakers
        _reset_breakers()
    except Exception:
        pass

    # User-context ContextVars are process-global. Many tests do
    # `current_user_id.set(...)` without resetting the token, so the value leaks
    # into the next test (which may not set it). Reset them to their declared
    # defaults per-test — fixes that latent cross-test leak.
    try:
        from prax.agent import user_context as _uc
        _uc.current_user_id.set(None)
        _uc.current_user.set(None)
        _uc.current_channel_id.set(None)
        _uc.current_channel_name.set("")
        _uc.current_user_message.set("")
        _uc.current_component.set("orchestrator")
        _uc.current_active_view.set("")
    except Exception:
        pass
    yield
