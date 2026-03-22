"""Plugin version registry with rollback support.

Tracks active/previous versions of every plugin and provides instant
rollback by swapping version pointers.  State is persisted to a JSON
file so it survives restarts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_DEFAULT_REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "registry.json"
)
_DEFAULT_MAX_FAILURES = 3


class PluginRegistry:
    """Tracks plugin versions, health, and rollback state."""

    def __init__(self, registry_path: str = _DEFAULT_REGISTRY_PATH) -> None:
        self._path = registry_path
        self._lock = threading.Lock()
        self._data: dict = {"plugins": {}, "prompts": {}}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Corrupt registry at %s, starting fresh: %s", self._path, exc)
                self._data = {"plugins": {}, "prompts": {}}

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self._path)

    # ------------------------------------------------------------------
    # Plugin tracking
    # ------------------------------------------------------------------

    def activate_plugin(self, rel_path: str, version: str) -> None:
        """Mark a plugin as active with the given version string."""
        with self._lock:
            entry = self._data["plugins"].get(rel_path, {})
            prev_version = entry.get("active_version")
            entry.update({
                "active_version": version,
                "previous_version": prev_version,
                "activated_at": datetime.now(UTC).isoformat(),
                "status": "active",
                "failure_count": 0,
                "max_failures_before_rollback": entry.get(
                    "max_failures_before_rollback", _DEFAULT_MAX_FAILURES
                ),
            })
            self._data["plugins"][rel_path] = entry
            self._save()
        logger.info("Activated plugin %s version %s (previous: %s)", rel_path, version, prev_version)

    def deactivate_plugin(self, rel_path: str) -> None:
        """Mark a plugin as inactive."""
        with self._lock:
            if rel_path in self._data["plugins"]:
                self._data["plugins"][rel_path]["status"] = "inactive"
                self._save()

    def get_plugin_info(self, rel_path: str) -> dict | None:
        with self._lock:
            return self._data["plugins"].get(rel_path)

    def list_plugins(self) -> dict:
        with self._lock:
            return dict(self._data["plugins"])

    def record_failure(self, rel_path: str) -> int:
        """Record a tool failure. Returns updated failure count."""
        with self._lock:
            entry = self._data["plugins"].get(rel_path)
            if not entry:
                return 0
            entry["failure_count"] = entry.get("failure_count", 0) + 1
            self._save()
            return entry["failure_count"]

    def record_success(self, rel_path: str) -> None:
        """Reset failure count on success."""
        with self._lock:
            entry = self._data["plugins"].get(rel_path)
            if entry and entry.get("failure_count", 0) > 0:
                entry["failure_count"] = 0
                self._save()

    def max_failures(self, rel_path: str) -> int:
        with self._lock:
            entry = self._data["plugins"].get(rel_path, {})
            return entry.get("max_failures_before_rollback", _DEFAULT_MAX_FAILURES)

    def needs_rollback(self, rel_path: str) -> bool:
        with self._lock:
            entry = self._data["plugins"].get(rel_path, {})
            return entry.get("failure_count", 0) >= entry.get(
                "max_failures_before_rollback", _DEFAULT_MAX_FAILURES
            )

    def mark_rolled_back(self, rel_path: str) -> None:
        """Swap active/previous versions after a rollback."""
        with self._lock:
            entry = self._data["plugins"].get(rel_path)
            if not entry:
                return
            entry["active_version"], entry["previous_version"] = (
                entry.get("previous_version"),
                entry.get("active_version"),
            )
            entry["status"] = "rolled_back"
            entry["failure_count"] = 0
            entry["activated_at"] = datetime.now(UTC).isoformat()
            self._save()
        logger.info("Rolled back plugin %s", rel_path)

    # ------------------------------------------------------------------
    # Prompt tracking
    # ------------------------------------------------------------------

    def activate_prompt(self, name: str, content_hash: str) -> None:
        with self._lock:
            entry = self._data["prompts"].get(name, {})
            prev = entry.get("active_hash")
            entry.update({
                "active_hash": content_hash,
                "previous_hash": prev,
                "activated_at": datetime.now(UTC).isoformat(),
            })
            self._data["prompts"][name] = entry
            self._save()

    def get_prompt_info(self, name: str) -> dict | None:
        with self._lock:
            return self._data["prompts"].get(name)

    # ------------------------------------------------------------------
    # Backup / restore helpers
    # ------------------------------------------------------------------

    @staticmethod
    def backup_file(filepath: str) -> str | None:
        """Copy a plugin file to ``<filepath>.prev``. Returns backup path."""
        if not os.path.exists(filepath):
            return None
        backup = filepath + ".prev"
        shutil.copy2(filepath, backup)
        return backup

    @staticmethod
    def restore_file(filepath: str) -> bool:
        """Restore from ``<filepath>.prev`` if it exists."""
        backup = filepath + ".prev"
        if os.path.exists(backup):
            shutil.copy2(backup, filepath)
            return True
        return False

    @staticmethod
    def file_hash(filepath: str) -> str:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
