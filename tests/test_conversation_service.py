import importlib

from langchain_core.messages import HumanMessage, SystemMessage


class StubAgent:
    def __init__(self):
        self.calls = []

    def run(self, conversation, user_input, workspace_context=""):
        self.calls.append((conversation, user_input, workspace_context))
        return "stub-response"


def test_reply_seeds_and_saves_history():
    module = importlib.reload(importlib.import_module('prax.services.conversation_service'))

    store = {}

    def fake_retrieve(db, phone_int):
        return store.get(phone_int)

    def fake_save(db, phone_int, payload):
        store.setdefault(phone_int, []).append(payload)

    service = module.ConversationService(
        agent=StubAgent(),
        retriever=fake_retrieve,
        saver=fake_save,
        database_name="memory.db",
    )

    result = service.reply("+10000000000", "hello")

    assert result == "stub-response"
    assert len(store[10000000000]) == 3  # system, user, assistant
    assert store[10000000000][0]['role'] == 'system'
    assert service.agent.calls[0][1] == "hello"
    assert isinstance(service.agent.calls[0][0][0], SystemMessage)


def test_reply_uses_existing_history():
    module = importlib.reload(importlib.import_module('prax.services.conversation_service'))

    history = [
        {'role': 'system', 'content': 'Hi', 'date': 'now'},
        {'role': 'user', 'content': 'Question', 'date': 'later'},
    ]
    store = {10000000000: history.copy()}

    def fake_retrieve(db, phone_int):
        return store.get(phone_int)

    def fake_save(db, phone_int, payload):
        store.setdefault(phone_int, []).append(payload)

    agent = StubAgent()
    service = module.ConversationService(
        agent=agent,
        retriever=fake_retrieve,
        saver=fake_save,
        database_name="memory.db",
    )

    service.reply("+10000000000", "follow up")

    conversation_passed = agent.calls[0][0]
    assert isinstance(conversation_passed[0], SystemMessage)
    assert isinstance(conversation_passed[1], HumanMessage)
    assert store[10000000000][-1]['role'] == 'assistant'

