"""Capabilities gateway — the official SDK surface for plugins.

Plugins receive a :class:`PluginCapabilities` instance at registration
time.  Instead of importing ``prax.settings`` or reading ``os.environ``
directly, they call methods on this object.  The gateway handles
credentials internally, enforces per-tier policy, and logs access.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

from prax.plugins.permissions import PluginPermissions
from prax.plugins.policy import PluginPolicy, get_policy
from prax.plugins.registry import PluginTrust

logger = logging.getLogger(__name__)

# Keys whose names match any of these patterns are considered secret
# and will be blocked by ``get_config()``.
_SECRET_PATTERNS = re.compile(
    r"key|secret|token|password|credential",
    re.IGNORECASE,
)


def _resolve_settings_attr(env_key: str) -> str | None:
    """Map an environment-variable name to the corresponding AppSettings attribute.

    Uses the Pydantic field aliases defined on :class:`~prax.settings.AppSettings`.
    For example, ``ELEVENLABS_API_KEY`` → ``elevenlabs_api_key``.
    """
    from prax.settings import AppSettings
    for field_name, field_info in AppSettings.model_fields.items():
        alias = field_info.alias
        if alias and alias == env_key:
            return field_name
    return None


class PluginCapabilities:
    """Narrow, audited API surface that plugins use to access Prax services.

    The framework constructs one instance per plugin, scoped to that
    plugin's trust tier and workspace context.
    """

    def __init__(
        self,
        plugin_rel_path: str,
        trust_tier: str,
        user_id: str | None = None,
        approved_secrets: set[str] | None = None,
        permissions: PluginPermissions | None = None,
    ) -> None:
        self.plugin_rel_path = plugin_rel_path
        self.trust_tier = trust_tier
        self.user_id = user_id
        self.policy: PluginPolicy = get_policy(trust_tier)
        self._http_request_count = 0
        self._approved_secrets: set[str] = approved_secrets or set()

        # Declarative permissions from permissions.md — authoritative ceiling.
        self._permissions: PluginPermissions | None = permissions

    # ------------------------------------------------------------------
    # Internal — permissions.md enforcement
    # ------------------------------------------------------------------

    def _check_permission(self, capability: str) -> None:
        """Raise if *capability* is not declared in permissions.md.

        For BUILTIN plugins, permissions.md is optional (all allowed).
        For IMPORTED plugins, permissions.md is the authoritative ceiling.
        """
        if self._permissions is None:
            return  # No permissions.md — use tier policy only (backward compat)
        if capability not in self._permissions.capabilities:
            raise PermissionError(
                f"Plugin '{self.plugin_rel_path}' does not declare '{capability}' "
                f"in its permissions.md — blocked."
            )

    # ------------------------------------------------------------------
    # Internal — scoped data directory
    # ------------------------------------------------------------------

    def _plugin_data_root(self) -> str:
        """Return the scoped data directory for this plugin.

        IMPORTED plugins: ``{workspace}/{user}/plugin_data/{plugin_rel_path}/``
        BUILTIN/WORKSPACE: ``{workspace}/{user}/active/``
        """
        from prax.services.workspace_service import workspace_root
        if self.user_id is None:
            raise RuntimeError("No user context — cannot resolve plugin data path.")
        root = workspace_root(self.user_id)
        if self.trust_tier == PluginTrust.IMPORTED:
            return os.path.join(root, "plugin_data", self.plugin_rel_path)
        return os.path.join(root, "active")

    # ------------------------------------------------------------------
    # LLM — plugin never sees API key
    # ------------------------------------------------------------------

    def build_llm(self, tier: str = "medium") -> Any:
        """Return a LangChain LLM without exposing API keys to the plugin."""
        self._check_permission("llm")
        if not self.policy.can_use_llm:
            raise PermissionError(
                f"Plugin '{self.plugin_rel_path}' (tier={self.trust_tier}) "
                "is not permitted to use LLM services."
            )
        from prax.agent.llm_factory import build_llm
        logger.info(
            "Plugin %s requesting LLM (tier=%s)", self.plugin_rel_path, tier,
        )
        return build_llm(tier=tier)

    # ------------------------------------------------------------------
    # HTTP — audited, rate-limited
    # ------------------------------------------------------------------

    def _check_http(self) -> None:
        self._check_permission("http")
        if not self.policy.can_make_http:
            raise PermissionError(
                f"Plugin '{self.plugin_rel_path}' is not permitted to make HTTP requests."
            )
        if self._http_request_count >= self.policy.max_http_requests_per_invocation:
            raise PermissionError(
                f"Plugin '{self.plugin_rel_path}' exceeded HTTP request limit "
                f"({self.policy.max_http_requests_per_invocation})."
            )
        self._http_request_count += 1

    def http_get(self, url: str, **kwargs: Any) -> Any:
        """Audited HTTP GET. Returns a ``requests.Response``."""
        self._check_http()
        import requests
        logger.info("Plugin %s HTTP GET %s", self.plugin_rel_path, url)
        return requests.get(url, timeout=kwargs.pop("timeout", 30), **kwargs)

    def http_post(self, url: str, **kwargs: Any) -> Any:
        """Audited HTTP POST. Returns a ``requests.Response``."""
        self._check_http()
        import requests
        logger.info("Plugin %s HTTP POST %s", self.plugin_rel_path, url)
        return requests.post(url, timeout=kwargs.pop("timeout", 30), **kwargs)

    # ------------------------------------------------------------------
    # Workspace files — scoped to user's workspace
    # ------------------------------------------------------------------

    def save_file(self, filename: str, content: bytes) -> str:
        """Save a file to the plugin's scoped directory. Returns the saved path.

        IMPORTED plugins write to ``plugin_data/{plugin_rel_path}/``.
        BUILTIN/WORKSPACE plugins write to ``active/`` (existing behaviour).
        """
        if self.user_id is None:
            raise RuntimeError("No user context — cannot save workspace files.")
        logger.info(
            "Plugin %s saving file %s for user %s",
            self.plugin_rel_path, filename, self.user_id,
        )
        if self.trust_tier != PluginTrust.IMPORTED:
            from prax.services.workspace_service import save_file
            return save_file(self.user_id, filename, content)

        from prax.services.workspace_service import safe_join
        data_root = self._plugin_data_root()
        os.makedirs(data_root, exist_ok=True)
        filepath = safe_join(data_root, filename)
        with open(filepath, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        return filepath

    def read_file(self, filename: str) -> str:
        """Read a file from the plugin's scoped directory.

        IMPORTED plugins can only read from their own scoped directory.
        BUILTIN/WORKSPACE plugins read from the user's ``active/`` directory.
        """
        if self.user_id is None:
            raise RuntimeError("No user context — cannot read workspace files.")
        logger.info(
            "Plugin %s reading file %s for user %s",
            self.plugin_rel_path, filename, self.user_id,
        )
        if self.trust_tier != PluginTrust.IMPORTED:
            from prax.services.workspace_service import read_file
            return read_file(self.user_id, filename)

        from prax.services.workspace_service import safe_join
        data_root = self._plugin_data_root()
        filepath = safe_join(data_root, filename)
        with open(filepath, encoding="utf-8") as f:
            return f.read()

    def workspace_path(self, *parts: str) -> str:
        """Return an absolute path within the plugin's scoped directory.

        IMPORTED plugins get a path under ``plugin_data/{plugin_rel_path}/``.
        BUILTIN/WORKSPACE plugins get the full workspace root.
        """
        if self.user_id is None:
            raise RuntimeError("No user context — cannot resolve workspace path.")
        if self.trust_tier == PluginTrust.IMPORTED:
            data_root = self._plugin_data_root()
            os.makedirs(data_root, exist_ok=True)
            if parts:
                from prax.services.workspace_service import safe_join
                return safe_join(data_root, *parts)
            return data_root
        from prax.services.workspace_service import workspace_root
        return os.path.join(workspace_root(self.user_id), *parts)

    def get_user_id(self) -> str | None:
        """Return the current user's ID, if available."""
        return self.user_id

    # ------------------------------------------------------------------
    # Shell — routed through safe_run with command whitelist
    # ------------------------------------------------------------------

    def run_command(
        self,
        cmd: list[str],
        *,
        timeout: int = 30,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a shell command. Audited and time-limited.

        Commands are routed through the sandbox-aware shell utilities so
        that system packages like pdflatex, ffmpeg, and pdftoppm are
        available even when the host container doesn't have them.

        IMPORTED plugins have ``cwd`` forced to their scoped directory.
        Any ``cwd`` they pass is treated as a relative path within it.
        """
        self._check_permission("commands")
        if not self.policy.can_run_commands:
            raise PermissionError(
                f"Plugin '{self.plugin_rel_path}' is not permitted to run commands."
            )
        # Enforce command whitelist from permissions.md.
        if self._permissions and self._permissions.allowed_commands is not None:
            cmd_name = cmd[0] if cmd else ""
            if not self._permissions.is_command_allowed(cmd_name):
                raise PermissionError(
                    f"Plugin '{self.plugin_rel_path}' is not allowed to run '{cmd_name}'. "
                    f"Allowed commands: {sorted(self._permissions.allowed_commands)}"
                )
        if self.trust_tier == PluginTrust.IMPORTED and self.user_id:
            from prax.services.workspace_service import safe_join
            forced_cwd = self._plugin_data_root()
            os.makedirs(forced_cwd, exist_ok=True)
            if cwd is not None:
                forced_cwd = safe_join(forced_cwd, cwd)
            cwd = forced_cwd
        logger.info(
            "Plugin %s running command: %s (cwd=%s)", self.plugin_rel_path, cmd, cwd,
        )
        from prax.utils.shell import run_command as sandbox_run
        return sandbox_run(cmd, cwd=cwd, timeout=timeout)

    def shared_tempdir(self, prefix: str = "prax_") -> str:
        """Create and return a temporary directory accessible from both app and sandbox."""
        from prax.utils.shell import shared_tempdir as sandbox_tempdir
        d = sandbox_tempdir(prefix=prefix)
        logger.info("Plugin %s created tempdir %s", self.plugin_rel_path, d)
        return d

    # ------------------------------------------------------------------
    # TTS — framework handles API key
    # ------------------------------------------------------------------

    def tts_synthesize(
        self,
        text: str,
        output_path: str,
        voice: str = "nova",
        provider: str = "openai",
    ) -> str:
        """Synthesize speech to *output_path* without exposing API keys.

        Returns the output path on success.
        """
        self._check_permission("tts")
        logger.info(
            "Plugin %s TTS request (%s/%s, %d chars)",
            self.plugin_rel_path, provider, voice, len(text),
        )
        if provider == "openai":
            from openai import OpenAI

            from prax.settings import settings
            client = OpenAI(api_key=settings.openai_key)
            response = client.audio.speech.create(
                model="tts-1", voice=voice, input=text,
            )
            response.stream_to_file(output_path)
        elif provider == "elevenlabs":
            import requests

            from prax.settings import settings
            resp = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                headers={"xi-api-key": settings.elevenlabs_api_key},
                json={"text": text, "model_id": "eleven_monolingual_v1"},
                timeout=60,
            )
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(resp.content)
        else:
            raise ValueError(f"Unsupported TTS provider: {provider}")
        return output_path

    # ------------------------------------------------------------------
    # Audio transcription — framework handles API key
    # ------------------------------------------------------------------

    def transcribe_audio(self, audio_path: str) -> str:
        """Transcribe an audio file using OpenAI Whisper without exposing API keys.

        Returns the transcript text.  The audio file must be < 25 MB
        (OpenAI Whisper API limit).
        """
        self._check_permission("transcription")
        logger.info(
            "Plugin %s transcription request (%s)",
            self.plugin_rel_path, audio_path,
        )
        file_size = os.path.getsize(audio_path)
        if file_size > 25 * 1024 * 1024:
            raise ValueError(
                f"Audio file is {file_size / (1024*1024):.1f} MB — "
                f"exceeds the 25 MB Whisper API limit. "
                f"Split the file or compress it first."
            )

        from openai import OpenAI

        from prax.settings import settings
        client = OpenAI(api_key=settings.openai_key)
        with open(audio_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
            )
        return transcript.text

    # ------------------------------------------------------------------
    # Approved secrets — explicit permission grants
    # ------------------------------------------------------------------

    def get_approved_secret(self, env_key: str) -> str | None:
        """Read a secret that the plugin has been explicitly approved to use.

        *env_key* is the environment-variable name (e.g. ``ELEVENLABS_API_KEY``).
        The method checks:
          1. BUILTIN plugins — always allowed.
          2. WORKSPACE plugins — auto-approved.
          3. IMPORTED plugins — only if *env_key* is in ``_approved_secrets``.

        The secret value is read from :data:`prax.settings.settings` using
        the Pydantic alias→attribute mapping.  The raw value is never stored
        in the registry; only the approval flag is persisted.
        """
        if self.trust_tier == PluginTrust.BUILTIN:
            pass  # Always allowed
        elif self.trust_tier == PluginTrust.WORKSPACE:
            pass  # Auto-approved
        else:
            # IMPORTED — require explicit approval.
            if env_key not in self._approved_secrets:
                logger.warning(
                    "Plugin %s requested unapproved secret '%s' — blocked",
                    self.plugin_rel_path, env_key,
                )
                raise PermissionError(
                    f"Plugin '{self.plugin_rel_path}' has not been approved "
                    f"to use secret '{env_key}'. Approve it in plugin settings."
                )

        # Resolve env var alias → settings attribute name.
        attr_name = _resolve_settings_attr(env_key)
        if attr_name is None:
            logger.warning(
                "Plugin %s requested unknown secret '%s' — no matching setting",
                self.plugin_rel_path, env_key,
            )
            return None

        from prax.settings import settings
        val = getattr(settings, attr_name, None)
        if val is None:
            return None
        logger.info(
            "Plugin %s accessed approved secret '%s'",
            self.plugin_rel_path, env_key,
        )
        return str(val)

    # ------------------------------------------------------------------
    # Config — non-secret settings only
    # ------------------------------------------------------------------

    def get_config(self, key: str) -> str | None:
        """Read a non-secret configuration value.

        Blocks any key whose name contains: key, secret, token,
        password, or credential.
        """
        if _SECRET_PATTERNS.search(key):
            logger.warning(
                "Plugin %s attempted to read secret config key '%s' — blocked",
                self.plugin_rel_path, key,
            )
            raise PermissionError(
                f"Plugin '{self.plugin_rel_path}' cannot access secret config key '{key}'."
            )
        if not self.policy.can_access_settings:
            # Non-BUILTIN plugins can only read explicitly safe keys.
            from prax.settings import settings
            val = getattr(settings, key, None)
            if val is None:
                return None
            return str(val)

        # BUILTIN plugins — unrestricted access.
        from prax.settings import settings
        val = getattr(settings, key, None)
        return str(val) if val is not None else None
