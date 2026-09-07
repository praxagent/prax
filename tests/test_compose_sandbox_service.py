"""Static hygiene checks for the ``sandbox`` service in the repo-root compose files.

The sandbox is a pure execution environment: the coding-agent CLIs, and the
OpenCode server on :4096 they talked to, are gone from the image.  The compose
residue they left behind was not merely stale — it was the defect:

* a compose-level healthcheck curling ``:4096`` could never pass, so
  ``depends_on: sandbox: condition: service_healthy`` blocked ``prax`` forever;
* ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` were injected into the one
  container whose job is to run untrusted code (credential-exfiltration surface);
* the repo was bind-mounted read-write at ``/source`` (and ``/root`` persisted
  from the host), giving the sandbox write access to the harness's own code.

These tests pin the fixed shape for every ``docker-compose*.yml`` at the repo
root that defines a ``sandbox`` service.  Keyless and static — YAML only, no
docker daemon needed.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = sorted(REPO_ROOT.glob("docker-compose*.yml"))

# Env-key suffixes that mean "credential".  ``_API_KEY`` is covered by ``_KEY``;
# ``_TOKEN`` / ``_SECRET`` / ``_PASSWORD`` mirror the repo's pre-commit sweep.
_SECRET_SUFFIXES = ("_KEY", "_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")

# Grandfathered out-of-workspace mounts, per file.  ``docker-compose.dev.yml`` is
# an override that still bind-mounts the harness source into the sandbox at
# ``/source/...`` (a leftover of the removed coding agents' "self-improvement"
# path).  Fixing it is tracked separately; this is a RATCHET — a file may only
# shrink its entry (the test passes once the mounts are gone) and must never
# add to it.  Every other file must have an empty set.
_GRANDFATHERED_MOUNTS: dict[str, set[str]] = {
    "docker-compose.dev.yml": {"/source/prax", "/source/app.py", "/source/config.py", "/source/tests"},
}

_INTERP = re.compile(r"\$\{[^}]*\}")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _services(path: Path) -> dict:
    return _load(path).get("services") or {}


def _container_path(volume) -> str:
    """Container-side path of a compose volume entry (short or long syntax).

    ``${VAR:-default}`` interpolations contain ``:`` and are blanked before the
    short-syntax split so the host half can't swallow the container half.
    """
    if isinstance(volume, dict):
        return str(volume.get("target", ""))
    parts = _INTERP.sub("X", str(volume)).split(":")
    # "host:container[:mode]" -> container; a bare "container" is an anonymous volume.
    return parts[1] if len(parts) >= 2 else parts[0]


def _env_keys(environment) -> list[str]:
    if isinstance(environment, dict):
        return [str(k) for k in environment]
    return [str(e).split("=", 1)[0] for e in (environment or [])]


def _healthcheck_test_string(service: dict) -> str:
    test = (service.get("healthcheck") or {}).get("test", "")
    return " ".join(test) if isinstance(test, list) else str(test)


def _params(with_sandbox_only: bool):
    out = []
    for p in COMPOSE_FILES:
        if with_sandbox_only and "sandbox" not in _services(p):
            continue
        out.append(pytest.param(p, id=p.name))
    return out


def test_compose_files_present():
    """The globbed set must include the two the README Quick Start uses — a
    silently empty parametrisation would make every test below vacuous."""
    names = {p.name for p in COMPOSE_FILES}
    assert {"docker-compose.yml", "docker-compose.lite.yml"} <= names


@pytest.mark.parametrize("path", _params(with_sandbox_only=True))
def test_sandbox_healthcheck_is_not_the_dead_opencode_probe(path: Path):
    """No sandbox healthcheck may probe :4096 / OpenCode's /global/health —
    nothing listens there, so such a check never passes and ``service_healthy``
    dependents never start.  (Absence of a compose-level healthcheck is fine:
    the image's own HEALTHCHECK applies — see the dependency test below.)"""
    test = _healthcheck_test_string(_services(path)["sandbox"])
    assert "4096" not in test, f"{path.name}: sandbox healthcheck still probes :4096: {test!r}"
    assert "/global/health" not in test, f"{path.name}: sandbox healthcheck still probes OpenCode: {test!r}"


@pytest.mark.parametrize("path", _params(with_sandbox_only=True))
def test_sandbox_environment_carries_no_credentials(path: Path):
    """No environment key on the sandbox may end in a credential suffix
    (``_KEY``, ``_API_KEY``, ``_TOKEN``, ``_SECRET``, ``_PASSWORD``).  The
    sandbox runs untrusted code; a key in its env is an exfiltration target."""
    keys = _env_keys(_services(path)["sandbox"].get("environment"))
    leaked = [k for k in keys if k.upper().endswith(_SECRET_SUFFIXES)]
    assert not leaked, f"{path.name}: credential-shaped env passed into the sandbox: {leaked}"


@pytest.mark.parametrize("path", _params(with_sandbox_only=True))
def test_sandbox_mounts_only_the_workspace(path: Path):
    """Every sandbox volume's container path must be ``/workspace`` or under it.
    ``/source`` (the repo), ``/root`` and ``/root/.<cli>`` (persisted agent
    homes) are exactly the mounts the removed coding agents needed."""
    mounts = [_container_path(v) for v in (_services(path)["sandbox"].get("volumes") or [])]
    outside = {m for m in mounts if not (m == "/workspace" or m.startswith("/workspace/"))}
    allowed = _GRANDFATHERED_MOUNTS.get(path.name, set())
    assert outside <= allowed, (
        f"{path.name}: sandbox mounts outside /workspace: {sorted(outside - allowed)}"
    )


@pytest.mark.parametrize("path", _params(with_sandbox_only=False))
def test_service_healthy_dependencies_have_a_health_source(path: Path):
    """A ``condition: service_healthy`` dependency on a service with no health
    source waits forever.  The source is either a compose-level ``healthcheck``
    or a ``HEALTHCHECK`` instruction in the Dockerfile the service builds from
    (the sandbox's case — its build context is the sibling prax-sandbox repo,
    which CI checks out because it is also a uv path dependency)."""
    services = _services(path)
    for name, svc in services.items():
        deps = svc.get("depends_on") or {}
        if not isinstance(deps, dict):
            continue  # short list form carries no condition
        for target, spec in deps.items():
            if (spec or {}).get("condition") != "service_healthy":
                continue
            if target not in services:
                continue  # override fragment; the base file defines the target
            tsvc = services[target]
            if _healthcheck_test_string(tsvc):
                continue  # compose-level healthcheck present
            build = tsvc.get("build")
            assert build, f"{path.name}: {name} waits on {target}, which has no healthcheck and no build"
            ctx = build if isinstance(build, str) else build.get("context", ".")
            dockerfile = (build.get("dockerfile", "Dockerfile") if isinstance(build, dict) else "Dockerfile")
            # Resolve ${PRAX_SANDBOX_PATH:-../prax-sandbox} the way compose would
            # with the variable unset — the default is the documented layout.
            ctx = re.sub(r"\$\{[^}:]*:-([^}]*)\}", r"\1", ctx)
            df = (REPO_ROOT / ctx / dockerfile).resolve()
            assert df.is_file(), f"{path.name}: {target}'s Dockerfile not found at {df}"
            assert re.search(r"^\s*HEALTHCHECK\b", df.read_text(), re.M), (
                f"{path.name}: {name} waits on {target} (service_healthy) but neither the compose "
                f"file nor {df} defines a healthcheck"
            )
