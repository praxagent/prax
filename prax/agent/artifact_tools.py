"""Artifact tools: pages Prax makes for a person and keeps updating.

``artifact_publish`` writes or updates one; TeamWork shows it in a sandboxed
viewer. ``artifact_share_public`` puts one on a public link — a hard floor that
holds even with HARD_FLOORS_ENABLED off, because a public link has no password:
a person decides every time. See prax/services/artifact_service.py.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langchain_core.tools import tool

from prax.agent.user_context import current_turn_source, current_user_id
from prax.services import artifact_service
from prax.settings import settings

_MAX_PUBLIC_HOURS = 168


def _uid() -> str:
    return current_user_id.get() or ""


@tool
def artifact_publish(title: str, html: str, artifact_id: str = "") -> str:
    """Create an artifact, or update one you made earlier, and show it in TeamWork.

    An artifact is a self-contained HTML page the user keeps: a plan, a table,
    a chart, a dashboard, a small interactive tool. Use one when the result is
    something to look at or come back to, not just to read once. Update an
    existing artifact (pass its artifact_id) instead of making a near-copy.

    Write ONE complete HTML document:
    - everything inline: CSS in <style>, JS in <script>, data in the page;
    - no network: the viewer blocks requests, external scripts, fonts and
      images (use inline SVG or data: URIs);
    - responsive (it may be shown narrow), and readable in light and dark
      (use prefers-color-scheme);
    - no secrets, tokens or passwords in it.

    Show it to the user by putting ``[artifact:<id>]`` on its own line in your
    reply: TeamWork renders the artifact there. On Discord or SMS that marker
    means nothing; say it is in TeamWork (or, if the user asks for a link
    anyone can open, use artifact_share_public).

    Args:
        title: A short name for it (required when creating).
        html: The complete HTML document.
        artifact_id: The id of an artifact to replace with a new version; empty to create.
    """
    try:
        item = artifact_service.publish(_uid(), title, html, artifact_id=artifact_id)
    except artifact_service.ArtifactError as e:
        return f"Not published: {e}."
    verb = "Created" if item["version"] == 1 else f"Updated to version {item['version']}"
    return (
        f"{verb}: \"{item['title']}\" (artifact {item['id']}, {item['size']} bytes).\n"
        f"Put [artifact:{item['id']}] on its own line in your reply to show it in TeamWork."
    )


@tool
def artifact_list() -> str:
    """List your artifacts (id, title, version, last update), newest first."""
    items = artifact_service.list_all(_uid())
    if not items:
        return "No artifacts yet."
    return "\n".join(
        f"- {m['id']}: \"{m.get('title', '')}\" v{m.get('version', 1)} "
        f"(updated {m.get('updated_at', '?')})" for m in items[:50])


@tool
def artifact_share_public(artifact_id: str, hours: int = 0) -> str:
    """Put an artifact on a PUBLIC link that anyone who has the link can open.

    The link has no password, so this always needs the user's decision for
    this exact artifact: they approve it in TeamWork, or ask for it in their
    own words ("share the retention chart publicly"). Only use it when the
    user wants to share outside TeamWork; inside TeamWork just show it with
    [artifact:<id>]. The link expires (default ARTIFACT_PUBLIC_HOURS, at most
    168 hours) and shows the latest version until then.

    Args:
        artifact_id: The artifact to share.
        hours: How long the link should work (0 = the default).
    """
    from prax.services import share_registry
    from prax.utils.ngrok import get_ngrok_url

    uid = _uid()
    page = artifact_service.page_path(uid, artifact_id)
    if page is None:
        return f"No artifact {artifact_id!r}; artifact_list shows the ones that exist."
    if not get_ngrok_url():
        return ("No public tunnel is configured (NGROK_URL), so there is no public "
                "address to share it at. It is still in TeamWork.")
    default = int(getattr(settings, "artifact_public_hours", 24) or 24)
    lifetime = max(1, min(int(hours or default), _MAX_PUBLIC_HOURS))
    expires = (datetime.now(UTC) + timedelta(hours=lifetime)).isoformat()
    entry = share_registry.register_file(
        uid, page, channel=current_turn_source.get() or None, expires_at=expires)
    url = share_registry.public_url_for(entry)
    return (
        f"Public link (anyone with it can open it; no password) — works for "
        f"{lifetime} hour(s), until {expires[:16].replace('T', ' ')} UTC:\n{url}\n"
        f"It shows the latest version of the artifact. Revoke it any time with "
        f"artifact_unshare(artifact_id=\"{artifact_id}\")."
    )


@tool
def artifact_unshare(artifact_id: str) -> str:
    """Revoke every public link to an artifact. It stays in TeamWork."""
    from prax.services import share_registry

    uid = _uid()
    page = artifact_service.page_path(uid, artifact_id)
    if page is None:
        return f"No artifact {artifact_id!r}."
    revoked = [e["token"] for e in share_registry.list_all(uid)
               if e.get("abs_path") == page and share_registry.revoke(uid, e["token"])]
    if not revoked:
        return f"{artifact_id} had no public link."
    return f"Revoked {len(revoked)} public link(s) to {artifact_id}. It is still in TeamWork."


def build_artifact_tools() -> list:
    """The artifact tools when ARTIFACTS_ENABLED, else none."""
    if not getattr(settings, "artifacts_enabled", False):
        return []
    return [artifact_publish, artifact_list, artifact_share_public, artifact_unshare]
