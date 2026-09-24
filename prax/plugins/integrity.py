"""Trust ledger for workspace plugins — code Prax runs must be code Prax wrote.

Workspace plugins (``<workspace>/plugins/``) are imported into the Prax
process (WORKSPACE tier) or run in a host subprocess (IMPORTED tier, and every
``sandbox_test_plugin``). The same directory is mounted read-write into the
sandbox container and is writable through TeamWork's file API, so anything
that can write a file there could otherwise get its code run on the host at
the next ``load_all()`` — a sandbox-to-host escalation.

The ledger closes that path. Each trusted write path — ``plugin_write``,
rollback, ``plugin_import``, ``plugin_import_update``, and the operator's
``scripts/plugin_trust.py`` — records a digest of the plugin's files. With
``WORKSPACE_PLUGIN_INTEGRITY_ENABLED`` the loader refuses any workspace
plugin whose current digest is not the one recorded.

The ledger lives beside the plugin registry, inside the Prax install and
outside every workspace, so the sandbox cannot see or edit it.

Residual (documented, not closed): the check and the import read the files
at different moments. A writer racing that window could swap a file after it
was verified. This bounds the attack to a timed race against a load the
attacker must also trigger, rather than "drop a file and wait".
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_LEDGER_PATH = os.path.join(os.path.dirname(__file__), "trusted_plugins.json")

# Not executed and rewritten by plugin_write on every call; hashing it would
# make an ordinary failed update look like tampering. ``*.prev`` backups are
# skipped too (hot_swap rewrites them on every activation); a restore from one
# is only trusted if its result matches a digest the ledger trusted before.
_UNHASHED_NAMES = frozenset({"readme.md"})
_UNHASHED_DIRS = frozenset({"__pycache__"})
# Past digests kept per plugin, so a rollback can be recognised as genuine.
_HISTORY = 20


def plugin_unit(plugin_file: Path) -> Path:
    """The thing whose contents decide what a plugin does.

    A folder plugin (``<name>/plugin.py``) is its whole folder — sibling
    modules, ``plugin.json`` and ``permissions.md`` (its capability ceiling)
    all change behaviour. A flat plugin (``name.py``) is just that file.
    """
    return plugin_file.parent if plugin_file.name == "plugin.py" else plugin_file


def digest(unit: Path) -> str:
    """SHA-256 over every file in *unit* that can change what the plugin does.

    Symlinks are hashed as their target *string*, never followed: a link's
    meaning is where it points, and following one could read outside the
    plugin.
    """
    h = hashlib.sha256()
    unit = Path(unit)
    if unit.is_symlink() or unit.is_file():
        _feed(h, unit, unit.name)
        return h.hexdigest()
    for root, dirs, files in os.walk(unit, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _UNHASHED_DIRS)
        for name in sorted(files):
            if name.lower() in _UNHASHED_NAMES or name.endswith((".pyc", ".prev")):
                continue
            path = Path(root) / name
            _feed(h, path, path.relative_to(unit).as_posix())
        # Directory symlinks are listed in dirs but not descended into.
        for d in list(dirs):
            p = Path(root) / d
            if p.is_symlink():
                _feed(h, p, p.relative_to(unit).as_posix())
    return h.hexdigest()


def _feed(h, path: Path, rel: str) -> None:
    h.update(rel.encode() + b"\0")
    if path.is_symlink():
        h.update(b"L" + os.readlink(path).encode() + b"\0")
    else:
        h.update(b"F" + path.read_bytes() + b"\0")


class TrustLedger:
    """Absolute plugin unit path -> the digest a trusted path recorded."""

    def __init__(self, path: str = _DEFAULT_LEDGER_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            with open(self._path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # An unreadable ledger trusts nothing (fail closed when enforced).
            logger.warning("Plugin trust ledger at %s is unreadable", self._path, exc_info=True)
            return {}

    def record(self, unit: Path, source: str) -> str:
        key = str(Path(unit).resolve())
        value = digest(Path(unit))
        with self._lock:
            data = self._read()
            prev = data.get(key) or {}
            history = [d for d in prev.get("history", []) if d != value]
            if prev.get("digest") and prev["digest"] != value:
                history.append(prev["digest"])
            data[key] = {
                "digest": value, "source": source, "recorded_at": int(time.time()),
                "history": history[-_HISTORY:],
            }
            tmp = self._path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp, self._path)
        logger.info("Plugin trust recorded for %s (%s)", key, source)
        return value

    def record_if_previously_trusted(self, unit: Path, source: str) -> bool:
        """Re-trust *unit* only if its contents match a digest trusted before.

        For restores from ``.prev`` backups, which sit in the workspace and so
        could have been planted: a genuine rollback reproduces bytes the ledger
        has already seen; a planted backup does not, and stays blocked.
        """
        entry = self._read().get(str(Path(unit).resolve())) or {}
        known = {entry.get("digest"), *entry.get("history", [])} - {None}
        if digest(Path(unit)) not in known:
            logger.warning("Restored plugin %s matches no trusted version; left untrusted", unit)
            return False
        self.record(unit, source)
        return True

    def check(self, unit: Path) -> tuple[bool, str]:
        """``(trusted, reason)`` for the unit's current contents."""
        key = str(Path(unit).resolve())
        entry = self._read().get(key)
        if not entry:
            return False, "not recorded by any Prax plugin tool"
        if entry.get("digest") != digest(Path(unit)):
            return False, "changed since Prax last wrote or approved it"
        return True, ""

    def entries(self) -> dict:
        return self._read()


_ledger: TrustLedger | None = None


def get_ledger() -> TrustLedger:
    global _ledger
    if _ledger is None:
        _ledger = TrustLedger()
    return _ledger


def enforced() -> bool:
    from prax.settings import settings
    return bool(getattr(settings, "workspace_plugin_integrity_enabled", False))


BLOCK_MESSAGE = (
    "blocked — {reason}. Files under the workspace can be written by the sandbox "
    "and TeamWork, so Prax only runs plugin code its own plugin tools produced. "
    "Review it, then approve with: uv run python scripts/plugin_trust.py approve {path}"
)
