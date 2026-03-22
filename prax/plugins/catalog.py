"""Auto-generated plugin catalog.

Regenerated whenever plugins are added, modified, or removed.
Parses plugin source files for metadata *without* importing them — safe to
call at any time without side-effects.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_plugin_metadata(plugin_py: Path) -> dict:
    """Extract PLUGIN_VERSION, PLUGIN_DESCRIPTION, and @tool names from source."""
    source = plugin_py.read_text()

    version = "1"
    m = re.search(r'PLUGIN_VERSION\s*=\s*["\']([^"\']+)["\']', source)
    if m:
        version = m.group(1)

    description = ""
    m = re.search(r'PLUGIN_DESCRIPTION\s*=\s*["\']([^"\']+)["\']', source)
    if m:
        description = m.group(1)

    tools = re.findall(r"@tool\s+def\s+(\w+)", source)

    return {"version": version, "description": description, "tools": tools}


def generate_catalog(*plugin_dirs: Path, catalog_path: Path | None = None) -> str:
    """Generate a CATALOG.md listing all plugins found in the given directories.

    Supports both folder-based plugins (``name/plugin.py``) and flat plugins
    (``name.py``).  Duplicate names are skipped (first directory wins).
    """
    lines = [
        "# Plugin Catalog",
        "",
        "Auto-generated list of all available plugins. **Do not edit manually.**",
        "",
        "| Plugin | Description | Version | Tools |",
        "|--------|-------------|---------|-------|",
    ]

    seen: set[str] = set()

    for plugins_dir in plugin_dirs:
        if not plugins_dir.is_dir():
            continue

        # Folder-based plugins: name/plugin.py
        for entry in sorted(plugins_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            plugin_py = entry / "plugin.py"
            if not plugin_py.exists():
                continue

            name = entry.name
            if name in seen:
                continue
            seen.add(name)

            try:
                meta = _parse_plugin_metadata(plugin_py)
                tools_str = ", ".join(f"`{t}`" for t in meta["tools"]) or "—"
                lines.append(
                    f"| `{name}` | {meta['description']} | v{meta['version']} | {tools_str} |"
                )
            except Exception:
                logger.warning("Failed to parse metadata for plugin %s", name)
                lines.append(f"| `{name}` | *(error reading metadata)* | ? | — |")

        # Flat plugins: name.py (backward compat)
        for plugin_file in sorted(plugins_dir.glob("*.py")):
            if plugin_file.name.startswith("_"):
                continue
            name = plugin_file.stem
            if name in seen:
                continue
            seen.add(name)

            try:
                meta = _parse_plugin_metadata(plugin_file)
                tools_str = ", ".join(f"`{t}`" for t in meta["tools"]) or "—"
                lines.append(
                    f"| `{name}` | {meta['description']} | v{meta['version']} | {tools_str} |"
                )
            except Exception:
                logger.warning("Failed to parse metadata for plugin %s", name)
                lines.append(f"| `{name}` | *(error reading metadata)* | ? | — |")

    content = "\n".join(lines) + "\n"

    if catalog_path:
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(content)
        logger.info("Updated catalog at %s", catalog_path)

    return content
