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

    # ----- Activity logs -----

    def create_activity_log(
        self,
        agent_name: str,
        activity_type: str,
        description: str,
        extra_data: dict | None = None,
    ) -> None:
        """Create a persistent activity log entry for an agent."""
        agent_id = self._agents.get(agent_name)
        if not agent_id or not self._project_id:
            return
        try:
            self._post(f"/projects/{self._project_id}/activity", {
                "agent_id": agent_id,
                "activity_type": activity_type,
                "description": description,
                "extra_data": extra_data,
            })
        except Exception:
            logger.debug("Failed to create activity log for %s", agent_name, exc_info=True)

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

    def ensure_channels(self, channels: list[dict[str, str]]) -> None:
        """Ensure the listed channels exist, creating any that are missing.

        Called on startup to add channels (like #discord, #sms) that may
        not have existed when the project was first created.
        """
        if not self._project_id:
            return
        try:
            result = self._post(
                f"/projects/{self._project_id}/ensure-channels",
                {"channels": channels},
            )
            updated = result.get("channels", {})
            self._channels.update(updated)
            logger.info("Ensured channels: %s", list(updated.keys()))
        except Exception:
            logger.debug("Failed to ensure channels", exc_info=True)

    def get_channel_message_count(self, channel_name: str) -> int:
        """Get the number of messages in a TeamWork channel."""
        if not self._project_id:
            return -1
        channel_id = self._channels.get(channel_name)
        if not channel_id:
            return -1
        try:
            result = self._get(
                f"/projects/{self._project_id}/channels/{channel_id}/message-count"
            )
            return result.get("count", 0)
        except Exception:
            logger.debug("Failed to get message count for %s", channel_name, exc_info=True)
            return -1

    def bulk_import_messages(self, messages: list[dict]) -> int:
        """Bulk import historical messages into TeamWork.

        Each dict should have: channel_id, content, agent_id (optional),
        message_type (optional, default 'chat'), created_at (optional ISO 8601).
        """
        if not self._project_id or not messages:
            return 0
        try:
            # Use a longer timeout for bulk operations (can be hundreds of messages)
            resp = requests.post(
                self._url(f"/projects/{self._project_id}/messages/bulk"),
                json={"messages": messages},
                headers=self._headers(),
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json().get("imported", 0)
        except Exception:
            logger.warning("Failed to bulk import messages", exc_info=True)
            return 0

    def clear_channel_messages(self, channel_name: str) -> int:
        """Delete all messages from a TeamWork channel (for re-sync)."""
        if not self._project_id:
            return 0
        channel_id = self._channels.get(channel_name)
        if not channel_id:
            return 0
        try:
            resp = requests.delete(
                self._url(f"/projects/{self._project_id}/channels/{channel_id}/messages"),
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("deleted", 0)
        except Exception:
            logger.warning("Failed to clear channel %s", channel_name, exc_info=True)
            return 0

    def sync_conversation_history(self, force: bool = False) -> dict[str, int]:
        """Sync historical SMS and Discord conversations to TeamWork channels.

        Reads from the conversations.db SQLite database and imports messages
        that haven't been synced yet. Returns a dict of channel_name → count.

        Args:
            force: If True, clear existing messages before re-syncing.
        """
        import json
        import sqlite3

        if not self._project_id:
            return {}

        db_path = settings.database_name
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT id, data FROM conversations")
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            logger.warning("Failed to read conversations.db for sync", exc_info=True)
            return {}

        # Load Discord display names from settings — these are the ONLY
        # conversation IDs we'll classify as Discord.  This avoids
        # misclassifying SHA256-derived TeamWork channel keys.
        discord_names: dict[str, str] = {}
        if settings.discord_allowed_users:
            try:
                discord_names = json.loads(settings.discord_allowed_users)
            except (json.JSONDecodeError, TypeError):
                pass

        # IDs to always skip — the TeamWork synthetic user fallback.
        # We do NOT skip the user's real phone number — those are real SMS
        # conversations that should go to #sms.  TeamWork web UI conversations
        # use SHA256-derived keys so they won't collide.
        skip_ids: set[int] = {10000000001}

        # Classify conversations by channel
        sms_conversations: list[tuple[str, list[dict]]] = []
        discord_conversations: list[tuple[str, list[dict]]] = []

        for row_id, data_json in rows:
            conv_id = str(row_id)

            # Skip TeamWork synthetic user and the user's own phone
            # (TeamWork web UI conversations use SHA256-derived keys, not these)
            if row_id in skip_ids:
                continue

            messages = json.loads(data_json)
            if not messages:
                continue

            if conv_id in discord_names:
                # Positively identified as a Discord user
                display = discord_names[conv_id]
                discord_conversations.append((display, messages))
            elif 10 <= len(conv_id) <= 15:
                # Looks like a phone number — SMS conversation
                phone = f"+{conv_id}"
                sms_conversations.append((phone, messages))
            else:
                # SHA256-derived TeamWork channel key or unknown — skip
                logger.debug("Skipping conversation %s (not SMS or known Discord)", conv_id)

        result: dict[str, int] = {}

        # Sync SMS conversations to #sms channel
        sms_channel_id = self._channels.get("sms")
        if sms_channel_id and sms_conversations:
            count = self.get_channel_message_count("sms")
            if force and count > 0:
                self.clear_channel_messages("sms")
                count = 0
            if count <= 0:
                batch = self._format_conversation_batch(
                    sms_conversations, sms_channel_id, "SMS"
                )
                if batch:
                    result["sms"] = self.bulk_import_messages(batch)

        # Sync Discord conversations to #discord channel
        discord_channel_id = self._channels.get("discord")
        if discord_channel_id and discord_conversations:
            count = self.get_channel_message_count("discord")
            if force and count > 0:
                self.clear_channel_messages("discord")
                count = 0
            if count <= 0:
                batch = self._format_conversation_batch(
                    discord_conversations, discord_channel_id, "Discord"
                )
                if batch:
                    result["discord"] = self.bulk_import_messages(batch)

        if result:
            logger.info("Synced conversation history to TeamWork: %s", result)
        return result

    def _format_conversation_batch(
        self,
        conversations: list[tuple[str, list[dict]]],
        channel_id: str,
        source: str,
    ) -> list[dict]:
        """Format conversation history into bulk import messages."""
        agent_id = self._agents.get("Prax")
        batch: list[dict] = []

        for sender_label, messages in conversations:
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                date = msg.get("date")

                if role == "system" or not content:
                    continue

                if role == "user":
                    item = {
                        "channel_id": channel_id,
                        "content": f"**[{sender_label}]** {content}",
                        "message_type": "chat",
                    }
                else:
                    # Assistant message — attribute to Prax agent
                    item = {
                        "channel_id": channel_id,
                        "agent_id": agent_id,
                        "content": content,
                        "message_type": "chat",
                    }

                if date:
                    item["created_at"] = date

                batch.append(item)

        # Sort by timestamp to interleave conversations chronologically
        batch.sort(key=lambda m: m.get("created_at", ""))
        return batch

    # ----- Terminal (shared PTY) -----

    def terminal_exec(self, command: str, timeout: float = 5.0) -> dict | None:
        """Execute a command in the user's shared terminal.

        Writes the command to the active PTY session so the user sees it,
        waits for output to settle, and returns the captured output.
        Returns None if no terminal session is active.
        """
        if not self._project_id:
            return None
        url = f"{self.base_url}/api/terminal/{self._project_id}/exec"
        try:
            resp = requests.post(
                url,
                json={"command": command, "timeout": timeout},
                headers=self._headers(),
                timeout=timeout + 5,
            )
            if resp.status_code == 404:
                return None  # No active terminal session
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.debug("terminal_exec failed", exc_info=True)
            return None

    def forward_external_message(
        self,
        channel_name: str,
        sender_label: str,
        content: str,
        agent_name: str | None = None,
    ) -> str | None:
        """Forward a message from an external channel (Discord, SMS) to TeamWork.

        Posts a formatted message to the #discord or #sms channel so users
        can see cross-channel conversations in TeamWork.
        """
        channel_id = self._channels.get(channel_name)
        if not channel_id:
            return None
        formatted = f"**[{sender_label}]** {content}"
        return self.send_message(
            content=formatted,
            channel_id=channel_id,
            agent_name=agent_name,
        )


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
