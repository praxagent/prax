"""Tests for scripts/smoke_test.py — the stack-connectivity smoke test.

Only the pure classification logic is unit-testable keylessly (the rest of
the script needs a live stack); `_prax_scrape_status` decides whether a down
Prometheus scrape target is a real wiring failure (CRITICAL) or the
documented native-shape loopback bind (WARN).

`_load_module` is called inside each test (not at module scope, matching
test_check_layers.py) so the script's top-level env parsing runs at test
time, never at pytest collection time.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "smoke_test.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("smoke_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["smoke_test"] = module
    spec.loader.exec_module(module)
    return module


def _target(url: str, health: str, last_error: str = "", job: str = "prax") -> dict:
    return {
        "labels": {"job": job},
        "scrapeUrl": url,
        "health": health,
        "lastError": last_error,
    }


def test_healthy_target_passes():
    smoke = _load_module()
    ok, _, detail = smoke._prax_scrape_status(
        [_target("http://prax:5001/metrics", "up")], prax_healthy=True
    )
    assert ok is True
    assert "up" in detail


def test_one_up_target_is_enough():
    """The two-target config means the unused shape's target is always down."""
    smoke = _load_module()
    ok, _, _ = smoke._prax_scrape_status(
        [
            _target("http://prax:5001/metrics", "down", "no such host"),
            _target("http://host.docker.internal:5001/metrics", "up"),
        ],
        prax_healthy=True,
    )
    assert ok is True


def test_native_loopback_shape_warns_not_fails():
    """Prax healthy on loopback + host-gateway refused = the documented secure
    default (PRAX_HOST=127.0.0.1) — must be a WARN, not a critical failure."""
    smoke = _load_module()
    ok, critical, detail = smoke._prax_scrape_status(
        [
            _target("http://prax:5001/metrics", "down", "context deadline exceeded"),
            _target(
                "http://host.docker.internal:5001/metrics",
                "down",
                'Get "http://host.docker.internal:5001/metrics": dial tcp '
                "172.17.0.1:5001: connect: connection refused",
            ),
        ],
        prax_healthy=True,
    )
    assert ok is False
    assert critical is False
    assert "loopback default" in detail


def test_prax_down_stays_critical():
    """Refused targets while Prax itself failed /health = real outage."""
    smoke = _load_module()
    ok, critical, _ = smoke._prax_scrape_status(
        [_target("http://host.docker.internal:5001/metrics", "down",
                 "connect: connection refused")],
        prax_healthy=False,
    )
    assert ok is False
    assert critical is True


def test_non_refused_errors_stay_critical():
    """Timeouts/DNS errors with Prax healthy are NOT the loopback shape — e.g.
    a firewalled bridge or broken host-gateway mapping deserves a hard fail."""
    smoke = _load_module()
    ok, critical, _ = smoke._prax_scrape_status(
        [
            _target("http://prax:5001/metrics", "down", "no such host"),
            _target("http://host.docker.internal:5001/metrics", "down",
                    "context deadline exceeded"),
        ],
        prax_healthy=True,
    )
    assert ok is False
    assert critical is True


def test_pre_first_scrape_window_warns_not_fails():
    """health='unknown' + empty lastError on every target = Prometheus hasn't
    scraped yet (it just started) — indeterminate, not a wiring failure."""
    smoke = _load_module()
    ok, critical, detail = smoke._prax_scrape_status(
        [
            _target("http://prax:5001/metrics", "unknown"),
            _target("http://host.docker.internal:5001/metrics", "unknown"),
        ],
        prax_healthy=True,
    )
    assert ok is False
    assert critical is False
    assert "no scrape attempt yet" in detail


def test_missing_or_filtered_job_stays_critical():
    """No prax job at all, and non-prax jobs being up, are both critical:
    the job-label filter must not be satisfied by other jobs' targets."""
    smoke = _load_module()
    for targets in ([], [_target("http://loki:3100/metrics", "up", job="loki")]):
        ok, critical, detail = smoke._prax_scrape_status(targets, prax_healthy=True)
        assert ok is False
        assert critical is True
        assert "no prax scrape job" in detail
