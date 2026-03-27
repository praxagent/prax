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
from prax.agent.user_context import current_user_id
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

    def reply(self, from_number: str, text: str, *, conversation_key: int | None = None) -> str:
        """Process a user message and return the agent's response.

        Args:
            from_number: User phone number (used for workspace context).
            text: The user's message.
            conversation_key: Override the DB key for conversation history.
                When provided (e.g. per-channel key for TeamWork), history is
                isolated under this key instead of the phone-number-derived one.
        """
        # Set user context so workspace tools know which user to operate on
        current_user_id.set(from_number)

        db_key = conversation_key if conversation_key is not None else int(from_number[1:])
        history = self._build_history(db_key)
        if not history:
            self._save(self._database, db_key, {
                "role": "system",
                "content": "You are a helpful assistant."
            })
            history = self._build_history(db_key)

        self._save(self._database, db_key, {"role": "user", "content": text})

        lc_history = _history_to_messages(history)
        workspace_ctx = get_workspace_context(from_number)
        logger.info("Agent invoked for %s (key=%s): %s", from_number, db_key, text[:80])
        response = self.agent.run(
            conversation=lc_history,
            user_input=text,
            workspace_context=workspace_ctx,
        )
        logger.info("Agent response for %s: %s", from_number, response[:80])

        self._save(self._database, db_key, {"role": "assistant", "content": response})
        return response


conversation_service = ConversationService()
