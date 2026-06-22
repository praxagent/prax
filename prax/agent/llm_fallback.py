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


# Substrings marking a *terminal* provider failure — one a retry or a wait will
# NOT fix because a human has to act (fix the key, pay the bill, get access, or
# update the model name).  Unlike transient errors, these get the provider
# DENYLISTED from the pool and the user told *why* (so they can find the late
# bill / revoked key), rather than re-tried every turn.  Keyed by cause so the
# notice can give an actionable hint.
_TERMINAL_ERROR_MARKERS: dict[str, tuple[str, ...]] = {
    "auth": (
        "401", "unauthorized", "invalid api key", "invalid_api_key",
        "incorrect api key", "no api key", "missing api key", "api key not valid",
        "authentication", "authentication_error", "invalid x-api-key",
    ),
    "billing": (
        "402", "payment required", "billing", "insufficient_quota",
        "insufficient quota", "exceeded your current quota", "quota exceeded",
        "credit balance", "insufficient credit", "out of credits",
        "spending limit", "past due", "account is not active", "unpaid",
    ),
    "access": (
        "403", "forbidden", "do not have access", "does not have access",
        "not entitled", "not authorized", "unsupported_country_region_territory",
        "not available in your region", "not available in your country",
        "export control",
    ),
    "decommissioned": (
        "model not found", "model_not_found", "no such model", "does not exist",
        "decommissioned", "has been deprecated", "has been retired",
        "unknown model", "model is not supported", "model_not_available",
    ),
}

# Per-cause user guidance: (short phrase for the error, actionable hint).
_TERMINAL_GUIDANCE: dict[str, tuple[str, str]] = {
    "auth": (
        "an authentication error",
        "the API key is likely missing, invalid, expired, or revoked — check this provider's credential",
    ),
    "billing": (
        "a billing/quota error",
        "this usually means an unpaid invoice, exhausted credit, or a hit spend cap — check the provider's billing dashboard",
    ),
    "access": (
        "an access/permissions error",
        "the account may not be entitled to this model, or it may be restricted in your region (e.g. an export control) — check model access and region eligibility",
    ),
    "decommissioned": (
        "a model-not-found error",
        "the model may have been renamed, retired, or deprecated — update the configured model name",
    ),
}


def classify_provider_error(exc: BaseException) -> str | None:
    """Classify *exc* as a provider failure.

    Returns one of ``"auth"``, ``"billing"``, ``"access"``, ``"decommissioned"``
    (**terminal** — a human must act; the provider should be denylisted), or
    ``"transient"`` (rate limit / overload / 5xx / connection — retry/failover),
    or ``None`` when it isn't a provider-side failure at all.

    Terminal causes are checked first and conservatively: an ambiguous message
    falls through to ``"transient"`` (which only retries) rather than wrongly
    denylisting a working provider.
    """
    haystack = f"{type(exc).__name__.lower()} {str(exc).lower()}"
    for kind, markers in _TERMINAL_ERROR_MARKERS.items():
        if any(m in haystack for m in markers):
            return kind
    return "transient" if is_provider_error(exc) else None


def terminal_user_notice(provider: str, kind: str, detail: str,
                         continuing: str | None = None, cooldown_seconds: int = 0) -> str:
    """Build a short, user-facing heads-up explaining why *provider* was denylisted.

    *detail* should be a non-sensitive token (e.g. the exception type name) —
    never the raw provider message, which can echo the API key.
    """
    phrase, guidance = _TERMINAL_GUIDANCE.get(
        kind, ("a persistent error", "a human may need to look into it"))
    cap = guidance[0].upper() + guidance[1:]
    parts = [
        f"⚠️ Heads-up: I've dropped **{provider}** from my model pool — it returned "
        f"{phrase} ({detail}). {cap}."
    ]
    parts.append(
        f"I'm continuing on **{continuing}** for now." if continuing
        else "I have no other provider configured, so this may keep failing until it's fixed."
    )
    if cooldown_seconds and cooldown_seconds > 0:
        parts.append(f"(I'll automatically re-try {provider} after about {max(1, cooldown_seconds // 60)} min.)")
    return " ".join(parts)


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
