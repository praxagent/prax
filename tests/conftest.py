import importlib
import os
import sys
from pathlib import Path

import pytest

# --- No test may reach a credential-injecting proxy, or spawn paid work. -------
#
# Runs once, at conftest import, before any test module is collected.
#
# app.py calls _export_proxy_env_from_dotenv() at MODULE IMPORT, copying
# HTTPS_PROXY from the developer's .env into os.environ.  settings.py says in
# as many words that doing this in tests is wrong, but any test that merely
# imports `app` triggered it, and from then on every request in the session went
# out through the secrets proxy — which strips whatever key a request carries
# and injects the REAL one.  The fake "sk-test" below therefore bought real
# images: creating a Library space starts a background thread that calls
# gpt-image-1, and on 2026-09-22 production's forward-proxy log showed 993
# successful image generations from local test runs in a single day.  GitHub CI
# has no .env, so it never saw any of this.
#
# The export skips any variable that is already set, so pre-setting these to ""
# makes it a no-op however and whenever `app` is imported.  Empty proxy values
# mean "no proxy" to httpx, requests and urllib alike.  Tests that exercise the
# export itself (test_proxy_env_export.py) monkeypatch these and are restored.
#
# Uppercase is set to "" (present, so the export skips it); lowercase is REMOVED
# rather than blanked, because the export also skips a var whose lowercase twin
# exists — blanking both would stop test_proxy_env_export.py, which clears only
# the uppercase names, from ever exercising the real export.
for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ[_proxy_var] = ""
    os.environ.pop(_proxy_var.lower(), None)
# Never start the cover-image thread.  With the proxy gone a fake key fails
# fast anyway, but threads that outlive the test that started them made the
# suite's memory numbers wrong: their ~3 MB responses landed during whatever
# unrelated test was running, and one of them took the blame for a 5.5 GB peak.
os.environ["AUTO_GENERATE_COVER"] = "false"

# --- Tokenizer files: fetched once, BEFORE the network ban. ---------------------
#
# tiktoken downloads its encoding tables on first use and caches them.  With a
# warm cache that is invisible, which is how a test came to depend on the network
# without anyone noticing — until it ran on a fresh machine (and on GitHub CI's
# fresh runners, every time).  So warm the two encodings Prax uses here, as
# session setup, the way `uv sync` fetches packages before tests run: static,
# public files, no key involved, and the proxy vars are already blanked above so
# this cannot route through the credential proxy.  Offline, this quietly does
# nothing and any test that needs a tokenizer fails with the ban's clear error.
try:
    import tiktoken as _tiktoken  # noqa: E402

    for _enc in ("o200k_base", "cl100k_base"):  # DEFAULT_ENCODING, and the gpt-4/3.5 family
        _tiktoken.get_encoding(_enc)
except Exception:  # noqa: BLE001 — best effort; never break collection
    pass

# --- Network ban: no test may reach anything off this machine. ------------------
#
# The two fixes above close the path that actually burned money; this closes the
# CLASS.  Every socket connect in the test process is checked: loopback and unix
# sockets are allowed (Qdrant, Neo4j, Ollama and the Docker socket are local),
# everything else is refused — so a new code path that calls a paid API cannot
# spend a cent, whatever key or proxy it finds.
#
# The secrets proxy's ports are refused EVEN THOUGH they are on loopback: that
# proxy exists to turn a request carrying a fake key into one carrying the real
# key, which is precisely how "sk-test" bought real images.
#
# A test that genuinely needs the network must say so with
# @pytest.mark.allow_network — and should then not run in `make ci`.
import ipaddress  # noqa: E402
import socket  # noqa: E402

_SECRETS_PROXY_PORTS = frozenset({8785, 8786})  # reverse + forward: both inject real keys
_network_allowed = False


class NetworkBlockedError(ConnectionRefusedError):
    """Raised instead of connecting. A ConnectionRefusedError, so code under test
    handles it exactly as it would an unreachable host."""


def _is_local(host: str, port: int) -> bool:
    if host in ("localhost", ""):
        return port not in _SECRETS_PROXY_PORTS
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False  # an unresolved hostname: not provably local
    return ip.is_loopback and port not in _SECRETS_PROXY_PORTS


def _guard(sock, address):
    if _network_allowed or sock.family == getattr(socket, "AF_UNIX", -1):
        return
    host, port = address[0], address[1]
    if not _is_local(str(host), int(port)):
        raise NetworkBlockedError(
            f"tests may not open network connections (attempted {host}:{port}). "
            "No test may reach a paid API or the credential-injecting proxy. "
            "Mock it, or mark the test @pytest.mark.allow_network."
        )


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _guarded_connect(self, address):
    _guard(self, address)
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    _guard(self, address)
    return _real_connect_ex(self, address)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: this test may open non-loopback network connections"
    )


# Markers whose tests are DELIBERATELY networked (and, for `integration`, paid):
# they only ever run when selected by hand (`pytest -m integration`), because
# pyproject's addopts deselects all three from every default run and from
# `make ci`. Without this, the ban would break the real-LLM integration suite
# the moment someone runs it on purpose.
#
# The lift is process-wide while such a test runs — a background thread leaked
# by an earlier test would get the network too. That window only exists in a
# hand-selected run, never in `make ci`, which is why the deselection matters.
_OPT_IN_NETWORK_MARKERS = ("allow_network", "integration", "e2e_live")


@pytest.fixture(autouse=True)
def _network_ban(request):
    global _network_allowed
    _network_allowed = any(
        request.node.get_closest_marker(m) is not None for m in _OPT_IN_NETWORK_MARKERS
    )
    yield
    _network_allowed = False

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
    # Inbound API-key check off, as shipped: pydantic reads .env itself, so a
    # dev box with PRAX_API_KEY set would otherwise 401 every blueprint test.
    # tests/test_inbound_auth.py sets the key explicitly per test.
    "PRAX_API_KEY": "",
    "DISCORD_ALLOWED_USERS": '{"999000000000000001": "TestUser"}',
    "DISCORD_ALLOWED_CHANNELS": "",
    "DISCORD_TO_PHONE_MAP": "",
}

# --- No real credential ever enters the test process. ---------------------------
#
# pydantic-settings reads the developer's .env itself, so without this every key
# in it is present in the test process — and the network ban above is the only
# thing stopping its use.  Belt AND braces: replace every registered credential
# with its TEST_ENV fake, or "" where there is none, before prax.settings is
# first imported.  An env var outranks .env, so the real values are never read.
#
# This mirrors GitHub CI exactly, which has no .env: all 30 registered
# credentials default to empty except two local ones, left alone here —
# FLASK_SECRET_KEY (TEST_ENV supplies it) and NEO4J_PASSWORD (its "prax-memory"
# default is what the local test Neo4j uses).  Taken from the credential
# registry, whose drift guard already fails CI if a credential is added to
# settings.py without a row — so a new key is scrubbed here automatically.
from prax.services.credential_registry import REGISTRY as _CREDENTIALS  # noqa: E402

_KEEP_LOCAL = {"FLASK_SECRET_KEY", "NEO4J_PASSWORD"}
for _cred in _CREDENTIALS:
    if _cred.env not in _KEEP_LOCAL:
        os.environ[_cred.env] = TEST_ENV.get(_cred.env, "")


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
