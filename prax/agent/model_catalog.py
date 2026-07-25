"""What models can this deployment actually use, and who decides which one.

Lives under ``agent/`` rather than ``services/`` on purpose: it reads the agent's
tier configuration (``model_tiers``), and the layer linter — correctly — forbids
services from importing agent modules. It is agent-domain knowledge with a
settings-shaped surface, not a service.

The UI cannot work this out for itself. Which providers are usable depends on
keys, base-URL overrides and — in the keyless deployment — on an egress proxy
that Prax deliberately cannot see into. So Prax reports it, and the UI renders
what it is told.

**The honesty problem this module has to solve.** Under keyless operation the
real provider keys live in the secrets-proxy; Prax holds only a placeholder and
routes egress through the proxy, which substitutes the real value. So "is
``ANTHROPIC_KEY`` set here?" is the *wrong question* — the answer is "no" on a
correctly-configured keyless box where Anthropic works perfectly. Reporting that
as "unavailable" would be a confident lie.

The catalog therefore reports **what Prax can determine**, and says which of
those it cannot confirm:

- ``configured`` — Prax has what it needs to *attempt* a call
- ``verified``   — Prax knows the credential is real (direct key, no proxy)
- ``reason``     — why not, in words a person can act on

A provider reachable only through the proxy comes back
``configured=True, verified=False`` with a reason saying the proxy holds the
credential. That is the truthful answer: it will probably work, and Prax is not
in a position to promise it.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Providers Prax can drive. `env_key` is what a DIRECT (non-proxied) deployment
# needs; `base_url_setting` is the override that redirects egress at a proxy.
PROVIDERS: tuple[dict[str, str], ...] = (
    {"id": "openai", "label": "OpenAI",
     "env_key": "openai_key", "base_url_setting": "openai_base_url"},
    {"id": "anthropic", "label": "Anthropic",
     "env_key": "anthropic_key", "base_url_setting": "anthropic_base_url"},
    {"id": "openrouter", "label": "OpenRouter",
     "env_key": "openrouter_api_key", "base_url_setting": "openrouter_base_url"},
)

# Where each provider publishes what it can serve. These are queried with the
# ordinary egress path, so under keyless operation the forward proxy injects the
# real credential and Prax discovers models holding only a placeholder — the
# same mechanism that makes completions work.
_DISCOVERY: dict[str, dict[str, Any]] = {
    # Public catalogue: no credential needed at all.
    "openrouter": {"url": "https://openrouter.ai/api/v1/models", "auth": None},
    "openai": {"url": "https://api.openai.com/v1/models", "auth": "bearer",
               "key_setting": "openai_key"},
    "anthropic": {"url": "https://api.anthropic.com/v1/models", "auth": "x-api-key",
                  "key_setting": "anthropic_key"},
}

# Discovered lists are cached: the UI polls this endpoint, and three provider
# round-trips per poll would be both slow and rude. Model catalogues change on
# the order of weeks.
_DISCOVERY_TTL_SECONDS = 3600
_discovery_cache: dict[str, tuple[float, list[str]]] = {}

# Model-name prefixes/substrings that identify which provider serves a model.
# Deliberately conservative: an unrecognised model is reported as provider
# "unknown" rather than guessed into the wrong bucket.
_MODEL_HINTS: tuple[tuple[str, str], ...] = (
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("claude", "anthropic"),
    ("/", "openrouter"),          # openrouter models are namespaced: vendor/model
)


def provider_for_model(model: str | None) -> str:
    """Best-effort provider for a model name, or ``"unknown"``.

    Order matters: an OpenRouter name is namespaced (``openai/gpt-4o-mini``) and
    would otherwise match the bare-``gpt-`` hint for OpenAI.
    """
    if not model:
        return "unknown"
    name = model.strip().lower()
    if "/" in name:
        return "openrouter"
    for hint, provider in _MODEL_HINTS:
        if hint != "/" and name.startswith(hint):
            return provider
    return "unknown"


def discover_models(provider_id: str, *, timeout: float = 6.0) -> list[str]:
    """Ask a provider what it can serve. Empty list if it will not say.

    Never raises and never blocks for long: this feeds a badge in the chat
    header, and a slow or unreachable provider must cost the user a shorter list,
    not a spinner or a stack trace.
    """
    import time

    spec = _DISCOVERY.get(provider_id)
    if not spec:
        return []

    cached = _discovery_cache.get(provider_id)
    if cached and (time.time() - cached[0]) < _DISCOVERY_TTL_SECONDS:
        return cached[1]

    try:
        import requests

        from prax.settings import settings

        headers = {}
        if spec["auth"] == "bearer":
            key = getattr(settings, spec["key_setting"], None)
            if not key:
                return []
            headers["Authorization"] = f"Bearer {key}"
        elif spec["auth"] == "x-api-key":
            key = getattr(settings, spec["key_setting"], None)
            if not key:
                return []
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"

        resp = requests.get(spec["url"], headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        # All three answer {"data": [{"id": ...}, ...]}.
        models = [m.get("id") for m in (payload.get("data") or []) if m.get("id")]
        models = sorted({m for m in models if m})
        _discovery_cache[provider_id] = (time.time(), models)
        return models
    except Exception as exc:  # noqa: BLE001 - a badge must not fail on a network blip
        logger.debug("model discovery failed for %s: %s", provider_id, exc)
        # Cache the failure briefly so a down provider is not retried on every
        # poll, but recover sooner than a success would.
        _discovery_cache[provider_id] = (time.time() - _DISCOVERY_TTL_SECONDS + 60, [])
        return []


def _egress_is_proxied() -> bool:
    """Whether outbound traffic goes through the credential-injecting proxy.

    Checked from the environment rather than settings because the forward proxy
    is wired with the conventional ``HTTPS_PROXY`` variables, not a Prax setting.
    """
    return bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))


@dataclass
class ProviderStatus:
    id: str
    label: str
    configured: bool
    #: True only when Prax itself holds a credential it can vouch for.
    verified: bool
    reason: str | None = None
    models: list[str] = field(default_factory=list)


def provider_status() -> list[ProviderStatus]:
    """Report each provider's usability, distinguishing fact from inference."""
    from prax.settings import settings

    proxied = _egress_is_proxied()
    out: list[ProviderStatus] = []
    for spec in PROVIDERS:
        key = getattr(settings, spec["env_key"], None)
        base_url = getattr(settings, spec["base_url_setting"], None)

        if key:
            # A key is present. Under proxied egress it may be a placeholder the
            # proxy replaces — so we can say "configured", not "verified".
            out.append(ProviderStatus(
                id=spec["id"], label=spec["label"], configured=True,
                verified=not proxied,
                reason=("egress is proxied — the secrets-proxy holds the real "
                        "credential, so this cannot be confirmed here")
                if proxied else None,
            ))
        elif base_url or proxied:
            # No local key, but egress is redirected: the proxy is expected to
            # supply the credential.
            out.append(ProviderStatus(
                id=spec["id"], label=spec["label"], configured=True, verified=False,
                reason=("no local key; the secrets-proxy is expected to inject "
                        "one. If calls fail, add it to the proxy's .env"),
            ))
        else:
            out.append(ProviderStatus(
                id=spec["id"], label=spec["label"], configured=False, verified=False,
                reason=f"no {spec['env_key'].upper()} configured",
            ))
    return out


def tier_assignments() -> dict[str, dict[str, Any]]:
    """The model bound to each tier, with the provider that serves it."""
    from prax.agent.model_tiers import get_tier_configs

    return {
        tc.tier.value: {
            "model": tc.model,
            "enabled": tc.enabled,
            "provider": provider_for_model(tc.model),
        }
        for tc in get_tier_configs().values()
    }


def selection_mode(override: str | None) -> str:
    """How the model is currently being chosen.

    Three genuinely different states, kept distinct because collapsing them into
    one dropdown loses meaning: ``auto`` lets Prax route per task, ``tier`` pins
    a capability level while still letting the tier's model change, and ``model``
    pins one exact model.
    """
    if not override:
        return "auto"
    from prax.agent.model_tiers import Tier

    return "tier" if override in {t.value for t in Tier} else "model"


def catalog(override: str | None = None, *, discover: bool = False) -> dict[str, Any]:
    """Everything the UI needs to render a model picker.

    ``discover`` asks each configured provider what it can serve. Off by default
    because the common call is a UI poll that only needs the current selection;
    the picker requests it when the user actually opens the dropdown.
    """
    providers = provider_status()
    tiers = tier_assignments()

    # Group the configured tier models under their provider so the picker can
    # show "what this deployment actually uses" without a hardcoded model list.
    by_provider: dict[str, list[str]] = {}
    for info in tiers.values():
        by_provider.setdefault(info["provider"], [])
        if info["model"] and info["model"] not in by_provider[info["provider"]]:
            by_provider[info["provider"]].append(info["model"])
    for p in providers:
        p.models = by_provider.get(p.id, [])
        if discover and p.configured:
            # Union: what the deployment is configured to use stays first-class
            # even if discovery fails or omits it.
            found = discover_models(p.id)
            p.models = sorted({*p.models, *found}) if found else p.models

    return {
        "providers": [asdict(p) for p in providers],
        "tiers": tiers,
        "mode": selection_mode(override),
        "override": override,
        "egress_proxied": _egress_is_proxied(),
    }
