"""Service layer that encapsulates the new LangChain-based agent."""
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

    def reply(self, from_number: str, text: str) -> str:
        # Set user context so workspace tools know which user to operate on
        current_user_id.set(from_number)

        phone_int = int(from_number[1:])
        history = self._build_history(phone_int)
        if not history:
            self._save(self._database, phone_int, {
                "role": "system",
                "content": "You are a helpful assistant."
            })
            history = self._build_history(phone_int)

        self._save(self._database, phone_int, {"role": "user", "content": text})

        lc_history = _history_to_messages(history)
        workspace_ctx = get_workspace_context(from_number)
        logger.info("Agent invoked for %s: %s", from_number, text[:80])
        response = self.agent.run(
            conversation=lc_history,
            user_input=text,
            workspace_context=workspace_ctx,
        )
        logger.info("Agent response for %s: %s", from_number, response[:80])

        self._save(self._database, phone_int, {"role": "assistant", "content": response})
        return response


conversation_service = ConversationService()
