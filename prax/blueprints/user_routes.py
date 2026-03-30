"""User identity API — manage users, display names, timezones, and workspace archiving."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from prax.services.identity_service import (
    archive_workspace,
    get_identities,
    get_user,
    get_user_by_identity,
    link_identity,
    list_users,
    update_user,
)

logger = logging.getLogger(__name__)

user_routes = Blueprint("users", __name__)


@user_routes.route("/api/users", methods=["GET"])
def api_list_users():
    """List all users."""
    users = list_users()
    return jsonify([_user_dict(u) for u in users])


@user_routes.route("/api/users/<user_id>", methods=["GET"])
def api_get_user(user_id: str):
    """Get a user by UUID."""
    user = get_user(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    result = _user_dict(user)
    result["identities"] = get_identities(user_id)
    return jsonify(result)


@user_routes.route("/api/users/<user_id>", methods=["PATCH"])
def api_update_user(user_id: str):
    """Update a user's display_name and/or timezone."""
    data = request.get_json(silent=True) or {}
    user = update_user(
        user_id,
        display_name=data.get("display_name"),
        timezone=data.get("timezone"),
    )
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify(_user_dict(user))


@user_routes.route("/api/users/<user_id>/link", methods=["POST"])
def api_link_identity(user_id: str):
    """Link a new provider identity to this user."""
    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "")
    external_id = data.get("external_id", "")
    if not provider or not external_id:
        return jsonify({"error": "provider and external_id required"}), 400
    ok = link_identity(user_id, provider, external_id)
    if not ok:
        return jsonify({"error": "Identity already linked to a different user"}), 409
    return jsonify({"status": "linked"})


@user_routes.route("/api/users/<user_id>/archive", methods=["POST"])
def api_archive_workspace(user_id: str):
    """Archive this user's workspace as a zip and create a fresh one."""
    path = archive_workspace(user_id)
    if not path:
        return jsonify({"error": "No workspace to archive"}), 404
    return jsonify({"status": "archived", "archive_path": path})


@user_routes.route("/api/users/by-identity/<provider>/<path:external_id>", methods=["GET"])
def api_get_by_identity(provider: str, external_id: str):
    """Look up a user by provider identity."""
    user = get_user_by_identity(provider, external_id)
    if not user:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_user_dict(user))


def _user_dict(user) -> dict:
    return {
        "id": user.id,
        "display_name": user.display_name,
        "workspace_dir": user.workspace_dir,
        "timezone": user.timezone,
        "created_at": user.created_at,
    }
