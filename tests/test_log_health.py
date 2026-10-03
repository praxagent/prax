"""Tests for prax/services/log_health.py — counts by call site, keeps no data."""
from __future__ import annotations

import importlib
import logging

import pytest

from prax.services import log_health
from prax.services.log_health import TEMPLATE_MAX_CHARS, LogHealthHandler

SECRET = "user-secret-7f3a"


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


def _record(lineno: int, level: int = logging.WARNING, msg: str = "x %s", args=(1,)):
    return logging.LogRecord("prax.test.synthetic", level, "/srv/prax/mod.py", lineno, msg, args, None)


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
        assert row["template"] == "retrying %s (attempt %d)"
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
        assert [r["location"] for r in handler.summarize(2)] == ["/srv/prax/mod.py:30", "/srv/prax/mod.py:10"]
        assert handler.summarize(0) == []


class TestPrivacy:
    def test_fstring_message_is_not_stored(self, counted):
        logger, handler = counted
        logger.warning(f"fetched page said {SECRET}")

        assert handler.summarize()[0]["template"] is None
        assert SECRET not in _everything_stored(handler)

    def test_template_kept_only_with_args(self, counted):
        logger, handler = counted
        logger.warning("tool failed for %s", SECRET)

        assert handler.summarize()[0]["template"] == "tool failed for %s"
        assert SECRET not in _everything_stored(handler)

    def test_message_without_args_keeps_no_template_even_if_it_looks_static(self, counted):
        logger, handler = counted
        logger.warning("plain words, no args")
        assert handler.summarize()[0]["template"] is None

    def test_exception_text_is_not_stored(self, counted):
        logger, handler = counted
        try:
            raise ValueError(SECRET)
        except ValueError:
            logger.exception("handler crashed in %s", "step")

        assert handler.summarize()[0]["template"] == "handler crashed in %s"
        assert SECRET not in _everything_stored(handler)

    def test_non_string_msg_is_not_stored(self, counted):
        logger, handler = counted
        logger.warning(ValueError(SECRET))
        assert SECRET not in _everything_stored(handler)

    def test_template_is_truncated_and_flattened(self, counted):
        logger, handler = counted
        logger.warning("line one\n" + "y" * 500 + " %s", 1)

        template = handler.summarize()[0]["template"]

        assert len(template) == TEMPLATE_MAX_CHARS
        assert template.endswith("…")
        assert "\n" not in template
        assert template.startswith("line one yyy")


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
            "/srv/prax/mod.py:1": 2, "/srv/prax/mod.py:2": 1,
        }


class TestInstall:
    def test_install_is_idempotent_and_uninstall_detaches(self):
        logger = logging.getLogger("prax.test.log_health.install")
        log_health.uninstall(logger)
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
        # Mirrors tests/test_routes.py: reload settings and app so the env applies.
        monkeypatch.setenv("DATABASE_NAME", str(tmp_path / "test.db"))
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
        monkeypatch.setenv("LOG_HEALTH_ENABLED", "true" if enabled else "false")

        import prax.settings as settings_mod
        importlib.reload(settings_mod)
        import prax.blueprints.twilio_auth as twilio_auth_mod
        monkeypatch.setattr(twilio_auth_mod, "settings", settings_mod.settings)
        import config as config_mod
        importlib.reload(config_mod)
        import prax.helpers_dictionaries as hd
        importlib.reload(hd)
        import app as app_mod
        importlib.reload(app_mod)

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
