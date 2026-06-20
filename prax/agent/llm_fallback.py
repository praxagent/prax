"""Cross-provider LLM failover.

When the primary LLM provider keeps failing (rate-limited, overloaded,
connection-dropped, or its circuit breaker is OPEN), surfacing the error to
the user is the worst outcome — the harness already has a unified
:func:`prax.agent.llm_factory.build_llm` interface that can speak to a second
provider just as easily.  This module decides *when* a failure is worth
failing over and *which* provider to try next.

The behaviour is opt-in: it only engages when ``settings.llm_fallback_enabled``
is set.  With it off, single-provider deployments behave exactly as before.

Chain selection:
- If ``settings.llm_fallback_chain`` is set, it is parsed verbatim (an ordered
  ``provider[:model]`` list).
- Otherwise the chain is auto-derived from whichever providers have credentials
  configured, excluding the primary, in a stable preference order.
"""
from __future__ import annotations

import logging

from prax.settings import settings

logger = logging.getLogger(__name__)

# Substrings that mark an exception as a *provider-side* failure worth retrying
# on a different provider.  Tool errors, validation errors, and logic bugs are
# deliberately excluded — a second provider won't fix those.
_PROVIDER_ERROR_MARKERS = (
    "rate limit",
    "ratelimit",
    "overloaded",
    "capacity",
    "service unavailable",
    "serviceunavailable",
    "timeout",
    "timed out",
    "connection",
    "econnreset",
    "bad gateway",
    "gateway timeout",
    "internal server error",
    "temporarily unavailable",
    "503",
    "502",
    "504",
    "529",
    "circuit breaker open",
)

# Exception *type names* (matched case-insensitively, substring) that are
# always provider-side regardless of message.
_PROVIDER_ERROR_TYPES = (
    "ratelimit",
    "apitimeout",
    "apiconnection",
    "internalserver",
    "serviceunavailable",
    "overloaded",
    "connectionerror",
    "timeouterror",
)


def is_provider_error(exc: BaseException) -> bool:
    """Return True if *exc* looks like a transient provider-side failure.

    Errors that another provider could plausibly satisfy (rate limits,
    overloads, 5xx, connection drops, OPEN breaker) qualify; deterministic
    failures (validation, tool errors) do not.
    """
    # ConnectionError is what the circuit breaker raises when OPEN, and what
    # the network layer raises on a dropped socket.
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    type_name = type(exc).__name__.lower()
    if any(marker in type_name for marker in _PROVIDER_ERROR_TYPES):
        return True
    msg = str(exc).lower()
    return any(marker in msg for marker in _PROVIDER_ERROR_MARKERS)


def parse_fallback_chain(spec: str) -> list[dict]:
    """Parse a ``provider[:model],provider[:model]`` spec into provider dicts.

    Whitespace-tolerant; blank entries are skipped.  A missing model is left as
    ``None`` so :func:`build_llm` resolves it from the provider's tier config.
    """
    chain: list[dict] = []
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if ":" in entry:
            provider, model = entry.split(":", 1)
            chain.append({"provider": provider.strip().lower(), "model": model.strip() or None})
        else:
            chain.append({"provider": entry.lower(), "model": None})
    return chain


def _auto_chain() -> list[dict]:
    """Derive a fallback chain from whichever providers have credentials."""
    try:
        from prax.agent.multi_model import _available_providers
        return list(_available_providers())
    except Exception:
        logger.debug("Could not auto-derive fallback chain", exc_info=True)
        return []


def get_fallback_providers(primary_provider: str) -> list[dict]:
    """Return the ordered fallback chain for *primary_provider*.

    The primary provider is filtered out so we never "fail over" to the
    provider that just failed.  Returns an empty list when fallback is
    disabled or no alternate provider is configured.
    """
    if not settings.llm_fallback_enabled:
        return []

    primary = (primary_provider or "").lower()
    spec = (settings.llm_fallback_chain or "").strip()
    chain = parse_fallback_chain(spec) if spec else _auto_chain()

    seen: set[str] = {primary}
    ordered: list[dict] = []
    for entry in chain:
        prov = (entry.get("provider") or "").lower()
        if not prov or prov in seen:
            continue
        seen.add(prov)
        ordered.append({"provider": prov, "model": entry.get("model")})
    return ordered
