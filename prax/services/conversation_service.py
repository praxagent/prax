"""Conversation service — orchestrates the LangChain agent per-session.

Architecture
~~~~~~~~~~~~
There are two memory layers in Prax:

1. **SQLite history** (``conversation_memory.py``)  — the legacy store.
   ``add_dict_to_list`` / ``retrieve_dict`` persist raw message dicts keyed
   by ``(channel, user_id)``.  This is used to reload history when a session
   resumes and is the *only* remaining consumer of ``conversation_memory``.

2. **Workspace trace log** (``workspace_service.append_trace`` / ``search_trace``)
   — the newer, git-backed log.  Every assistant turn is appended as a plain-
   text line to ``<workspace>/trace.log`` (auto-rotated at 0.5 MB).  Use this
   for debugging, audit trails, and cross-session search.

New features should use workspace traces for persistence and avoid adding
new call sites into ``conversation_memory``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prax.agent import ConversationAgent
from prax.agent.user_context import current_user, current_user_id
from prax.conversation_memory import add_dict_to_list, retrieve_dict
from prax.services.workspace_service import get_workspace_context
from prax.settings import settings

logger = logging.getLogger(__name__)


def _history_to_messages(history: list[dict]) -> list:
    messages = []
    for item in history:
        if item["role"] == "system":
            messages.append(SystemMessage(content=item["content"]))
        elif item["role"] == "assistant":
            messages.append(AIMessage(content=item["content"]))
        else:
            messages.append(HumanMessage(content=item["content"]))
    return messages


class ConversationService:
    def __init__(
        self,
        agent: ConversationAgent | None = None,
        retriever: Callable[[str, int], list[dict] | None] = retrieve_dict,
        saver: Callable[[str, int, list[dict]], None] = add_dict_to_list,
        database_name: str | None = None,
    ) -> None:
        self.agent = agent or ConversationAgent()
        self._retrieve = retriever
        self._save = saver
        self._database = database_name or settings.database_name

    def _build_history(self, phone_int: int) -> list[dict]:
        conversation = self._retrieve(self._database, phone_int) or []
        return [
            {key: value for key, value in entry.items() if key != "date"}
            for entry in conversation
        ]

    def reply(self, user_id: str, text: str, *, conversation_key: int | None = None) -> str:
        """Process a user message and return the agent's response.

        Args:
            user_id: User UUID (preferred) or legacy phone number / ``D{id}``.
            text: The user's message.
            conversation_key: Override the DB key for conversation history.
                When provided (e.g. per-channel key for TeamWork), history is
                isolated under this key instead of the default user-derived one.
        """
        # Set user context so workspace tools know which user to operate on.
        current_user_id.set(user_id)

        # Resolve User object for richer context (display_name, timezone, etc.)
        user_obj = None
        try:
            from prax.services.identity_service import get_user
            user_obj = get_user(user_id)
            if user_obj:
                current_user.set(user_obj)
        except Exception:
            pass

        # Derive the database key for conversation history.
        if conversation_key is not None:
            db_key = conversation_key
        elif user_obj:
            # Stable integer derived from UUID (first 15 hex digits).
            db_key = int(user_id.replace("-", "")[:15], 16)
        else:
            # Legacy fallback: phone number (+1555...) or D{discord_id}.
            raw = user_id.lstrip("+").lstrip("D")
            db_key = int(raw)

        history = self._build_history(db_key)
        if not history:
            self._save(self._database, db_key, {
                "role": "system",
                "content": "You are a helpful assistant."
            })
            history = self._build_history(db_key)

        self._save(self._database, db_key, {"role": "user", "content": text})

        lc_history = _history_to_messages(history)
        workspace_ctx = get_workspace_context(user_id)
        logger.info("Agent invoked for %s (key=%s): %s", user_id, db_key, text[:80])
        response = self.agent.run(
            conversation=lc_history,
            user_input=text,
            workspace_context=workspace_ctx,
        )
        logger.info("Agent response for %s: %s", user_id, response[:80])

        self._save(self._database, db_key, {"role": "assistant", "content": response})
        return response


conversation_service = ConversationService()
