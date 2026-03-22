"""Watchdog supervisor for self-improve safety.

Runs the Flask app as a subprocess and monitors its health.  If the app
crashes after a self-improve deploy, the watchdog automatically rolls back
the offending commit and restarts.  This prevents a bad deploy from
permanently bricking the app.

The watchdog writes rollback info to .self-improve-state.yaml so Prax can
report what happened to the user on the next conversation turn.

Usage:
    python scripts/watchdog.py                  # default: uv run python app.py
    python scripts/watchdog.py -- flask run     # custom command
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HEALTH_URL = "http://localhost:{port}/health"
HEALTH_INTERVAL = 10          # seconds between health checks
STARTUP_GRACE = 30            # seconds to wait before first health check
CRASH_THRESHOLD = 3           # consecutive failed checks before rollback
MAX_PLAIN_RESTARTS = 3        # restarts without a deploy (prevents infinite loops)
STATE_FILE = ".self-improve-state.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(message)s",
)
log = logging.getLogger("watchdog")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    return Path(os.getcwd()) / STATE_FILE


def _read_state() -> dict:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        return yaml.safe_load(p.read_text()) or {}
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    _state_path().write_text(
        yaml.dump(state, default_flow_style=False, sort_keys=False)
    )


def _has_pending_deploy() -> bool:
    """True if the last action was a self-improve deploy."""
    state = _read_state()
    return bool(state.get("pending"))


def _rollback_deploy() -> str | None:
    """Revert the last commit if it's a self-improve deploy.

    Returns the reverted commit message, or None if rollback wasn't possible.
    """
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        capture_output=True, text=True, timeout=10,
    )
    last_msg = result.stdout.strip()

    if "(self-improve)" not in last_msg:
        log.warning("Last commit is not a self-improve deploy: %s", last_msg)
        return None

    result = subprocess.run(
        ["git", "revert", "HEAD", "--no-edit"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        log.error("git revert failed: %s", result.stderr.strip())
        return None

    log.info("Rolled back: %s", last_msg)
    return last_msg


def _record_watchdog_rollback(reverted_msg: str) -> None:
    """Write rollback info so Prax can report it to the user."""
    state = _read_state()
    state["pending"] = False
    state["watchdog_rollback"] = {
        "reverted_commit": reverted_msg,
        "reason": "App crashed after self-improve deploy",
        "rolled_back_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write_state(state)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def _check_health(port: int) -> bool:
    """Return True if the app responds to a health check."""
    import urllib.error
    import urllib.request

    url = HEALTH_URL.format(port=port)
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _get_port() -> int:
    return int(os.environ.get("PORT", "5001"))


def _start_app(cmd: list[str]) -> subprocess.Popen:
    log.info("Starting app: %s", " ".join(cmd))
    return subprocess.Popen(cmd)


def main() -> None:
    # Parse optional custom command after --.
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        app_cmd = sys.argv[idx + 1:]
    else:
        app_cmd = ["uv", "run", "python", "app.py"]

    port = _get_port()
    plain_restarts = 0

    proc = _start_app(app_cmd)

    # Forward SIGTERM to the child so Docker stop works cleanly.
    def _forward_signal(signum, _frame):
        if proc.poll() is None:
            proc.send_signal(signum)

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)

    # Wait for initial startup.
    log.info("Waiting %ds for app startup...", STARTUP_GRACE)
    deadline = time.monotonic() + STARTUP_GRACE
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break  # Process already exited — handle below.
        if _check_health(port):
            log.info("App is healthy.")
            break
        time.sleep(2)

    consecutive_failures = 0

    while True:
        # Check if process is still running.
        exit_code = proc.poll()
        app_running = exit_code is None

        if app_running:
            if _check_health(port):
                consecutive_failures = 0
                plain_restarts = 0  # Reset — app is healthy.
                time.sleep(HEALTH_INTERVAL)
                continue
            else:
                consecutive_failures += 1
                log.warning(
                    "Health check failed (%d/%d)",
                    consecutive_failures, CRASH_THRESHOLD,
                )
                if consecutive_failures < CRASH_THRESHOLD:
                    time.sleep(HEALTH_INTERVAL)
                    continue
                # Threshold reached — kill the hung process.
                log.error("App unresponsive, killing process.")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        else:
            # Exit code 0 means clean shutdown (e.g. Werkzeug reloader
            # restarting after a file change).  Just restart immediately
            # without counting against the restart limit.
            if exit_code == 0:
                log.info("App exited cleanly (code 0), restarting.")
                consecutive_failures = 0
                time.sleep(1)
                proc = _start_app(app_cmd)
                log.info("Waiting %ds for restart...", STARTUP_GRACE)
                deadline = time.monotonic() + STARTUP_GRACE
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        break
                    if _check_health(port):
                        log.info("App is healthy.")
                        break
                    time.sleep(2)
                continue

            log.error("App crashed with code %s", exit_code)

        # --- App is down (crash or unresponsive). Decide rollback or restart. ---

        if _has_pending_deploy():
            log.info("Pending self-improve deploy detected — attempting rollback.")
            reverted = _rollback_deploy()
            if reverted:
                _record_watchdog_rollback(reverted)
                log.info("Rollback complete. Restarting app.")
                plain_restarts = 0
            else:
                log.warning("Rollback failed — restarting anyway.")
        else:
            plain_restarts += 1
            if plain_restarts > MAX_PLAIN_RESTARTS:
                log.error(
                    "Max restarts (%d) reached without a deploy to rollback. Giving up.",
                    MAX_PLAIN_RESTARTS,
                )
                sys.exit(1)
            log.info("No pending deploy. Plain restart %d/%d.",
                     plain_restarts, MAX_PLAIN_RESTARTS)

        consecutive_failures = 0
        time.sleep(2)  # Brief pause before restart.
        proc = _start_app(app_cmd)

        # Grace period for the restarted app.
        log.info("Waiting %ds for restart...", STARTUP_GRACE)
        time.sleep(STARTUP_GRACE)


if __name__ == "__main__":
    main()
