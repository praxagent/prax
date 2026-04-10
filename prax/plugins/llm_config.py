"""Hot-reloadable LLM routing configuration.

Reads ``plugins/configs/llm_routing.yaml`` and provides per-component
overrides for provider, model, tier, and temperature.  The config file can be
modified by the agent at runtime — changes take effect on the next call
to ``get_component_config()``.

Experiment overrides
--------------------
For A/B testing, callers can set temporary per-component overrides via
:func:`set_experiment_overrides`.  These take highest priority (above both
the YAML file and environment defaults) and are scoped to the current
``contextvars`` context, so parallel test runs don't interfere.
"""
from __future__ import annotations

import contextvars
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Experiment override context
# ---------------------------------------------------------------------------

_experiment_overrides: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "_experiment_overrides", default=None
)


def set_experiment_overrides(overrides: dict | None) -> contextvars.Token:
    """Activate per-component tier/model overrides for the current context.

    *overrides* is a dict keyed by component name (e.g. ``"orchestrator"``,
    ``"subagent_research"``) with values that can contain ``tier``, ``model``,
    ``provider``, and/or ``temperature``.

    Returns a token that must be passed to :func:`clear_experiment_overrides`
    to restore the previous state.

    Example::

        token = set_experiment_overrides({
            "subagent_research": {"tier": "medium"},
            "orchestrator": {"tier": "high"},
        })
        try:
            agent.run(...)
        finally:
            clear_experiment_overrides(token)
    """
    logger.info("Experiment overrides activated: %s", overrides)
    return _experiment_overrides.set(overrides)


def clear_experiment_overrides(token: contextvars.Token) -> None:
    """Restore the override state to before :func:`set_experiment_overrides`."""
    _experiment_overrides.reset(token)
    logger.info("Experiment overrides cleared")

_CONFIG_PATH = Path(__file__).parent / "configs" / "llm_routing.yaml"


def _load_config() -> dict:
    """Load the YAML config (re-reads on every call for hot-reload)."""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        logger.exception("Failed to load LLM routing config")
        return {}


def _env_override(component: str, key: str) -> str | None:
    """Check for an env-var override for a component config key.

    Convention: ``{COMPONENT}_{KEY}`` in UPPER_CASE, e.g.::

        ORCHESTRATOR_TIER=high
        SUBAGENT_RESEARCH_TIER=medium
        SUBAGENT_BROWSER_PROVIDER=anthropic

    Returns ``None`` if the env var isn't set or is empty.
    """
    import os
    env_name = f"{component.upper()}_{key.upper()}"
    val = os.environ.get(env_name, "").strip()
    return val or None


def get_component_config(component: str) -> dict[str, str | float | None]:
    """Return LLM config for a named component.

    Returns a dict with keys ``provider``, ``model``, ``tier``, ``temperature``.
    Values are ``None`` if not overridden (meaning use global defaults).

    Priority (highest first):
      1. Environment variable (``{COMPONENT}_{KEY}``, e.g.
         ``ORCHESTRATOR_TIER=high``) — lets operators override per
         deployment without touching config files.
      2. Experiment overrides (via :func:`set_experiment_overrides`)
      3. Per-component YAML overrides (``llm_routing.yaml``)
      4. YAML defaults
    """
    config = _load_config()
    components = config.get("components") or {}
    overrides = components.get(component) or {}
    defaults = config.get("defaults") or {}

    result = {
        "provider": overrides.get("provider") or defaults.get("provider"),
        "model": overrides.get("model") or defaults.get("model"),
        "tier": overrides.get("tier") or defaults.get("tier"),
        "temperature": overrides.get("temperature") if "temperature" in overrides else defaults.get("temperature"),
    }

    # Apply experiment overrides
    exp = _experiment_overrides.get()
    if exp and component in exp:
        comp_exp = exp[component]
        for key in ("provider", "model", "tier", "temperature"):
            if key in comp_exp:
                result[key] = comp_exp[key]

    # Apply env-var overrides (highest priority)
    for key in ("provider", "model", "tier"):
        env_val = _env_override(component, key)
        if env_val is not None:
            result[key] = env_val
    # Temperature needs float conversion
    temp_env = _env_override(component, "temperature")
    if temp_env is not None:
        try:
            result["temperature"] = float(temp_env)
        except ValueError:
            pass

    return result


def update_component_config(component: str, **kwargs) -> dict:
    """Update the routing config for a component and persist.

    Args:
        component: Component name (e.g. "orchestrator", "subagent_research").
        **kwargs: Keys to update (provider, model, tier, temperature).

    Returns:
        The updated component config.
    """
    config = _load_config()
    if "components" not in config:
        config["components"] = {}
    if component not in config["components"]:
        config["components"][component] = {}

    for key in ("provider", "model", "tier", "temperature"):
        if key in kwargs:
            config["components"][component][key] = kwargs[key]

    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    logger.info("Updated LLM config for %s: %s", component, kwargs)
    return config["components"][component]
