"""Tests for TeamWork conversation history sync classification and formatting.

Prevents regressions like SHA256-derived TeamWork channel keys being
misclassified as Discord conversations.
"""
import hashlib
import json
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conversations_db(path: str, rows: dict[int, list[dict]]) -> None:
    """Create a test conversations.db with the given rows."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
    for conv_id, messages in rows.items():
        conn.execute("INSERT INTO conversations (id, data) VALUES (?, ?)",
                     (conv_id, json.dumps(messages)))
    conn.commit()
    conn.close()


def _sha256_channel_key(channel_uuid: str) -> int:
    """Generate a TeamWork-style SHA256 conversation key from a channel UUID."""
    return int(hashlib.sha256(channel_uuid.encode()).hexdigest()[:15], 16)


SAMPLE_SMS_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello from SMS", "date": "2026-03-01T10:00:00.000"},
    {"role": "assistant", "content": "Hi there!", "date": "2026-03-01T10:00:05.000"},
    {"role": "user", "content": "How are you?", "date": "2026-03-01T10:01:00.000"},
    {"role": "assistant", "content": "I'm doing well.", "date": "2026-03-01T10:01:05.000"},
]

SAMPLE_DISCORD_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hey from Discord", "date": "2026-03-02T15:00:00.000"},
    {"role": "assistant", "content": "Hello!", "date": "2026-03-02T15:00:05.000"},
]

SAMPLE_TEAMWORK_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "[via TeamWork web UI — public channel] hi",
     "date": "2026-03-03T20:00:00.000"},
    {"role": "assistant", "content": "Hello from TeamWork!", "date": "2026-03-03T20:00:05.000"},
]


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------

class TestSyncClassification:
    """Verify that conversations are classified into the correct channels."""

    def _make_client(self, db_path, discord_users=None, teamwork_user_phone=""):
        """Create a TeamWorkClient wired to a test DB."""
        from prax.services.teamwork_service import TeamWorkClient

        client = TeamWorkClient.__new__(TeamWorkClient)
        client.base_url = "http://fake:8000"
        client.api_key = ""
        client._project_id = "test-project"
        client._channels = {"sms": "sms-chan-id", "discord": "discord-chan-id"}
        client._agents = {"Prax": "prax-agent-id"}

        mock_settings = MagicMock()
        mock_settings.database_name = db_path
        mock_settings.discord_allowed_users = (
            json.dumps(discord_users) if discord_users else None
        )
        mock_settings.teamwork_user_phone = teamwork_user_phone

        return client, mock_settings

    def test_phone_number_classified_as_sms(self, tmp_path):
        """10-11 digit phone numbers go to #sms."""
        db_path = str(tmp_path / "conv.db")
        _make_conversations_db(db_path, {
            12678093704: SAMPLE_SMS_MESSAGES,
        })

        client, mock_settings = self._make_client(db_path)
        imported = []

        def fake_bulk(msgs):
            imported.extend(msgs)
            return len(msgs)

        client.bulk_import_messages = fake_bulk
        client.get_channel_message_count = lambda _: 0

        with patch("prax.services.teamwork_service.settings", mock_settings):
            result = client.sync_conversation_history()

        assert result.get("sms", 0) > 0
        # User messages should have sender label with phone
        user_msgs = [m for m in imported if "+12678093704" in m.get("content", "")]
        assert len(user_msgs) == 2  # 2 user messages (system skipped)

    def test_discord_id_in_allowed_users_classified_as_discord(self, tmp_path):
        """IDs found in DISCORD_ALLOWED_USERS go to #discord."""
        discord_id = 1034618247871483964
        db_path = str(tmp_path / "conv.db")
        _make_conversations_db(db_path, {
            discord_id: SAMPLE_DISCORD_MESSAGES,
        })

        client, mock_settings = self._make_client(
            db_path, discord_users={str(discord_id): "TJ"}
        )
        imported = []

        def fake_bulk(msgs):
            imported.extend(msgs)
            return len(msgs)

        client.bulk_import_messages = fake_bulk
        client.get_channel_message_count = lambda _: 0

        with patch("prax.services.teamwork_service.settings", mock_settings):
            result = client.sync_conversation_history()

        assert result.get("discord", 0) > 0
        # User message should have Discord display name
        user_msgs = [m for m in imported if "TJ" in m.get("content", "")]
        assert len(user_msgs) == 1

    def test_sha256_channel_key_skipped(self, tmp_path):
        """SHA256-derived TeamWork channel keys must NOT be classified as Discord."""
        # Generate a key the same way TeamWork does
        channel_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        sha_key = _sha256_channel_key(channel_uuid)

        db_path = str(tmp_path / "conv.db")
        _make_conversations_db(db_path, {
            sha_key: SAMPLE_TEAMWORK_MESSAGES,
        })

        client, mock_settings = self._make_client(db_path)
        imported = []

        def fake_bulk(msgs):
            imported.extend(msgs)
            return len(msgs)

        client.bulk_import_messages = fake_bulk
        client.get_channel_message_count = lambda _: 0

        with patch("prax.services.teamwork_service.settings", mock_settings):
            result = client.sync_conversation_history()

        # Nothing should be imported — SHA256 key should be skipped
        assert result == {}
        assert len(imported) == 0

    def test_teamwork_synthetic_user_skipped(self, tmp_path):
        """ID 10000000001 (TeamWork synthetic user) must always be skipped."""
        db_path = str(tmp_path / "conv.db")
        _make_conversations_db(db_path, {
            10000000001: SAMPLE_TEAMWORK_MESSAGES,
        })

        client, mock_settings = self._make_client(db_path)
        imported = []

        def fake_bulk(msgs):
            imported.extend(msgs)
            return len(msgs)

        client.bulk_import_messages = fake_bulk
        client.get_channel_message_count = lambda _: 0

        with patch("prax.services.teamwork_service.settings", mock_settings):
            result = client.sync_conversation_history()

        assert result == {}
        assert len(imported) == 0

    def test_unknown_18_digit_id_not_in_discord_users_skipped(self, tmp_path):
        """18-digit IDs NOT in DISCORD_ALLOWED_USERS must be skipped.

        These are likely SHA256-derived TeamWork channel keys, not Discord IDs.
        This was the original regression — digit count alone is not enough.
        """
        unknown_id = 870442053233137099  # Real 18-digit ID from prod

        db_path = str(tmp_path / "conv.db")
        _make_conversations_db(db_path, {
            unknown_id: SAMPLE_TEAMWORK_MESSAGES,
        })

        # DISCORD_ALLOWED_USERS does NOT include this ID
        client, mock_settings = self._make_client(
            db_path, discord_users={"999999999999999999": "SomeoneElse"}
        )
        imported = []

        def fake_bulk(msgs):
            imported.extend(msgs)
            return len(msgs)

        client.bulk_import_messages = fake_bulk
        client.get_channel_message_count = lambda _: 0

        with patch("prax.services.teamwork_service.settings", mock_settings):
            result = client.sync_conversation_history()

        assert result == {}
        assert len(imported) == 0

    def test_real_phone_not_skipped_even_if_teamwork_user_phone(self, tmp_path):
        """User's real SMS conversations should sync to #sms even when
        TEAMWORK_USER_PHONE matches (TeamWork web UI uses SHA256 keys)."""
        phone_key = 12678093704

        db_path = str(tmp_path / "conv.db")
        _make_conversations_db(db_path, {
            phone_key: SAMPLE_SMS_MESSAGES,
        })

        # TEAMWORK_USER_PHONE matches this conversation's phone number
        client, mock_settings = self._make_client(
            db_path, teamwork_user_phone="+12678093704"
        )
        imported = []

        def fake_bulk(msgs):
            imported.extend(msgs)
            return len(msgs)

        client.bulk_import_messages = fake_bulk
        client.get_channel_message_count = lambda _: 0

        with patch("prax.services.teamwork_service.settings", mock_settings):
            result = client.sync_conversation_history()

        # SMS messages should still be imported
        assert result.get("sms", 0) > 0


# ---------------------------------------------------------------------------
# Formatting tests
# ---------------------------------------------------------------------------

class TestSyncFormatting:
    """Verify message formatting during sync."""

    def _make_client(self):
        from prax.services.teamwork_service import TeamWorkClient

        client = TeamWorkClient.__new__(TeamWorkClient)
        client._agents = {"Prax": "prax-agent-id"}
        return client

    def test_system_messages_excluded(self):
        """System messages should not be imported."""
        client = self._make_client()
        batch = client._format_conversation_batch(
            [("+15551234567", SAMPLE_SMS_MESSAGES)],
            "chan-id", "SMS",
        )
        for msg in batch:
            assert "You are a helpful assistant" not in msg["content"]

    def test_user_messages_have_sender_label(self):
        """User messages should be formatted as **[sender]** content."""
        client = self._make_client()
        batch = client._format_conversation_batch(
            [("+15551234567", SAMPLE_SMS_MESSAGES)],
            "chan-id", "SMS",
        )
        user_msgs = [m for m in batch if "agent_id" not in m or m["agent_id"] is None]
        assert all("**[+15551234567]**" in m["content"] for m in user_msgs)

    def test_assistant_messages_attributed_to_prax(self):
        """Assistant messages should have agent_id set to Prax."""
        client = self._make_client()
        batch = client._format_conversation_batch(
            [("TestUser", SAMPLE_DISCORD_MESSAGES)],
            "chan-id", "Discord",
        )
        assistant_msgs = [m for m in batch if m.get("agent_id") == "prax-agent-id"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["content"] == "Hello!"

    def test_timestamps_preserved(self):
        """Original timestamps from conversations.db should be passed through."""
        client = self._make_client()
        batch = client._format_conversation_batch(
            [("User", SAMPLE_SMS_MESSAGES)],
            "chan-id", "SMS",
        )
        dates = [m["created_at"] for m in batch if "created_at" in m]
        assert len(dates) == 4  # 2 user + 2 assistant (system excluded)
        assert dates == sorted(dates)  # chronological order

    def test_multiple_conversations_interleaved_chronologically(self):
        """Messages from multiple users should be sorted by timestamp."""
        early = [
            {"role": "user", "content": "first", "date": "2026-01-01T00:00:00.000"},
            {"role": "assistant", "content": "r1", "date": "2026-01-01T00:00:01.000"},
        ]
        late = [
            {"role": "user", "content": "second", "date": "2026-01-02T00:00:00.000"},
            {"role": "assistant", "content": "r2", "date": "2026-01-02T00:00:01.000"},
        ]

        client = self._make_client()
        batch = client._format_conversation_batch(
            [("UserA", late), ("UserB", early)],
            "chan-id", "SMS",
        )

        contents = [m["content"] for m in batch]
        # UserB's "first" should come before UserA's "second"
        assert contents.index("r1") < contents.index("r2")


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------

class TestSyncIdempotency:
    """Verify sync doesn't re-import when channels already have messages."""

    def test_skips_when_channel_has_messages(self, tmp_path):
        """Sync should not import if channel already has messages."""
        db_path = str(tmp_path / "conv.db")
        _make_conversations_db(db_path, {
            12678093704: SAMPLE_SMS_MESSAGES,
        })

        from prax.services.teamwork_service import TeamWorkClient

        client = TeamWorkClient.__new__(TeamWorkClient)
        client.base_url = "http://fake:8000"
        client.api_key = ""
        client._project_id = "test-project"
        client._channels = {"sms": "sms-chan-id", "discord": "discord-chan-id"}
        client._agents = {"Prax": "prax-agent-id"}

        bulk_called = []
        client.bulk_import_messages = lambda msgs: bulk_called.append(msgs) or len(msgs)
        client.get_channel_message_count = lambda _: 50  # Already has messages

        mock_settings = MagicMock()
        mock_settings.database_name = db_path
        mock_settings.discord_allowed_users = None
        mock_settings.teamwork_user_phone = ""

        with patch("prax.services.teamwork_service.settings", mock_settings):
            result = client.sync_conversation_history()

        assert result == {}
        assert len(bulk_called) == 0

    def test_force_clears_and_reimports(self, tmp_path):
        """force=True should clear and re-import even when channel has messages."""
        db_path = str(tmp_path / "conv.db")
        _make_conversations_db(db_path, {
            12678093704: SAMPLE_SMS_MESSAGES,
        })

        from prax.services.teamwork_service import TeamWorkClient

        client = TeamWorkClient.__new__(TeamWorkClient)
        client.base_url = "http://fake:8000"
        client.api_key = ""
        client._project_id = "test-project"
        client._channels = {"sms": "sms-chan-id", "discord": "discord-chan-id"}
        client._agents = {"Prax": "prax-agent-id"}

        cleared = []
        imported = []

        client.bulk_import_messages = lambda msgs: imported.extend(msgs) or len(msgs)
        client.get_channel_message_count = lambda _: 50
        client.clear_channel_messages = lambda name: cleared.append(name) or 50

        mock_settings = MagicMock()
        mock_settings.database_name = db_path
        mock_settings.discord_allowed_users = None
        mock_settings.teamwork_user_phone = ""

        with patch("prax.services.teamwork_service.settings", mock_settings):
            result = client.sync_conversation_history(force=True)

        assert "sms" in cleared
        assert result.get("sms", 0) > 0
        assert len(imported) > 0
