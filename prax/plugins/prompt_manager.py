"""Manages versioned system prompts loaded from plugins/prompts/.

Prompts are plain text or markdown files with ``{{VARIABLE}}`` placeholders
that are expanded at load time.  The registry tracks content hashes so
prompts can be rolled back instantly.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

from prax.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class PromptManager:
    """Load, update, and rollback system prompts."""

    def __init__(self, registry: PluginRegistry | None = None) -> None:
        self.registry = registry or PluginRegistry()

    def load(self, name: str, variables: dict[str, str] | None = None) -> str:
        """Load a prompt file, expanding ``{{KEY}}`` placeholders.

        Falls back to empty string if the file doesn't exist.
        """
        path = _PROMPTS_DIR / name
        if not path.exists():
            logger.warning("Prompt file not found: %s", path)
            return ""
        text = path.read_text()
        for key, value in (variables or {}).items():
            text = text.replace(f"{{{{{key}}}}}", value)
        return text

    def read(self, name: str) -> str:
        """Read raw prompt content (without variable expansion)."""
        path = _PROMPTS_DIR / name
        if not path.exists():
            return f"Prompt not found: {name}"
        return path.read_text()

    def write(self, name: str, content: str) -> dict:
        """Write a prompt file and register its hash.

        A backup of the previous version is saved for rollback.
        """
        path = _PROMPTS_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)

        # Backup existing.
        if path.exists():
            backup = str(path) + ".prev"
            shutil.copy2(str(path), backup)

        path.write_text(content)

        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        self.registry.activate_prompt(name, content_hash)

        logger.info("Updated prompt %s (hash: %s)", name, content_hash)
        return {"status": "updated", "name": name, "hash": content_hash}

    def rollback(self, name: str) -> dict:
        """Restore the previous version of a prompt."""
        path = _PROMPTS_DIR / name
        backup = str(path) + ".prev"

        if not os.path.exists(backup):
            return {"error": f"No backup found for prompt '{name}'"}

        shutil.copy2(backup, str(path))

        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        self.registry.activate_prompt(name, content_hash)

        logger.info("Rolled back prompt %s (hash: %s)", name, content_hash)
        return {"status": "rolled_back", "name": name, "hash": content_hash}

    def list_prompts(self) -> list[dict]:
        """List all prompt files with their registry info."""
        results = []
        for p in sorted(_PROMPTS_DIR.glob("*")):
            if p.name.startswith(".") or p.name.endswith(".prev"):
                continue
            if p.is_file():
                info = self.registry.get_prompt_info(p.name) or {}
                results.append({
                    "name": p.name,
                    "size": p.stat().st_size,
                    "hash": info.get("active_hash", "untracked"),
                    "previous_hash": info.get("previous_hash"),
                })
        return results


# Module-level singleton
_manager: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    global _manager
    if _manager is None:
        _manager = PromptManager()
    return _manager
