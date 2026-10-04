"""deploy/update.sh restarting the services.

On praxvm the deploy runs as the service account, which had no sudo rule, so
the restart failed with a bare sudo error. That left the new code on disk and
the old processes running. Now: with a rule, it restarts TeamWork then Prax;
without one, it stops with exit 2 and says what to run, before any
verification can call the half-deployed box healthy.

Runs the real script against an empty PRAX_ROOT, with sudo/uv/curl/sleep
replaced by stand-ins on PATH. No git, no network, no services touched.
"""
from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "update.sh"


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def deploy(tmp_path):
    root = tmp_path / "PRAX"
    (root / "prax").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "sudo-calls"

    def stub(name, body):
        p = bin_dir / name
        p.write_text("#!/usr/bin/env bash\n" + body)
        p.chmod(0o755)

    stub("uv", "echo synced\n")
    stub("sleep", "exit 0\n")
    stub("curl", "printf 200\n")
    stub("systemctl", "echo active\n")
    stub("sudo", f"""
echo "$*" >> {calls}
[ "$SUDO_RULE" = yes ] && [ "$1" = -n ] && exit 0
echo "Sorry, user $(id -un) is not allowed to execute that." >&2
exit 1
""")

    def run(rule: bool):
        port = str(_closed_port())
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "PRAX_ROOT": str(root),
            "SUDO_RULE": "yes" if rule else "no",
            "SANDBOX_CLIPBOARD_PORT": port, "SANDBOX_VNC_PORT": port, "SANDBOX_CDP_PORT": port,
        }
        proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                              env=env, stdin=subprocess.DEVNULL, timeout=60)
        made = calls.read_text().splitlines() if calls.exists() else []
        return proc, made
    return run


def test_with_a_sudo_rule_it_restarts_teamwork_then_prax(deploy):
    proc, calls = deploy(rule=True)
    assert calls == ["-n systemctl restart teamwork", "-n systemctl restart prax"]
    assert "==> verify" in proc.stdout
    assert "could not restart" not in proc.stdout


def test_without_one_it_stops_and_says_what_to_run(deploy):
    proc, calls = deploy(rule=False)
    assert proc.returncode == 2
    assert calls == ["-n systemctl restart teamwork"]        # no prompt without a terminal
    out = proc.stdout
    assert "OLD processes are still running" in out
    assert "sudo systemctl restart teamwork && sleep 15 && sudo systemctl restart prax" in out
    assert "NOPASSWD:" in out and "restart teamwork" in out
    assert "==> verify" not in out                            # never reports a half deploy healthy
