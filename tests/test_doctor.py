"""Tests for prax/agent/doctor.py — prax_doctor must report what is true.

Every setting a check reads is pinned here with monkeypatch: conftest scrubs
credentials, but non-credential values (TEAMWORK_URL, model names, tiers) still
come from a developer's .env, and a doctor test must not depend on them.
"""
from __future__ import annotations

import logging
import socket
import time

import pytest

from prax.agent import doctor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def settings():
    """The live settings object.  conftest reloads prax.settings for every
    test, so a module-level import would patch a stale copy."""
    import prax.settings as settings_mod

    return settings_mod.settings


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 400


def _requests_get(monkeypatch, *, status: int = 200, exc: Exception | None = None) -> list[str]:
    import requests

    calls: list[str] = []

    def fake_get(url, timeout=None, **kwargs):
        calls.append(url)
        if exc is not None:
            raise exc
        return _Resp(status)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def _pin_tiers(monkeypatch, settings, tiers: str = "low,medium") -> None:
    monkeypatch.setattr(settings, "enabled_tiers", tiers)
    for name in ("low", "medium", "high", "pro"):
        monkeypatch.setattr(settings, f"{name}_enabled_legacy", None)
    monkeypatch.setattr(settings, "low_model", "model-low")
    monkeypatch.setattr(settings, "medium_model", "model-medium")
    monkeypatch.setattr(settings, "high_model", "model-high")
    monkeypatch.setattr(settings, "pro_model", "model-pro")


@pytest.fixture
def openai_settings(monkeypatch, settings):
    """A keyless-but-valid OpenAI configuration held ONLY in settings."""
    for var in ("OPENAI_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "default_llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_key", "sk-test")
    monkeypatch.setattr(settings, "openai_base_url", None)
    _pin_tiers(monkeypatch, settings)
    from prax.agent.circuit_breaker import get_breaker
    get_breaker("llm:openai").reset()
    yield
    get_breaker("llm:openai").reset()


# ---------------------------------------------------------------------------
# TeamWork — settings, not os.environ
# ---------------------------------------------------------------------------


class TestTeamWork:
    def test_url_held_only_in_settings_is_seen(self, monkeypatch, settings):
        # The original bug: pydantic reads .env without exporting it, so an
        # os.environ lookup reported "not configured" on a configured box.
        monkeypatch.delenv("TEAMWORK_URL", raising=False)
        monkeypatch.setattr(settings, "teamwork_url", "http://tw.test:8000/")
        monkeypatch.setattr(settings, "teamwork_enabled_legacy", None)
        calls = _requests_get(monkeypatch)

        out = doctor._check_teamwork()

        assert out == "[OK] TeamWork: connected at http://tw.test:8000"
        assert calls == ["http://tw.test:8000/health"]

    def test_empty_url_is_standalone_and_never_probed(self, monkeypatch, settings):
        monkeypatch.setenv("TEAMWORK_URL", "http://environ-only.test")  # must be ignored
        monkeypatch.setattr(settings, "teamwork_url", "")
        monkeypatch.setattr(settings, "teamwork_enabled_legacy", None)
        calls = _requests_get(monkeypatch)

        out = doctor._check_teamwork()

        assert out.startswith("[OK] TeamWork: not configured")
        assert "TEAMWORK_URL is empty" in out
        assert calls == []

    def test_legacy_false_overriding_a_set_url_is_a_warning(self, monkeypatch, settings):
        monkeypatch.setattr(settings, "teamwork_url", "http://tw.test:8000")
        monkeypatch.setattr(settings, "teamwork_enabled_legacy", False)
        calls = _requests_get(monkeypatch)

        out = doctor._check_teamwork()

        assert out.startswith("[WARN] TeamWork: off")
        assert "TEAMWORK_ENABLED=false" in out
        assert calls == []

    def test_unreachable_is_a_warning(self, monkeypatch, settings):
        import requests

        monkeypatch.setattr(settings, "teamwork_url", "http://tw.test:8000")
        monkeypatch.setattr(settings, "teamwork_enabled_legacy", None)
        _requests_get(monkeypatch, exc=requests.ConnectionError("refused"))

        out = doctor._check_teamwork()

        assert out.startswith("[WARN] TeamWork: http://tw.test:8000 configured but unreachable")

    def test_unhealthy_status_is_a_warning(self, monkeypatch, settings):
        monkeypatch.setattr(settings, "teamwork_url", "http://tw.test:8000")
        monkeypatch.setattr(settings, "teamwork_enabled_legacy", None)
        _requests_get(monkeypatch, status=503)

        assert doctor._check_teamwork() == "[WARN] TeamWork: http://tw.test:8000/health returned 503"


# ---------------------------------------------------------------------------
# LLM — every enabled tier through the real factory
# ---------------------------------------------------------------------------


class TestLLM:
    def test_key_held_only_in_settings_passes(self, openai_settings):
        # No OPENAI_KEY in os.environ — the old check FAILed here.
        out = doctor._check_llm()
        assert out == "[OK] LLM: openai, tiers: low=model-low, medium=model-medium"

    def test_missing_key_fails_with_the_factory_error(self, openai_settings, monkeypatch, settings):
        monkeypatch.setattr(settings, "openai_key", "")

        out = doctor._check_llm()

        assert out.startswith("[FAIL] LLM: openai — cannot build")
        assert "low (model-low): ValueError: OPENAI_KEY is required" in out
        assert "medium (model-medium): ValueError: OPENAI_KEY is required" in out

    def test_builds_exactly_the_enabled_tiers(self, monkeypatch, settings):
        _pin_tiers(monkeypatch, settings, "medium,pro")
        import prax.agent.llm_factory as llm_factory

        built: list[str] = []
        monkeypatch.setattr(llm_factory, "build_llm", lambda tier=None, **kw: built.append(tier))

        out = doctor._check_llm()

        assert built == ["medium", "pro"]
        assert out.endswith("tiers: medium=model-medium, pro=model-pro")

    def test_one_failing_tier_fails_the_check(self, monkeypatch, settings):
        _pin_tiers(monkeypatch, settings, "low,high")
        import prax.agent.llm_factory as llm_factory

        def fake_build(tier=None, **kw):
            if tier == "high":
                raise ConnectionError("Circuit breaker OPEN for LLM provider 'openai'")

        monkeypatch.setattr(llm_factory, "build_llm", fake_build)

        out = doctor._check_llm()

        assert out.startswith("[FAIL] LLM:")
        assert "high (model-high): ConnectionError: Circuit breaker OPEN" in out
        assert "low (" not in out

    def test_construction_makes_no_network_call(self, openai_settings, monkeypatch):
        attempts: list[tuple] = []

        def refuse(*args, **kwargs):
            attempts.append(args[1:] or args)
            raise ConnectionRefusedError("doctor construction must not touch the network")

        monkeypatch.setattr(socket.socket, "connect", lambda self, addr: refuse(self, addr))
        monkeypatch.setattr(socket.socket, "connect_ex", lambda self, addr: refuse(self, addr))
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: refuse(None, *a))
        monkeypatch.setattr(socket, "create_connection", lambda *a, **k: refuse(None, *a))

        out = doctor._check_llm()

        assert out.startswith("[OK] LLM:")
        assert attempts == []
        # The recorder is live: a real request would have shown up above.
        import httpx
        with pytest.raises(httpx.ConnectError):
            httpx.get("http://api.openai.invalid/v1/models")
        assert attempts

    def test_probe_builds_are_not_recorded_as_tier_choices(self, openai_settings):
        from prax.agent.llm_factory import peek_tier_choices

        before = len(peek_tier_choices())
        doctor._check_llm()
        assert len(peek_tier_choices()) == before


# ---------------------------------------------------------------------------
# Plugins — the loader's real health API
# ---------------------------------------------------------------------------


def _loader(tmp_path, *, tool_map: dict[str, str], load_errors: dict[str, str] | None = None):
    from prax.plugins.loader import PluginLoader
    from prax.plugins.registry import PluginRegistry

    loader = PluginLoader(registry=PluginRegistry(str(tmp_path / "registry.json")))
    loader._tool_to_plugin = dict(tool_map)
    loader._load_errors = dict(load_errors or {})
    for rel in set(tool_map.values()):
        loader.registry.activate_plugin(rel, "1")
    return loader


class TestPlugins:
    def test_counts_loaded_plugins_and_tools(self, tmp_path, monkeypatch):
        import prax.plugins.loader as loader_mod

        loader = _loader(tmp_path, tool_map={"wx_now": "custom/weather", "wx_week": "custom/weather",
                                             "news_get": "news"})
        monkeypatch.setattr(loader_mod, "get_plugin_loader", lambda: loader)

        assert doctor._check_plugins() == "[OK] Plugins: 2 plugin(s), 3 tool(s) loaded"

    def test_load_errors_and_failing_plugins_warn(self, tmp_path, monkeypatch):
        import prax.plugins.loader as loader_mod

        loader = _loader(
            tmp_path,
            tool_map={"wx_now": "custom/weather", "news_get": "news"},
            load_errors={"shared/evil": "blocked — unacknowledged security warnings"},
        )
        loader.registry.record_failure("news")
        loader.registry.record_failure("news")
        monkeypatch.setattr(loader_mod, "get_plugin_loader", lambda: loader)

        out = doctor._check_plugins()

        assert out.startswith("[WARN] Plugins: 2 plugin(s), 2 tool(s) loaded; needs attention:")
        assert "shared/evil: not loaded — blocked — unacknowledged security warnings" in out
        assert "news: 2 tool failure(s) since its last success (auto-rollback at 3)" in out
        assert "custom/weather:" not in out

    def test_a_plugin_whose_import_raises_is_reported(self, tmp_path, monkeypatch):
        import textwrap

        import prax.plugins.loader as loader_mod
        from prax.plugins.loader import PluginLoader
        from prax.plugins.registry import PluginRegistry

        custom = tmp_path / "tools" / "custom"
        custom.mkdir(parents=True)
        (custom / "hello.py").write_text(textwrap.dedent("""\
            from langchain_core.tools import tool

            PLUGIN_VERSION = "1"

            @tool
            def hello_world(name: str) -> str:
                \"\"\"Say hello.\"\"\"
                return f"Hello, {name}!"

            def register():
                return [hello_world]
        """))
        (custom / "broken.py").write_text('PLUGIN_VERSION = "1"\nraise RuntimeError("exploded at import")\n')
        monkeypatch.setattr(loader_mod, "_PLUGINS_ROOT", tmp_path / "tools")
        loader = PluginLoader(registry=PluginRegistry(str(tmp_path / "registry.json")))
        loader.load_all()
        monkeypatch.setattr(loader_mod, "get_plugin_loader", lambda: loader)

        out = doctor._check_plugins()

        assert out.startswith("[WARN] Plugins: 1 plugin(s), 1 tool(s) loaded; needs attention:")
        assert "failed to load — RuntimeError: exploded at import" in out
        # A crash is not a refusal: plugin_list's "Blocked plugins" stays clean.
        assert loader.get_load_errors() == {}

    def test_a_success_clears_the_warning(self, tmp_path):
        loader = _loader(tmp_path, tool_map={"news_get": "news"})
        loader.registry.record_failure("news")
        loader.record_tool_success("news_get")
        assert loader.health_report().problems == []

    def test_system_status_uses_the_same_report(self, tmp_path, monkeypatch, settings):
        import prax.agent.tool_registry as tool_registry
        import prax.plugins.loader as loader_mod
        from prax.agent.workspace_tools import system_status

        loader = _loader(
            tmp_path,
            tool_map={"wx_now": "custom/weather", "news_get": "news"},
            load_errors={"shared/evil": "blocked"},
        )
        monkeypatch.setattr(loader_mod, "get_plugin_loader", lambda: loader)
        monkeypatch.setattr(tool_registry, "get_registered_tools", lambda: [object()] * 7)
        monkeypatch.setattr(settings, "log_path", str(tmp_path / "absent.log"))

        out = system_status.invoke({})

        assert "**Tools:** 7 total" in out
        assert "**Plugins:** 2 loaded (custom/weather, news)" in out
        assert "**Plugin tools:** 2" in out
        assert "**Plugins needing attention:**" in out
        assert "  - shared/evil: not loaded — blocked" in out
        assert "Error gathering status" not in out


# ---------------------------------------------------------------------------
# Settings — FLASK_SECRET_KEY from settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_secret_comes_from_settings(self, monkeypatch, settings):
        monkeypatch.setattr(settings, "agent_max_tool_calls", 50)
        monkeypatch.setattr(settings, "flask_secret_key", "x" * 32)
        monkeypatch.setenv("FLASK_SECRET_KEY", "")  # what os.environ said is irrelevant

        assert doctor._check_settings().startswith("[OK] Settings:")

    @pytest.mark.parametrize("secret", ["change-me", "short", ""])
    def test_weak_secret_warns(self, monkeypatch, secret, settings):
        monkeypatch.setattr(settings, "agent_max_tool_calls", 50)
        monkeypatch.setattr(settings, "flask_secret_key", secret)
        monkeypatch.setenv("FLASK_SECRET_KEY", "y" * 40)

        out = doctor._check_settings()

        assert out == "[WARN] Settings: FLASK_SECRET_KEY is weak or a placeholder"


# ---------------------------------------------------------------------------
# Health monitor — a fresh verdict, not None for the first nine turns
# ---------------------------------------------------------------------------


class TestHealthMonitor:
    @pytest.fixture(autouse=True)
    def _monitor(self, monkeypatch, settings):
        import prax.agent.health_monitor as mon

        monkeypatch.setattr(settings, "health_monitor_enabled", True)
        monkeypatch.setattr(mon, "_last_check", None)
        return mon

    def test_runs_a_check_when_none_exists(self, _monitor, monkeypatch):
        from prax.agent.health_monitor import HealthCheck

        degraded = HealthCheck(overall="degraded", alerts=["3/10 tool calls failed (30% error rate)"])
        monkeypatch.setattr(_monitor, "run_health_check", lambda: degraded)

        out = doctor._check_health_monitor()

        assert out.startswith("[WARN] Health Monitor: degraded")
        assert "3/10 tool calls failed" in out
        assert _monitor.get_last_check() is degraded

    def test_reuses_a_fresh_check(self, _monitor, monkeypatch):
        from prax.agent.health_monitor import HealthCheck

        fresh = HealthCheck(timestamp=time.time() - 30, overall="unhealthy", alerts=["4 LLM errors"])
        monkeypatch.setattr(_monitor, "_last_check", fresh)
        monkeypatch.setattr(_monitor, "run_health_check", lambda: pytest.fail("re-ran a fresh check"))

        out = doctor._check_health_monitor()

        assert out.startswith("[FAIL] Health Monitor: unhealthy (checked 30s ago)")

    def test_disabled(self, monkeypatch, settings):
        monkeypatch.setattr(settings, "health_monitor_enabled", False)
        assert doctor._check_health_monitor() == (
            "[OK] Health Monitor: disabled (HEALTH_MONITOR_ENABLED=false)"
        )


# ---------------------------------------------------------------------------
# Log health — in-process counts, no file reads
# ---------------------------------------------------------------------------


@pytest.fixture
def counted_logger():
    """A private logger with the log-health counter installed on it."""
    from prax.services import log_health

    logger = logging.getLogger("prax.test.doctor_log_health")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    log_health.uninstall(logger)
    log_health.install(logger)
    yield logger
    log_health.uninstall(logger)
    logger.propagate = True


class TestLogHealth:
    def test_disabled_says_how_to_enable(self, monkeypatch, settings):
        monkeypatch.setattr(settings, "log_health_enabled", False)
        out = doctor._check_log_health()
        assert out.startswith("[OK] Log health: off")
        assert "LOG_HEALTH_ENABLED=true" in out

    def test_enabled_but_not_installed_warns(self, monkeypatch, settings):
        from prax.services import log_health

        monkeypatch.setattr(settings, "log_health_enabled", True)
        monkeypatch.setattr(log_health, "_handler", None)
        assert doctor._check_log_health().startswith("[WARN] Log health: LOG_HEALTH_ENABLED is true")

    def test_recurring_warning_is_listed_without_its_data(self, counted_logger, monkeypatch, settings):
        monkeypatch.setattr(settings, "log_health_enabled", True)
        monkeypatch.setattr(settings, "log_health_warn_count", 5)
        monkeypatch.setattr(settings, "log_health_top_n", 10)

        for i in range(6):
            counted_logger.warning("fetch failed for %s", f"private-url-{i}")
        counted_logger.error(f"user said private-text {7}")

        out = doctor._check_log_health()

        lines = out.splitlines()
        assert lines[0].startswith("[WARN] Log health: a call site has logged 5+ warnings/errors since ")
        assert "(top 2 of 2 site(s))" in lines[0]
        assert lines[1].startswith("    6 × WARNING tests/test_doctor.py:")
        assert lines[1].endswith("— fetch failed for %s")
        assert lines[2].startswith("    1 × ERROR tests/test_doctor.py:")
        assert lines[2].endswith("— (message not kept)")
        assert "private" not in out

    def test_below_threshold_is_ok(self, counted_logger, monkeypatch, settings):
        monkeypatch.setattr(settings, "log_health_enabled", True)
        monkeypatch.setattr(settings, "log_health_warn_count", 5)
        counted_logger.warning("slow call %s", 1)

        out = doctor._check_log_health()

        assert out.startswith("[OK] Log health: 1 warning/error call site(s) since ")
        assert out.endswith("none has logged 5+ times")

    def test_reads_no_file(self, counted_logger, monkeypatch, settings):
        import builtins
        import io

        monkeypatch.setattr(settings, "log_health_enabled", True)
        monkeypatch.setattr(settings, "log_health_warn_count", 1)
        counted_logger.warning("thing %s", 1)

        def no_open(*args, **kwargs):
            raise AssertionError(f"log health opened a file: {args[:1]}")

        monkeypatch.setattr(builtins, "open", no_open)
        monkeypatch.setattr(io, "open", no_open)

        out = doctor._check_log_health()

        # The check's own except would turn a refused open() into a WARN too,
        # so assert on the listing that only the success path produces.
        assert "1 × WARNING tests/test_doctor.py:" in out
        assert "opened a file" not in out


# ---------------------------------------------------------------------------
# The whole report
# ---------------------------------------------------------------------------


class TestReport:
    def test_header_counts_and_no_spoke_check(self, monkeypatch):
        results = {
            "_check_llm": "[OK] LLM: x",
            "_check_sandbox": "[OK] Sandbox: x",
            "_check_plugins": "[WARN] Plugins: x",
            "_check_workspace": "[OK] Workspace: x",
            "_check_teamwork": "[OK] TeamWork: x",
            "_check_scheduler": "[WARN] Scheduler: x",
            "_check_settings": "[OK] Settings: x",
            "_check_health_monitor": "[FAIL] Health Monitor: x",
            "_check_log_health": "[WARN] Log health: x\n    3 × WARNING a.py:1 — t",
        }
        for name, value in results.items():
            monkeypatch.setattr(doctor, name, lambda value=value: value)

        out = doctor.prax_doctor.invoke({})

        assert out.splitlines()[0] == "Prax Doctor -- 5 healthy, 3 warnings, 1 errors"
        assert "Spokes" not in out
        assert not hasattr(doctor, "_check_spokes")
