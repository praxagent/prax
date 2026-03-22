"""Hot-reloadable LLM routing configuration.

Reads ``plugins/configs/llm_routing.yaml`` and provides per-component
overrides for provider, model, and temperature.  The config file can be
modified by the agent at runtime — changes take effect on the next call
to ``get_component_config()``.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

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


def get_component_config(component: str) -> dict[str, str | float | None]:
    """Return LLM config for a named component.

    Returns a dict with keys ``provider``, ``model``, ``temperature``.
    Values are ``None`` if not overridden (meaning use global defaults).
    """
    config = _load_config()
    components = config.get("components") or {}
    overrides = components.get(component) or {}
    defaults = config.get("defaults") or {}

    return {
        "provider": overrides.get("provider") or defaults.get("provider"),
        "model": overrides.get("model") or defaults.get("model"),
        "temperature": overrides.get("temperature") if "temperature" in overrides else defaults.get("temperature"),
    }


def update_component_config(component: str, **kwargs) -> dict:
    """Update the routing config for a component and persist.

    Args:
        component: Component name (e.g. "orchestrator", "subagent_research").
        **kwargs: Keys to update (provider, model, temperature).

    Returns:
        The updated component config.
    """
    config = _load_config()
    if "components" not in config:
        config["components"] = {}
    if component not in config["components"]:
        config["components"][component] = {}

    for key in ("provider", "model", "temperature"):
        if key in kwargs:
            config["components"][component][key] = kwargs[key]

    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    logger.info("Updated LLM config for %s: %s", component, kwargs)
    return config["components"][component]
