"""Configuration for the secrets proxy — the ONLY place the real keys live."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Upstream:
    """One proxied provider: where to forward, and how to inject its credential.

    The real key is read from the proxy process's env (``key_env``) at request
    time — it is never stored on this object, never logged, and never returned to
    the caller.
    """
    name: str
    base_url: str          # e.g. https://api.openai.com
    key_env: str           # env var in the PROXY's process holding the real key
    auth_scheme: str       # "bearer" (Authorization: Bearer) | "x-api-key"
    extra_headers: dict[str, str] = field(default_factory=dict)

    def real_key(self) -> str | None:
        return os.environ.get(self.key_env) or None

    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]:
        """Return *headers* with any inbound auth stripped and the real key set.

        Strips whatever placeholder auth the client sent (never trust it) and
        injects the real credential for this upstream.
        """
        # Drop client-supplied auth (case-insensitive) — the proxy owns auth.
        cleaned = {k: v for k, v in headers.items()
                   if k.lower() not in ("authorization", "x-api-key")}
        key = self.real_key()
        if key:
            if self.auth_scheme == "bearer":
                cleaned["Authorization"] = f"Bearer {key}"
            elif self.auth_scheme == "x-api-key":
                cleaned["x-api-key"] = key
        cleaned.update(self.extra_headers)
        return cleaned


def _default_upstreams() -> dict[str, Upstream]:
    # OpenAI-compatible: the base URL may point at OpenAI or a third-party
    # (OpenRouter/DeepSeek/…) — PROXY_OPENAI_BASE_URL overrides. The key env is
    # the proxy's own OPENAI_KEY (NOT Prax's — Prax has only a placeholder).
    openai_base = os.environ.get("PROXY_OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
    anthropic_base = os.environ.get("PROXY_ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    return {
        "openai": Upstream("openai", openai_base, "OPENAI_KEY", "bearer"),
        "anthropic": Upstream(
            "anthropic", anthropic_base, "ANTHROPIC_KEY", "x-api-key",
            extra_headers={"anthropic-version": os.environ.get(
                "PROXY_ANTHROPIC_VERSION", "2023-06-01")}),
    }


@dataclass
class ProxyConfig:
    """Runtime config. Real keys are pulled from env by the Upstreams, not held here."""
    host: str = "127.0.0.1"
    port: int = 8785
    upstreams: dict[str, Upstream] = field(default_factory=_default_upstreams)
    # Forward-timeout (seconds) for the whole upstream request/stream.
    timeout: float = 600.0
    # Audit log: one line per proxied call (method/provider/path/status/bytes —
    # NEVER the key or body). Empty = log to the module logger only.
    audit_log_path: str | None = field(
        default_factory=lambda: os.environ.get("PROXY_AUDIT_LOG") or None)

    @classmethod
    def from_env(cls) -> ProxyConfig:
        return cls(
            host=os.environ.get("PROXY_HOST", "127.0.0.1"),
            port=int(os.environ.get("PROXY_PORT", "8785")),
            timeout=float(os.environ.get("PROXY_TIMEOUT_S", "600")),
        )
