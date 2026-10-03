"""Children never inherit Prax's proxy credential (prax/services/child_env.py)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from prax.services import child_env

PRINT_PROXY = [sys.executable, "-c", "import os; print(os.environ.get('HTTPS_PROXY', ''))"]


@pytest.fixture()
def installed(monkeypatch):
    def _install(url: str = ""):
        child_env.install(url)
    yield _install
    child_env.uninstall()


def test_strip_userinfo():
    assert child_env.strip_userinfo("http://prax-prod:s3cret@127.0.0.1:8786") == "http://127.0.0.1:8786"
    assert child_env.strip_userinfo("http://127.0.0.1:8786") == "http://127.0.0.1:8786"
    assert child_env.strip_userinfo("") == ""


def test_a_child_does_not_inherit_the_credential(installed, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://prax-prod:s3cret@127.0.0.1:8786")
    installed()
    out = subprocess.run(PRINT_PROXY, capture_output=True, text=True, check=True).stdout.strip()
    assert out == "http://127.0.0.1:8786"


def test_an_explicit_env_is_cleaned_too(installed):
    installed()
    env = {"HTTPS_PROXY": "http://prax-prod:s3cret@127.0.0.1:8786", "PATH": "/usr/bin:/bin"}
    out = subprocess.run(PRINT_PROXY, env=env, capture_output=True, text=True, check=True).stdout
    assert "s3cret" not in out


def test_children_get_their_own_identity_when_configured(installed, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://prax-prod:s3cret@127.0.0.1:8786")
    installed("http://prax-tools:tools-tok@127.0.0.1:8786")
    out = subprocess.run(PRINT_PROXY, capture_output=True, text=True, check=True).stdout.strip()
    assert out == "http://prax-tools:tools-tok@127.0.0.1:8786"


def test_nothing_changes_without_a_credential(installed, monkeypatch):
    """The flag is on by default because without a credential it is a no-op."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8786")
    monkeypatch.setenv("UNRELATED", "kept")
    installed()
    out = subprocess.run([sys.executable, "-c", "import os; print(os.environ['UNRELATED'], "
                          "os.environ['HTTPS_PROXY'])"], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["kept", "http://127.0.0.1:8786"]


def test_asyncio_subprocesses_are_covered(installed, monkeypatch):
    import asyncio

    monkeypatch.setenv("HTTPS_PROXY", "http://prax-prod:s3cret@127.0.0.1:8786")
    installed()

    async def run():
        proc = await asyncio.create_subprocess_exec(*PRINT_PROXY, stdout=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        return out.decode().strip()

    assert asyncio.run(run()) == "http://127.0.0.1:8786"


def test_install_is_idempotent_and_uninstall_restores(installed):
    original = subprocess.Popen.__init__
    installed()
    wrapped = subprocess.Popen.__init__
    child_env.install()
    assert subprocess.Popen.__init__ is wrapped
    child_env.uninstall()
    assert subprocess.Popen.__init__ is original
