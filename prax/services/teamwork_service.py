"""TeamWork integration — lets Prax control a TeamWork project via its API.

TeamWork provides a Slack-like web UI with channels, task boards, file browsers,
and real-time WebSocket updates. Prax uses it as a visual frontend — sending
messages, creating agents, updating tasks — while remaining the orchestrator.
"""
from __future__ import annotations

import logging
import threading
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

    def create_project(
        self,
        name: str,
        description: str,
        webhook_url: str,
        workspace_dir: str | None = None,
    ) -> dict:
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
                    # Update workspace_dir if it changed (e.g. phone number configured after first run).
                    if workspace_dir:
                        try:
                            self._patch(
                                f"/projects/{self._project_id}",
                                {"workspace_dir": workspace_dir},
                            )
                        except Exception:
                            logger.debug("Could not update workspace_dir", exc_info=True)
                    return proj
        except Exception:
            logger.debug("Could not list existing projects, creating new one", exc_info=True)

        payload: dict = {
            "name": name,
            "description": description,
            "webhook_url": webhook_url,
        }
        if workspace_dir:
            payload["workspace_dir"] = workspace_dir
        result = self._post("/projects", payload)
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
        channel_id: str | None = None,
        extra_data: dict | None = None,
    ) -> str | None:
        """Send a message to a TeamWork channel.

        Args:
            channel_id: If provided, send directly to this channel ID
                        (bypasses name lookup — use for DM channels or
                        channels not in the registered set).
            channel: Channel name to look up if channel_id is not given.
            extra_data: Optional metadata dict attached to the message
                        (e.g. ``{"trace_id": "abc123", "grafana_url": "..."}``).
        """
        if not self._project_id:
            return None
        if not channel_id:
            channel_id = self._channels.get(channel)
        if not channel_id:
            logger.warning("Unknown channel: %s (known: %s)", channel, list(self._channels.keys()))
            return None
        agent_id = self._agents.get(agent_name) if agent_name else None
        payload: dict = {
            "channel_id": channel_id,
            "agent_id": agent_id,
            "content": content,
        }
        if extra_data:
            payload["extra_data"] = extra_data
        try:
            result = self._post(f"/projects/{self._project_id}/messages", payload)
            return result.get("message_id")
        except Exception:
            logger.warning("Failed to send TeamWork message", exc_info=True)
            return None

    def send_typing(
        self,
        channel: str = "general",
        agent_name: str | None = None,
        channel_id: str | None = None,
        is_typing: bool = True,
    ) -> None:
        """Send a typing indicator (start or stop)."""
        if not self._project_id:
            return
        if not channel_id:
            channel_id = self._channels.get(channel)
        agent_id = self._agents.get(agent_name) if agent_name else None
        if not channel_id or not agent_id:
            return
        try:
            self._post(
                f"/projects/{self._project_id}/typing",
                {"channel_id": channel_id, "agent_id": agent_id, "is_typing": is_typing},
            )
        except Exception:
            pass

    def typing(
        self,
        channel: str = "general",
        agent_name: str | None = None,
        channel_id: str | None = None,
        interval: float = 3.0,
    ) -> _TypingContext:
        """Return a context manager that keeps the typing indicator alive.

        Usage::

            with tw.typing(channel_id=cid, agent_name="Prax"):
                # ... long-running work ...
        """
        return _TypingContext(self, channel, agent_name, channel_id, interval)

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

    # ----- Live output -----

    def update_live_output(
        self,
        agent_name: str,
        output: str,
        status: str = "running",
        append: bool = True,
        error: str | None = None,
    ) -> None:
        """Push live execution output for an agent to TeamWork.

        Called during spoke/subagent execution so the frontend can display
        real-time tool call logs.
        """
        agent_id = self._agents.get(agent_name)
        if not agent_id or not self._project_id:
            return
        try:
            self._post(
                f"/projects/{self._project_id}/agents/{agent_id}/live-output",
                {
                    "output": output,
                    "status": status,
                    "append": append,
                    "error": error,
                },
            )
        except Exception:
            logger.debug("Failed to push live output for %s", agent_name, exc_info=True)

    # ----- Channel management -----

    def get_channel_id(self, name: str) -> str | None:
        return self._channels.get(name)

    def add_channel(self, name: str, channel_id: str) -> None:
        self._channels[name] = channel_id


class _TypingContext:
    """Keeps a TeamWork typing indicator alive by re-sending every *interval* seconds."""

    def __init__(
        self,
        client: TeamWorkClient,
        channel: str,
        agent_name: str | None,
        channel_id: str | None,
        interval: float,
    ) -> None:
        self._client = client
        self._channel = channel
        self._agent_name = agent_name
        self._channel_id = channel_id
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _TypingContext:
        self._client.send_typing(
            channel=self._channel,
            agent_name=self._agent_name,
            channel_id=self._channel_id,
            is_typing=True,
        )
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self._client.send_typing(
            channel=self._channel,
            agent_name=self._agent_name,
            channel_id=self._channel_id,
            is_typing=False,
        )

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self._client.send_typing(
                channel=self._channel,
                agent_name=self._agent_name,
                channel_id=self._channel_id,
                is_typing=True,
            )


# Singleton
_client: TeamWorkClient | None = None


def get_teamwork_client() -> TeamWorkClient:
    global _client
    if _client is None:
        _client = TeamWorkClient()
    return _client
