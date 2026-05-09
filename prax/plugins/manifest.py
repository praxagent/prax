"""Plugin manifest parsing and validation.

External plugins declare their identity, tools, routing intent, and coarse
risk in ``plugin.json``. The manifest is data, not authority: Prax core
decides which routes are visible to which agent and whether any requested
orchestrator exposure is granted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "plugin.json"

KNOWN_ROUTES = frozenset({
    "artifact",
    "media",
    "research",
    "sysadmin",
    "utility",
    "vision",
    "workspace",
})

KNOWN_RISK_LEVELS = frozenset({"low", "medium", "high"})
KNOWN_ORCHESTRATOR_EXPOSURE = frozenset({"none", "requested"})


class PluginManifestError(ValueError):
    """Raised when a plugin manifest is missing or invalid."""


@dataclass(frozen=True)
class PluginToolManifest:
    """Manifest metadata for one tool exposed by a plugin."""

    name: str
    description: str
    route: str
    risk: str
    orchestrator_exposure: str = "none"


@dataclass(frozen=True)
class PluginManifest:
    """Validated manifest metadata for a plugin directory."""

    name: str
    version: str
    description: str
    tools: tuple[PluginToolManifest, ...]

    @property
    def tool_map(self) -> dict[str, PluginToolManifest]:
        return {tool.name: tool for tool in self.tools}


def load_plugin_manifest(
    plugin_dir: str | Path,
    *,
    required: bool = False,
) -> PluginManifest | None:
    """Load and validate ``plugin.json`` from *plugin_dir*.

    Args:
        plugin_dir: Directory containing ``plugin.py``.
        required: If true, a missing manifest is an error. If false, missing
            manifests return ``None`` for backward compatibility.
    """
    manifest_path = Path(plugin_dir) / MANIFEST_FILENAME
    if not manifest_path.is_file():
        if required:
            raise PluginManifestError(f"Missing required {MANIFEST_FILENAME}")
        return None

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginManifestError(f"Invalid {MANIFEST_FILENAME}: {exc}") from exc
    except OSError as exc:
        raise PluginManifestError(f"Could not read {MANIFEST_FILENAME}: {exc}") from exc

    return parse_plugin_manifest(raw)


def parse_plugin_manifest(raw: dict[str, Any]) -> PluginManifest:
    """Validate raw manifest JSON data and return a structured manifest."""
    if not isinstance(raw, dict):
        raise PluginManifestError("Manifest must be a JSON object")

    name = _required_str(raw, "name")
    version = _required_str(raw, "version")
    description = _required_str(raw, "description")
    tools_raw = raw.get("tools")
    if not isinstance(tools_raw, list) or not tools_raw:
        raise PluginManifestError("Manifest field 'tools' must be a non-empty list")

    tools: list[PluginToolManifest] = []
    seen: set[str] = set()
    for idx, item in enumerate(tools_raw):
        if not isinstance(item, dict):
            raise PluginManifestError(f"tools[{idx}] must be an object")
        tool_name = _required_str(item, "name", label=f"tools[{idx}].name")
        if tool_name in seen:
            raise PluginManifestError(f"Duplicate tool declaration: {tool_name}")
        seen.add(tool_name)
        route = _required_str(item, "route", label=f"tools[{idx}].route")
        if route not in KNOWN_ROUTES:
            raise PluginManifestError(
                f"Tool {tool_name!r} declares unknown route {route!r}; "
                f"known routes: {sorted(KNOWN_ROUTES)}"
            )
        risk = _required_str(item, "risk", label=f"tools[{idx}].risk")
        if risk not in KNOWN_RISK_LEVELS:
            raise PluginManifestError(
                f"Tool {tool_name!r} declares unknown risk {risk!r}; "
                f"known risks: {sorted(KNOWN_RISK_LEVELS)}"
            )
        exposure = str(item.get("orchestrator_exposure", "none")).strip().lower()
        if exposure not in KNOWN_ORCHESTRATOR_EXPOSURE:
            raise PluginManifestError(
                f"Tool {tool_name!r} declares unknown orchestrator_exposure "
                f"{exposure!r}; known values: {sorted(KNOWN_ORCHESTRATOR_EXPOSURE)}"
            )
        tools.append(PluginToolManifest(
            name=tool_name,
            description=_required_str(
                item, "description", label=f"tools[{idx}].description",
            ),
            route=route,
            risk=risk,
            orchestrator_exposure=exposure,
        ))

    return PluginManifest(
        name=name,
        version=version,
        description=description,
        tools=tuple(tools),
    )


def _required_str(
    data: dict[str, Any],
    field: str,
    *,
    label: str | None = None,
) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PluginManifestError(f"Manifest field '{label or field}' is required")
    return value.strip()
