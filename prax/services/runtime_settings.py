"""Settings a TeamWork admin can change from the Settings page, without a restart.

Most of Prax is configured in ``.env`` and read once at startup. A few settings
are read every time they are used, so they can change while Prax runs. Those,
and only those, are listed in :data:`REGISTRY`. An admin flips them in
TeamWork; the values go to ``.env-teamwork-override`` beside ``.env`` (not in
git), which wins over ``.env`` and is applied to the live settings object at
once.

Rules:
- **An explicit allowlist.** Only keys in :data:`REGISTRY` are written, and
  only they are honoured when the file is read back. A hand-edited key outside
  the list is ignored and logged.
- **Nothing that loosens protection.** Approvals, hard floors, public exposure,
  network and credentials stay in ``.env`` only, where changing them takes
  shell access, so a web session (or an injected agent driving one) cannot
  switch them off.
- **No agent tool writes here.** Only the TeamWork route does, behind the same
  key as Prax's other TeamWork routes. Who may use it (admins, once TeamWork
  has per-person accounts) is TeamWork's check.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()


@dataclass(frozen=True)
class RuntimeSetting:
    key: str          # the .env name
    field: str        # the attribute on prax.settings.settings
    label: str
    help: str
    category: str


REGISTRY: dict[str, RuntimeSetting] = {s.key: s for s in [
    RuntimeSetting(
        "DESKTOP_SCREENSHOTS_ENABLED", "desktop_screenshots_enabled",
        "Prax can look at the desktop",
        "Screenshots of the sandbox desktop go to the vision model, which is billed "
        "per look. Off: Prax still types into windows and lists them, but can't see "
        "the screen or read a terminal's output.",
        "Desktop",
    ),
    RuntimeSetting(
        "DESKTOP_KERNEL_TOOLS", "desktop_kernel_tools",
        "Prax types into the desktop himself",
        "In the Desktop tab Prax types into your terminal and reads the screen "
        "directly. Off: he hands every desktop task to his desktop helper, which is "
        "slower.",
        "Desktop",
    ),
    RuntimeSetting(
        "ARTIFACTS_ENABLED", "artifacts_enabled",
        "Artifacts",
        "Prax can make pages (plans, tables, small tools) that he keeps updating, "
        "shown in chat. Public links still need your approval each time.",
        "Features",
    ),
    RuntimeSetting(
        "CLAIM_AUDIT_ATTENDED_QUARANTINE", "claim_audit_attended_quarantine",
        "Show self-check warnings in replies",
        "When Prax's self-check flags something in a reply (an unverified figure, a "
        "claim that something is on your screen), add a short note to the reply "
        "itself, not only to the Auditor channel.",
        "Honesty",
    ),
    RuntimeSetting(
        "LOG_HEALTH_ENABLED", "log_health_enabled",
        "Log health in diagnostics",
        "prax_doctor also reads Prax's own log for recent errors and warnings.",
        "Diagnostics",
    ),
]}


def _settings():
    # Read at call time: tests reload prax.settings.
    import prax.settings
    return prax.settings.settings


def overrides_path() -> Path:
    return Path(_settings().teamwork_overrides_path)


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"not a true/false value: {value!r}")


def read_overrides() -> dict[str, bool]:
    """The overrides on disk, allowlisted keys only."""
    path = overrides_path()
    if not path.exists():
        return {}
    from dotenv import dotenv_values
    out: dict[str, bool] = {}
    for key, raw in dotenv_values(path).items():
        if key not in REGISTRY:
            logger.warning("Ignoring %s in %s: not a setting TeamWork may change", key, path)
            continue
        try:
            out[key] = _parse_bool(raw)
        except ValueError:
            logger.warning("Ignoring %s in %s: %r is not true/false", key, path, raw)
    return out


def _env_value(spec: RuntimeSetting) -> bool:
    """What the setting would be without the override: .env, else the default."""
    settings_cls = type(_settings())
    model_field = settings_cls.model_fields[spec.field]
    raw = os.environ.get(spec.key)
    if raw is None:
        try:
            from dotenv import dotenv_values
            env_file = settings_cls.model_config.get("env_file") or ".env"
            raw = dotenv_values(env_file).get(spec.key)
        except Exception:
            raw = None
    if raw is None:
        return bool(model_field.default)
    try:
        return _parse_bool(raw)
    except ValueError:
        return bool(model_field.default)


def apply_overrides() -> dict[str, bool]:
    """Apply the override file to the live settings object. Returns what it set."""
    settings = _settings()
    applied = read_overrides()
    for key, value in applied.items():
        setattr(settings, REGISTRY[key].field, value)
    if applied:
        logger.info("Settings from TeamWork applied: %s", ", ".join(f"{k}={v}" for k, v in applied.items()))
    return applied


def list_settings() -> list[dict]:
    settings = _settings()
    overrides = read_overrides()
    return [{
        "key": spec.key,
        "label": spec.label,
        "help": spec.help,
        "category": spec.category,
        "value": bool(getattr(settings, spec.field)),
        "source": "teamwork" if spec.key in overrides else "env",
        "env_value": _env_value(spec),
    } for spec in REGISTRY.values()]


def _write(overrides: dict[str, bool]) -> None:
    path = overrides_path()
    lines = [
        "# Written by Prax for TeamWork's Settings page. Wins over .env.",
        "# Only settings Prax lists as changeable from TeamWork are honoured;",
        "# see prax/services/runtime_settings.py.",
    ]
    lines += [f"{k}={'true' if v else 'false'}" for k, v in sorted(overrides.items())]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def set_override(key: str, value) -> dict:
    """Set one setting from TeamWork. Raises KeyError / ValueError."""
    if key not in REGISTRY:
        raise KeyError(f"{key} can't be changed from TeamWork")
    parsed = _parse_bool(value)
    with _lock:
        overrides = read_overrides()
        overrides[key] = parsed
        _write(overrides)
        setattr(_settings(), REGISTRY[key].field, parsed)
    logger.info("Setting changed from TeamWork: %s=%s", key, parsed)
    return next(s for s in list_settings() if s["key"] == key)


def clear_override(key: str) -> dict:
    """Go back to the .env value (or the default)."""
    if key not in REGISTRY:
        raise KeyError(f"{key} can't be changed from TeamWork")
    spec = REGISTRY[key]
    with _lock:
        overrides = read_overrides()
        overrides.pop(key, None)
        _write(overrides)
        setattr(_settings(), spec.field, _env_value(spec))
    logger.info("Setting reset to .env from TeamWork: %s", key)
    return next(s for s in list_settings() if s["key"] == key)
