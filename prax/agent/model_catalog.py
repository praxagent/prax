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


def catalog(override: str | None = None) -> dict[str, Any]:
    """Everything the UI needs to render a model picker."""
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

    return {
        "providers": [asdict(p) for p in providers],
        "tiers": tiers,
        "mode": selection_mode(override),
        "override": override,
        "egress_proxied": _egress_is_proxied(),
    }
