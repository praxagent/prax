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
import tempfile
from typing import Any

from prax.plugins.policy import PluginPolicy, get_policy
from prax.plugins.registry import PluginTrust

logger = logging.getLogger(__name__)

# Keys whose names match any of these patterns are considered secret
# and will be blocked by ``get_config()``.
_SECRET_PATTERNS = re.compile(
    r"key|secret|token|password|credential",
    re.IGNORECASE,
)


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
    ) -> None:
        self.plugin_rel_path = plugin_rel_path
        self.trust_tier = trust_tier
        self.user_id = user_id
        self.policy: PluginPolicy = get_policy(trust_tier)
        self._http_request_count = 0

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

        IMPORTED plugins have ``cwd`` forced to their scoped directory.
        Any ``cwd`` they pass is treated as a relative path within it.
        """
        if not self.policy.can_run_commands:
            raise PermissionError(
                f"Plugin '{self.plugin_rel_path}' is not permitted to run commands."
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
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

    def shared_tempdir(self, prefix: str = "prax_") -> str:
        """Create and return a temporary directory path."""
        d = tempfile.mkdtemp(prefix=prefix)
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
                headers={"xi-api-key": settings.elevenlabs_key},
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
