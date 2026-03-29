"""Plugin management API routes.

Thin REST wrapper around workspace_service plugin management functions.
Used by the TeamWork UI and available for direct API access.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

plugin_routes = Blueprint("plugins", __name__)

logger = logging.getLogger(__name__)


def _get_user_id() -> str:
    """Resolve the user_id from query params or settings."""
    uid = request.args.get("user_id")
    if uid:
        return uid
    from prax.settings import settings
    return settings.teamwork_user_phone or "+10000000001"


@plugin_routes.route("/plugins", methods=["GET"])
def list_plugins():
    """List all shared (imported) plugins with registry metadata."""
    user_id = _get_user_id()

    try:
        from prax.services.workspace_service import list_shared_plugins
        plugins = list_shared_plugins(user_id)
    except Exception:
        plugins = []

    # Enrich with registry data (trust tier, warnings acknowledged, etc.)
    try:
        from prax.plugins.registry import PluginRegistry
        registry = PluginRegistry()
        reg_data = registry.list_plugins()
    except Exception:
        reg_data = {}

    enriched = []
    for p in plugins:
        rel_key = f"shared/{p['name']}"
        reg_entry = reg_data.get(rel_key, {})
        enriched.append({
            **p,
            "trust_tier": reg_entry.get("trust_tier", "imported"),
            "active_version": reg_entry.get("active_version"),
            "security_warnings_acknowledged": reg_entry.get(
                "security_warnings_acknowledged", False
            ),
        })

    return jsonify(enriched)


@plugin_routes.route("/plugins/import", methods=["POST"])
def import_plugin():
    """Import a plugin from a git repository URL."""
    user_id = _get_user_id()
    data = request.get_json(force=True)
    repo_url = data.get("repo_url")
    if not repo_url:
        return jsonify({"error": "repo_url is required"}), 400

    name = data.get("name")
    plugin_subfolder = data.get("plugin_subfolder")

    try:
        from prax.services.workspace_service import import_plugin_repo
        result = import_plugin_repo(
            user_id, repo_url, name=name, plugin_subfolder=plugin_subfolder,
        )
        return jsonify(result)
    except Exception as e:
        logger.exception("Failed to import plugin")
        return jsonify({"error": str(e)}), 500


@plugin_routes.route("/plugins/<name>", methods=["DELETE"])
def remove_plugin(name: str):
    """Remove an imported plugin."""
    user_id = _get_user_id()

    try:
        from prax.services.workspace_service import remove_plugin_repo
        result = remove_plugin_repo(user_id, name)

        # Refresh plugin loader.
        try:
            from prax.plugins.loader import get_plugin_loader
            get_plugin_loader().load_all()
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        logger.exception("Failed to remove plugin %s", name)
        return jsonify({"error": str(e)}), 500


@plugin_routes.route("/plugins/<name>/update", methods=["POST"])
def update_plugin(name: str):
    """Pull latest version of an imported plugin."""
    user_id = _get_user_id()

    try:
        from prax.services.workspace_service import update_plugin_repo
        result = update_plugin_repo(user_id, name)

        # Refresh plugin loader.
        try:
            from prax.plugins.loader import get_plugin_loader
            get_plugin_loader().load_all()
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        logger.exception("Failed to update plugin %s", name)
        return jsonify({"error": str(e)}), 500


@plugin_routes.route("/plugins/<name>/acknowledge", methods=["POST"])
def acknowledge_warnings(name: str):
    """Acknowledge security warnings for an imported plugin."""
    rel_path = f"shared/{name}"

    try:
        from prax.plugins.registry import PluginRegistry
        registry = PluginRegistry()
        registry.acknowledge_warnings(rel_path)

        # Refresh plugin loader so the plugin activates.
        try:
            from prax.plugins.loader import get_plugin_loader
            get_plugin_loader().load_all()
        except Exception:
            pass

        return jsonify({"status": "acknowledged", "name": name})
    except Exception as e:
        logger.exception("Failed to acknowledge warnings for %s", name)
        return jsonify({"error": str(e)}), 500


@plugin_routes.route("/plugins/<name>/check-updates", methods=["GET"])
def check_updates(name: str):
    """Check if a plugin has upstream updates without pulling."""
    user_id = _get_user_id()

    try:
        from prax.services.workspace_service import check_plugin_updates
        result = check_plugin_updates(user_id, name)
        if "error" in result:
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        logger.exception("Failed to check updates for %s", name)
        return jsonify({"error": str(e)}), 500


@plugin_routes.route("/plugins/check-updates", methods=["GET"])
def check_all_updates():
    """Check all imported plugins for upstream updates."""
    user_id = _get_user_id()

    try:
        from prax.services.workspace_service import (
            check_plugin_updates,
            list_shared_plugins,
        )
        plugins = list_shared_plugins(user_id)
        results = []
        for p in plugins:
            result = check_plugin_updates(user_id, p["name"])
            results.append(result)
        return jsonify(results)
    except Exception as e:
        logger.exception("Failed to check updates for all plugins")
        return jsonify({"error": str(e)}), 500


@plugin_routes.route("/plugins/update-all", methods=["POST"])
def update_all_plugins():
    """Pull latest version for all imported plugins."""
    user_id = _get_user_id()

    try:
        from prax.services.workspace_service import (
            list_shared_plugins,
            update_plugin_repo,
        )
        plugins = list_shared_plugins(user_id)
        results = []
        for p in plugins:
            try:
                result = update_plugin_repo(user_id, p["name"])
                results.append(result)
            except Exception as e:
                results.append({"name": p["name"], "error": str(e)})

        # Refresh plugin loader once after all updates.
        try:
            from prax.plugins.loader import get_plugin_loader
            get_plugin_loader().load_all()
        except Exception:
            pass

        return jsonify(results)
    except Exception as e:
        logger.exception("Failed to update all plugins")
        return jsonify({"error": str(e)}), 500


@plugin_routes.route("/plugins/<name>/security", methods=["GET"])
def security_scan(name: str):
    """Run a security scan on an imported plugin."""
    user_id = _get_user_id()

    try:
        from prax.services.workspace_service import (
            get_workspace_plugins_dir,
            scan_plugin_security,
        )
        plugins_dir = get_workspace_plugins_dir(user_id)
        if not plugins_dir:
            return jsonify({"error": "No plugins directory found"}), 404

        import os
        plugin_dir = os.path.join(plugins_dir, "shared", name)
        if not os.path.isdir(plugin_dir):
            return jsonify({"error": f"Plugin '{name}' not found"}), 404

        warnings = scan_plugin_security(plugin_dir)
        return jsonify({"name": name, "warnings": warnings})
    except Exception as e:
        logger.exception("Failed to scan plugin %s", name)
        return jsonify({"error": str(e)}), 500


@plugin_routes.route("/plugins/<name>/skills", methods=["GET"])
def plugin_skills(name: str):
    """Return the Skills.md content for a plugin.

    Searches for Skills.md in each plugin subfolder within the shared
    plugin repo.  Live metadata (version, description, tools) is read
    from ``plugin.py`` — never duplicated in the markdown.

    If the plugin is a multi-plugin repo the ``subfolder`` query
    parameter can select a specific plugin.
    """
    import os
    from pathlib import Path

    from prax.plugins.catalog import _parse_plugin_metadata

    user_id = _get_user_id()
    subfolder = request.args.get("subfolder", "")

    try:
        from prax.services.workspace_service import (
            get_workspace_plugins_dir,
        )
        plugins_dir = get_workspace_plugins_dir(user_id)
        if not plugins_dir:
            return jsonify({"error": "No plugins directory found"}), 404

        repo_dir = os.path.join(plugins_dir, "shared", name)
        if not os.path.isdir(repo_dir):
            return jsonify({"error": f"Plugin '{name}' not found"}), 404

        # If a subfolder is requested, look there first.
        if subfolder:
            candidate = os.path.join(repo_dir, subfolder, "Skills.md")
            if os.path.isfile(candidate):
                with open(candidate, encoding="utf-8") as f:
                    content = f.read()
                meta = _read_plugin_meta(repo_dir, subfolder)
                return jsonify({"name": name, "subfolder": subfolder,
                                "content": content, **meta})

        # Collect Skills.md from every plugin subfolder in the repo.
        skills: list[dict] = []
        for dirpath, _dirs, files in os.walk(repo_dir):
            if "Skills.md" in files:
                rel = os.path.relpath(dirpath, repo_dir)
                sub = rel if rel != "." else None
                with open(os.path.join(dirpath, "Skills.md"),
                          encoding="utf-8") as f:
                    content = f.read()
                meta = _read_plugin_meta(repo_dir, sub)
                skills.append({
                    "subfolder": sub,
                    "content": content,
                    **meta,
                })

        if not skills:
            return jsonify({"error": "No Skills.md found for this plugin"}), 404

        return jsonify({"name": name, "skills": skills})
    except Exception as e:
        logger.exception("Failed to read skills for plugin %s", name)
        return jsonify({"error": str(e)}), 500


def _read_plugin_meta(repo_dir: str, subfolder: str | None) -> dict:
    """Read version, description, and tools from a plugin.py next to Skills.md."""
    import os
    from pathlib import Path

    from prax.plugins.catalog import _parse_plugin_metadata

    base = os.path.join(repo_dir, subfolder) if subfolder else repo_dir
    plugin_py = Path(base) / "plugin.py"
    if plugin_py.is_file():
        meta = _parse_plugin_metadata(plugin_py)
        return {
            "version": meta.get("version", "?"),
            "description": meta.get("description", ""),
            "tools": meta.get("tools", []),
        }
    return {}
