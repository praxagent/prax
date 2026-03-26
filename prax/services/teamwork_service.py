"""TeamWork integration — lets Prax control a TeamWork project via its API.

TeamWork provides a Slack-like web UI with channels, task boards, file browsers,
and real-time WebSocket updates. Prax uses it as a visual frontend — sending
messages, creating agents, updating tasks — while remaining the orchestrator.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from prax.settings import settings

logger = logging.getLogger(__name__)


class TeamWorkClient:
    """HTTP client for the TeamWork external agent API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.teamwork_url or "").rstrip("/")
        self.api_key = api_key or settings.teamwork_api_key or ""
        self._project_id: str | None = None
        self._channels: dict[str, str] = {}  # name -> id
        self._agents: dict[str, str] = {}  # name -> id

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    @property
    def project_id(self) -> str | None:
        return self._project_id

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/external{path}"

    def _post(self, path: str, json: dict) -> dict:
        resp = requests.post(self._url(path), json=json, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, json: dict) -> dict:
        resp = requests.patch(self._url(path), json=json, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ----- Project lifecycle -----

    def _get(self, path: str) -> Any:
        resp = requests.get(self._url(path), headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def create_project(self, name: str, description: str, webhook_url: str) -> dict:
        """Create or reconnect to an external-mode project in TeamWork."""
        # Check for existing external projects first (idempotent startup).
        try:
            existing = self._get("/projects")
            for proj in existing:
                if proj.get("name") == name:
                    self._project_id = proj["project_id"]
                    self._channels = proj.get("channels", {})
                    self._agents = proj.get("agents", {})
                    logger.info(
                        "Reconnected to TeamWork project %s (id=%s)",
                        name, self._project_id,
                    )
                    return proj
        except Exception:
            logger.debug("Could not list existing projects, creating new one", exc_info=True)

        result = self._post("/projects", {
            "name": name,
            "description": description,
            "webhook_url": webhook_url,
        })
        self._project_id = result["project_id"]
        self._channels = result.get("channels", {})
        logger.info(
            "Created TeamWork project %s (id=%s, channels=%s)",
            name, self._project_id, list(self._channels.keys()),
        )
        return result

    # ----- Agents -----

    def create_agent(self, name: str, role: str = "assistant", soul: str = "") -> str:
        """Create or reconnect to an agent in TeamWork and return its ID."""
        if not self._project_id:
            raise RuntimeError("No active TeamWork project")
        # Idempotent: if the agent was already loaded (e.g. from create_project),
        # reuse it instead of creating a duplicate.
        existing_id = self._agents.get(name)
        if existing_id:
            logger.info("Reconnected to existing TeamWork agent %s (id=%s)", name, existing_id)
            return existing_id
        result = self._post(f"/projects/{self._project_id}/agents", {
            "name": name,
            "role": role,
            "soul_prompt": soul,
        })
        agent_id = result["agent_id"]
        self._agents[name] = agent_id
        logger.info("Created TeamWork agent %s (id=%s)", name, agent_id)
        return agent_id

    def set_agent_status(self, agent_name: str, status: str) -> None:
        """Update an agent's status (idle/working/offline)."""
        agent_id = self._agents.get(agent_name)
        if not agent_id or not self._project_id:
            return
        try:
            self._patch(
                f"/projects/{self._project_id}/agents/{agent_id}/status",
                {"status": status},
            )
        except Exception:
            logger.debug("Failed to update agent status", exc_info=True)

    def get_agent_id(self, name: str) -> str | None:
        return self._agents.get(name)

    # ----- Messages -----

    def send_message(
        self,
        content: str,
        channel: str = "general",
        agent_name: str | None = None,
    ) -> str | None:
        """Send a message to a TeamWork channel."""
        if not self._project_id:
            return None
        channel_id = self._channels.get(channel)
        if not channel_id:
            logger.warning("Unknown channel: %s (known: %s)", channel, list(self._channels.keys()))
            return None
        agent_id = self._agents.get(agent_name) if agent_name else None
        try:
            result = self._post(f"/projects/{self._project_id}/messages", {
                "channel_id": channel_id,
                "agent_id": agent_id,
                "content": content,
            })
            return result.get("message_id")
        except Exception:
            logger.warning("Failed to send TeamWork message", exc_info=True)
            return None

    def send_typing(self, channel: str = "general", agent_name: str | None = None) -> None:
        """Send a typing indicator."""
        if not self._project_id:
            return
        channel_id = self._channels.get(channel)
        agent_id = self._agents.get(agent_name) if agent_name else None
        if not channel_id or not agent_id:
            return
        try:
            self._post(
                f"/projects/{self._project_id}/typing",
                {"channel_id": channel_id, "agent_id": agent_id},
            )
        except Exception:
            pass

    # ----- Tasks -----

    def create_task(
        self,
        title: str,
        description: str = "",
        assigned_to: str | None = None,
        status: str = "pending",
    ) -> str | None:
        """Create a task on the TeamWork board."""
        if not self._project_id:
            return None
        agent_id = self._agents.get(assigned_to) if assigned_to else None
        try:
            result = self._post(f"/projects/{self._project_id}/tasks", {
                "title": title,
                "description": description,
                "assigned_to": agent_id,
                "status": status,
            })
            return result.get("task_id")
        except Exception:
            logger.warning("Failed to create TeamWork task", exc_info=True)
            return None

    def update_task(self, task_id: str, **kwargs: Any) -> None:
        """Update a task (status, assigned_to, title, description)."""
        if not self._project_id or not task_id:
            return
        # Resolve agent name to ID if provided
        if "assigned_to" in kwargs and kwargs["assigned_to"]:
            kwargs["assigned_to"] = self._agents.get(kwargs["assigned_to"], kwargs["assigned_to"])
        try:
            self._patch(f"/projects/{self._project_id}/tasks/{task_id}", kwargs)
        except Exception:
            logger.warning("Failed to update TeamWork task", exc_info=True)

    # ----- Channel management -----

    def get_channel_id(self, name: str) -> str | None:
        return self._channels.get(name)

    def add_channel(self, name: str, channel_id: str) -> None:
        self._channels[name] = channel_id


# Singleton
_client: TeamWorkClient | None = None


def get_teamwork_client() -> TeamWorkClient:
    global _client
    if _client is None:
        _client = TeamWorkClient()
    return _client
