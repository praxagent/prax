"""Tests for prax/services/log_health.py — counts by call site, keeps no data."""
from __future__ import annotations

import importlib
import logging
import os
import sys

import pytest

from prax.services import log_health
from prax.services.log_health import _REPO_ROOT, TEMPLATE_MAX_CHARS, LogHealthHandler

SECRET = "user-secret-7f3a"
PRAX_MODULE = os.path.join(_REPO_ROOT, "prax", "services", "example.py")


@pytest.fixture
def counted():
    """A private logger with its own counter (never the root logger)."""
    logger = logging.getLogger("prax.test.log_health")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = LogHealthHandler()
    logger.addHandler(handler)
    yield logger, handler
    logger.removeHandler(handler)
    logger.propagate = True


def _record(
    lineno: int, level: int = logging.WARNING, msg: str = "x %s", args=(1,),
    pathname: str = "/srv/prax/mod.py", exc_info=None,
):
    return logging.LogRecord("prax.test.synthetic", level, pathname, lineno, msg, args, exc_info)


def _prax_record(msg: str, args=(1,), lineno: int = 1, exc_info=None):
    """A record as if logged from a module in this repo's prax/ package."""
    return _record(lineno, msg=msg, args=args, pathname=PRAX_MODULE, exc_info=exc_info)


def _log_at_one_site(logger: logging.Logger, level: int, msg: str, *args) -> None:
    logger.log(level, msg, *args)


def _everything_stored(handler: LogHealthHandler) -> str:
    return repr(handler._groups) + repr(handler.summarize(1000))


class TestGrouping:
    def test_same_site_is_one_group(self, counted):
        logger, handler = counted
        for i in range(3):
            logger.warning("retrying %s (attempt %d)", "job", i)

        rows = handler.summarize()

        assert len(rows) == 1
        row = rows[0]
        assert row["count"] == 3
        assert row["level"] == "WARNING"
        assert row["logger"] == "prax.test.log_health"
        assert row["location"].startswith("tests/test_log_health.py:")
        assert row["template"] is None  # tests/ is not Prax's own code
        assert row["first_seen"] <= row["last_seen"]

    def test_level_logger_and_line_each_split_groups(self, counted):
        logger, handler = counted
        _log_at_one_site(logger, logging.WARNING, "a %s", 1)
        _log_at_one_site(logger, logging.ERROR, "a %s", 1)  # same line, other level
        logger.warning("b %s", 1)  # other line
        logging.getLogger("prax.test.log_health.child").warning("c %s", 1)  # other logger

        assert handler.group_count() == 4

    def test_below_warning_is_ignored(self, counted):
        logger, handler = counted
        logger.info("chatty %s", 1)
        logger.debug("chattier %s", 1)
        assert handler.summarize() == []

    def test_summarize_orders_by_count_and_honours_top_n(self):
        handler = LogHealthHandler()
        for lineno, n in ((10, 5), (20, 2), (30, 9)):
            for _ in range(n):
                handler.handle(_record(lineno))

        assert [r["count"] for r in handler.summarize()] == [9, 5, 2]
        assert [r["location"] for r in handler.summarize(2)] == ["<external>/mod.py:30", "<external>/mod.py:10"]
        assert handler.summarize(0) == []


class TestPrivacy:
    def test_fstring_message_is_not_stored(self, counted):
        logger, handler = counted
        logger.warning(f"fetched page said {SECRET}")

        assert handler.summarize()[0]["template"] is None
        assert SECRET not in _everything_stored(handler)

    def test_template_kept_only_with_args(self):
        handler = LogHealthHandler()
        handler.handle(_prax_record("tool failed for %s", args=(SECRET,)))

        assert handler.summarize()[0]["template"] == "tool failed for %s"
        assert SECRET not in _everything_stored(handler)

    def test_message_without_args_keeps_no_template_even_if_it_looks_static(self):
        handler = LogHealthHandler()
        handler.handle(_prax_record("plain words, no args", args=()))
        assert handler.summarize()[0]["template"] is None

    def test_exception_text_is_not_stored(self):
        handler = LogHealthHandler()
        try:
            raise ValueError(SECRET)
        except ValueError:
            handler.handle(_prax_record(
                "handler crashed in %s", args=("step",), exc_info=sys.exc_info(),
            ))

        assert handler.summarize()[0]["template"] == "handler crashed in %s"
        assert SECRET not in _everything_stored(handler)

    def test_exception_through_a_real_logger_is_not_stored(self, counted):
        logger, handler = counted
        try:
            raise ValueError(SECRET)
        except ValueError:
            logger.exception("handler crashed in %s", "step")

        assert SECRET not in _everything_stored(handler)

    def test_non_string_msg_is_not_stored(self, counted):
        logger, handler = counted
        logger.warning(ValueError(SECRET))
        assert SECRET not in _everything_stored(handler)

    def test_template_is_truncated_and_flattened(self):
        handler = LogHealthHandler()
        handler.handle(_prax_record("line one\n" + "y" * 500 + " %s"))

        template = handler.summarize()[0]["template"]

        assert len(template) == TEMPLATE_MAX_CHARS
        assert template.endswith("…")
        assert "\n" not in template
        assert template.startswith("line one yyy")


class TestOnlyPraxTemplates:
    """A library's template is not a string Prax wrote: werkzeug's has the
    client IP and a timestamp baked into it for every request line."""

    WERKZEUG_MSG = '100.64.0.7 - - [02/Oct/2026 09:15:02] "%s" %s %s'

    @pytest.mark.parametrize("pathname", [
        "/usr/lib/python3.13/site-packages/werkzeug/_internal.py",
        # The in-repo .venv is under the repo root but is not Prax's code.
        os.path.join(_REPO_ROOT, ".venv", "lib", "python3.13", "site-packages", "werkzeug", "_internal.py"),
    ])
    def test_library_templates_are_not_kept(self, pathname):
        handler = LogHealthHandler()
        handler.handle(_record(97, msg=self.WERKZEUG_MSG, args=("GET / HTTP/1.1", 200, "-"), pathname=pathname))

        row = handler.summarize()[0]
        assert row["template"] is None
        assert row["location"] == "werkzeug/_internal.py:97"
        assert "100.64.0.7" not in _everything_stored(handler)

    @pytest.mark.parametrize("pathname", [
        PRAX_MODULE,
        os.path.join(_REPO_ROOT, "app.py"),
    ])
    def test_prax_templates_are_kept(self, pathname):
        handler = LogHealthHandler()
        handler.handle(_record(5, msg="retrying %s", args=("job",), pathname=pathname))
        assert handler.summarize()[0]["template"] == "retrying %s"

    @pytest.mark.parametrize("pathname", [
        os.path.join(_REPO_ROOT, "scripts", "tool.py"),
        os.path.join(_REPO_ROOT, "tests", "test_x.py"),
        "/srv/elsewhere/prax/services/example.py",
    ])
    def test_other_call_sites_keep_no_template(self, pathname):
        handler = LogHealthHandler()
        handler.handle(_record(5, msg="retrying %s", args=("job",), pathname=pathname))
        assert handler.summarize()[0]["template"] is None


class TestLocations:
    """A location never names a user and never leaks a stranger's path."""

    @pytest.fixture
    def workspace(self, tmp_path, monkeypatch):
        import prax.settings as settings_mod

        ws = tmp_path / "workspaces"
        monkeypatch.setattr(settings_mod.settings, "workspace_dir", str(ws))
        return ws

    @pytest.mark.parametrize("user", ["usr_3f9a2b1c", "12678093704"])
    def test_a_workspace_path_drops_the_user_directory(self, workspace, user):
        pathname = str(workspace / user / "plugins" / "x" / "plugin.py")

        short = log_health._short_path(pathname)

        assert short == "<workspace>/plugins/x/plugin.py"
        assert user not in short

    def test_a_relative_workspace_dir_is_resolved(self, tmp_path, monkeypatch):
        import prax.settings as settings_mod

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(settings_mod.settings, "workspace_dir", "workspaces")
        pathname = str(tmp_path / "workspaces" / "usr_1" / "plugins" / "p.py")

        assert log_health._short_path(pathname) == "<workspace>/plugins/p.py"

    def test_a_workspace_inside_the_checkout_still_drops_the_user(self, monkeypatch):
        import prax.settings as settings_mod

        monkeypatch.setattr(settings_mod.settings, "workspace_dir", os.path.join(_REPO_ROOT, "workspaces"))
        pathname = os.path.join(_REPO_ROOT, "workspaces", "usr_1", "plugins", "p.py")

        assert log_health._short_path(pathname) == "<workspace>/plugins/p.py"

    def test_site_packages_wins_even_inside_the_repo(self, workspace):
        pathname = os.path.join(_REPO_ROOT, ".venv", "lib", "python3.13", "site-packages", "httpx", "_client.py")
        assert log_health._short_path(pathname) == "httpx/_client.py"

    def test_prax_code_is_repo_relative(self, workspace):
        assert log_health._short_path(PRAX_MODULE) == "prax/services/example.py"

    def test_any_other_path_keeps_only_its_basename(self, workspace):
        assert log_health._short_path("/home/someone/private-project/tool.py") == "<external>/tool.py"

    def test_a_row_uses_the_short_location(self, workspace):
        handler = LogHealthHandler()
        handler.handle(_record(3, pathname=str(workspace / "usr_9" / "plugins" / "p.py")))
        assert handler.summarize()[0]["location"] == "<workspace>/plugins/p.py:3"
        assert "usr_9" not in repr(handler.summarize())


class TestBound:
    def test_new_sites_past_the_cap_are_counted_as_overflow(self):
        handler = LogHealthHandler(max_groups=3)
        for lineno in range(1, 6):
            handler.handle(_record(lineno))

        assert handler.group_count() == 3
        assert handler.overflow == 2

    def test_existing_sites_keep_counting_at_the_cap(self):
        handler = LogHealthHandler(max_groups=2)
        handler.handle(_record(1))
        handler.handle(_record(2))
        handler.handle(_record(3))  # overflow
        handler.handle(_record(1))

        assert handler.overflow == 1
        assert {r["location"]: r["count"] for r in handler.summarize()} == {
            "<external>/mod.py:1": 2, "<external>/mod.py:2": 1,
        }


class TestInstall:
    @pytest.fixture(autouse=True)
    def _clean(self):
        log_health.uninstall()
        yield
        log_health.uninstall()

    def test_install_is_idempotent_and_uninstall_detaches(self):
        logger = logging.getLogger("prax.test.log_health.install")
        try:
            first = log_health.install(logger)
            second = log_health.install(logger)

            assert first is second is log_health.get_handler()
            assert logger.handlers.count(first) == 1
            logger.propagate = False
            logger.warning("installed %s", 1)
            assert log_health.summarize()[0]["count"] == 1
        finally:
            log_health.uninstall(logger)
            logger.propagate = True

        assert log_health.get_handler() is None
        assert first not in logger.handlers
        assert log_health.summarize() == []

    def test_uninstall_without_a_target_detaches_from_where_it_was_installed(self):
        logger = logging.getLogger("prax.test.log_health.where")
        handler = log_health.install(logger)

        log_health.uninstall()

        assert handler not in logger.handlers
        assert handler not in logging.getLogger().handlers
        assert log_health.get_handler() is None

    def test_uninstall_of_another_logger_keeps_the_installed_handler(self):
        installed_on = logging.getLogger("prax.test.log_health.a")
        other = logging.getLogger("prax.test.log_health.b")
        handler = log_health.install(installed_on)

        log_health.uninstall(other)

        assert log_health.get_handler() is handler
        assert handler in installed_on.handlers

        log_health.uninstall()
        assert handler not in installed_on.handlers
        assert log_health.get_handler() is None

    def test_installing_elsewhere_moves_the_handler(self):
        first = logging.getLogger("prax.test.log_health.first")
        second = logging.getLogger("prax.test.log_health.second")
        handler = log_health.install(first)

        assert log_health.install(second) is handler
        assert handler not in first.handlers
        assert handler in second.handlers

        log_health.uninstall()
        assert handler not in second.handlers

    def test_a_failing_record_never_breaks_logging(self, counted):
        logger, handler = counted

        class Exploding:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        handler.emit(Exploding())  # must not raise
        logger.warning("still counting %s", 1)
        assert handler.summarize()[0]["count"] == 1


class TestAppStartup:
    """The counter goes on the root logger at startup, and only behind the flag."""

    @pytest.mark.parametrize("enabled", [False, True])
    def test_create_app_installs_only_when_enabled(self, monkeypatch, tmp_path, enabled):
        # Import app BEFORE reloading settings, and patch rather than reload
        # it: a reloaded `app` keeps the flag-on settings after this test, and
        # every later create_app() would install a handler nobody removes.
        import app as app_mod
        import prax.settings as settings_mod

        monkeypatch.setenv("DATABASE_NAME", str(tmp_path / "test.db"))
        monkeypatch.setenv("LOG_PATH", str(tmp_path / "app.log"))
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
        # create_app waits ~30 s for a configured TeamWork that is not running.
        monkeypatch.setenv("TEAMWORK_URL", "")
        monkeypatch.setenv("LOG_HEALTH_ENABLED", "true" if enabled else "false")
        importlib.reload(settings_mod)
        monkeypatch.setattr(app_mod, "settings", settings_mod.settings)

        log_health.uninstall()
        try:
            app_mod.create_app()
            handler = log_health.get_handler()
            if enabled:
                assert handler is not None
                assert handler in logging.getLogger().handlers
            else:
                assert handler is None
        finally:
            log_health.uninstall()
        assert all(
            not isinstance(h, LogHealthHandler) for h in logging.getLogger().handlers
        )
