"""Declarative plugin permissions — parsed from ``permissions.md``.

Each plugin directory may contain a ``permissions.md`` file that declares
exactly what the plugin is allowed to do.  This file is **authoritative**:
the framework enforces it as the ceiling of the plugin's capabilities.

For IMPORTED plugins, ``permissions.md`` is **required** — if it's missing,
the plugin is blocked from loading.  For BUILTIN and WORKSPACE plugins
it is optional (full access is the default).

Format
------

.. code-block:: markdown

    # Permissions

    ## capabilities
    - llm
    - http
    - commands
    - tts
    - transcription

    ## secrets
    - ELEVENLABS_API_KEY: Authenticate with ElevenLabs API

    ## allowed_commands
    - pdflatex
    - ffmpeg
    - which

Sections
--------

- **capabilities** — which gateway methods the plugin may call.
  Recognized values: ``llm``, ``http``, ``commands``, ``tts``,
  ``transcription``, ``filesystem``.
- **secrets** — environment-variable names the plugin needs access to,
  with a human-readable reason after the colon.
- **allowed_commands** — exact command names (argv[0]) the plugin may
  pass to ``caps.run_command()``.  If this section is present, any
  command not listed is blocked.  If absent and ``commands`` is in
  capabilities, all commands are allowed (not recommended for IMPORTED).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# All recognized capability names.
KNOWN_CAPABILITIES = frozenset({
    "llm",
    "http",
    "commands",
    "tts",
    "transcription",
    "filesystem",
})


@dataclass(frozen=True)
class PluginPermissions:
    """Parsed, immutable permissions from a plugin's ``permissions.md``."""

    capabilities: frozenset[str] = field(default_factory=frozenset)
    secrets: tuple[dict[str, str], ...] = ()  # ({"key": ..., "reason": ...}, ...)
    allowed_commands: frozenset[str] | None = None  # None = no whitelist (all allowed if commands capability granted)

    # Convenience properties
    @property
    def can_use_llm(self) -> bool:
        return "llm" in self.capabilities

    @property
    def can_make_http(self) -> bool:
        return "http" in self.capabilities

    @property
    def can_run_commands(self) -> bool:
        return "commands" in self.capabilities

    @property
    def can_use_tts(self) -> bool:
        return "tts" in self.capabilities

    @property
    def can_transcribe(self) -> bool:
        return "transcription" in self.capabilities

    @property
    def secret_keys(self) -> set[str]:
        return {s["key"] for s in self.secrets}

    def is_command_allowed(self, cmd_name: str) -> bool:
        """Check if a specific command is allowed.

        Returns True if:
        - ``commands`` is in capabilities AND
        - either no whitelist exists (allowed_commands is None) OR
        - the command is in the whitelist
        """
        if not self.can_run_commands:
            return False
        if self.allowed_commands is None:
            return True
        return cmd_name in self.allowed_commands


# Unrestricted permissions for BUILTIN plugins (backward compat).
UNRESTRICTED = PluginPermissions(
    capabilities=KNOWN_CAPABILITIES,
    secrets=(),
    allowed_commands=None,
)

# No permissions at all — used when permissions.md is missing for IMPORTED.
NONE = PluginPermissions()


def parse_permissions_md(text: str) -> PluginPermissions:
    """Parse the content of a ``permissions.md`` file.

    Returns a :class:`PluginPermissions` instance.
    Unrecognized section names are silently ignored.
    """
    capabilities: set[str] = set()
    secrets: list[dict[str, str]] = []
    allowed_commands: list[str] | None = None

    current_section: str | None = None

    for line in text.splitlines():
        stripped = line.strip()

        # Section header: ## capabilities, ## secrets, ## allowed_commands
        m = re.match(r"^##\s+(\w+(?:_\w+)*)", stripped)
        if m:
            current_section = m.group(1).lower()
            if current_section == "allowed_commands":
                allowed_commands = []  # presence means whitelist is active
            continue

        # List item: - value
        m = re.match(r"^[-*]\s+(.+)", stripped)
        if not m:
            continue
        value = m.group(1).strip()

        if current_section == "capabilities":
            cap = value.lower()
            if cap in KNOWN_CAPABILITIES:
                capabilities.add(cap)
            else:
                logger.warning("Unknown capability in permissions.md: %s", cap)

        elif current_section == "secrets":
            # Format: - ENV_KEY: reason text
            # or just: - ENV_KEY
            if ":" in value:
                key, reason = value.split(":", 1)
                secrets.append({"key": key.strip(), "reason": reason.strip()})
            else:
                secrets.append({"key": value.strip(), "reason": ""})

        elif current_section == "allowed_commands":
            # Strip inline comments: - ffmpeg — video assembly
            cmd = value.split("—")[0].split("#")[0].strip()
            # Also strip backticks: - `ffmpeg`
            cmd = cmd.strip("`")
            if cmd and allowed_commands is not None:
                allowed_commands.append(cmd)

    return PluginPermissions(
        capabilities=frozenset(capabilities),
        secrets=tuple(secrets),
        allowed_commands=frozenset(allowed_commands) if allowed_commands is not None else None,
    )


def load_permissions(plugin_dir: str | Path) -> PluginPermissions | None:
    """Load ``permissions.md`` from a plugin directory.

    Returns the parsed permissions, or ``None`` if the file doesn't exist.
    """
    perms_path = Path(plugin_dir) / "permissions.md"
    if not perms_path.is_file():
        return None

    try:
        text = perms_path.read_text(encoding="utf-8")
        perms = parse_permissions_md(text)
        logger.info(
            "Loaded permissions.md from %s: capabilities=%s, secrets=%s, commands=%s",
            plugin_dir,
            sorted(perms.capabilities),
            [s["key"] for s in perms.secrets],
            sorted(perms.allowed_commands) if perms.allowed_commands is not None else "any",
        )
        return perms
    except Exception:
        logger.exception("Failed to parse permissions.md in %s", plugin_dir)
        return None
