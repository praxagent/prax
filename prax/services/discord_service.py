"""Discord bot service — runs alongside Flask in a background thread.

Connects to Discord via WebSocket (no ngrok needed).  Messages from
authorised users are routed through the same ConversationService used by
SMS/voice, so the agent has access to all tools.

Identity linking: if ``DISCORD_TO_PHONE_MAP`` maps a Discord user ID to a
phone number, that phone number is used as the service identity — sharing
conversation history, workspace, and scheduled messages with Twilio.
Otherwise the Discord ID is prefixed with ``D`` to create a standalone identity.

Gated behind ``DISCORD_BOT_TOKEN`` — if not set, the bot simply doesn't start.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import threading

from prax.settings import settings

logger = logging.getLogger(__name__)

# Populated at init time from DISCORD_ALLOWED_USERS JSON.
_allowed_users: dict[str, str] = {}

# Populated from DISCORD_ALLOWED_CHANNELS (comma-separated IDs).
_allowed_channels: set[int] = set()

# Maps Discord user ID → phone number for identity linking.
_discord_to_phone: dict[str, str] = {}

# Maps service user_id → most recent Discord channel (for sending files back).
_user_channels: dict[str, object] = {}

# Reference to the running bot (for clean shutdown / outbound messaging).
_bot_thread: threading.Thread | None = None
_loop: asyncio.AbstractEventLoop | None = None
_client = None  # discord.Client once connected

# Message length limit for Discord (2000 chars).
_DISCORD_MAX_LEN = 2000


def _load_allowed_users() -> dict[str, str]:
    raw = settings.discord_allowed_users
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse DISCORD_ALLOWED_USERS — expecting JSON dict")
        return {}


def _load_allowed_channels() -> set[int]:
    raw = settings.discord_allowed_channels
    if not raw:
        return set()
    return {int(ch.strip()) for ch in raw.split(",") if ch.strip().isdigit()}


def _load_discord_to_phone() -> dict[str, str]:
    raw = settings.discord_to_phone_map

    # Explicit opt-out: setting it to "none" or "false" disables auto-linking.
    if raw and raw.strip().lower() in ("none", "false", "off", "{}"):
        return {}

    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse DISCORD_TO_PHONE_MAP — expecting JSON dict")
            return {}

    # Auto-link: if there's exactly one Discord user and one phone user,
    # link them automatically so they share history/workspace.
    from prax.helpers_dictionaries import num_to_names
    discord_users = _load_allowed_users()
    if len(discord_users) == 1 and len(num_to_names) == 1:
        discord_id = next(iter(discord_users))
        phone = next(iter(num_to_names))
        logger.info(
            "Auto-linking Discord user %s → %s (single user on both channels). "
            "Set DISCORD_TO_PHONE_MAP=false to disable.",
            discord_id, phone,
        )
        return {discord_id: phone}

    return {}


def _user_id_for_service(discord_id: int | str) -> str:
    """Convert Discord user ID to an identifier for conversation_service.

    If ``DISCORD_TO_PHONE_MAP`` has a mapping for this user, returns the
    phone number (e.g. ``+15551234567``) so they share history/workspace
    with their Twilio identity.  Otherwise falls back to ``D{id}``.
    """
    phone = _discord_to_phone.get(str(discord_id))
    if phone:
        return phone
    return f"D{discord_id}"


def _chunk_message(text: str) -> list[str]:
    """Split a message into chunks that fit Discord's 2000-char limit."""
    if len(text) <= _DISCORD_MAX_LEN:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= _DISCORD_MAX_LEN:
            chunks.append(text)
            break
        # Try to split at a newline near the limit.
        cut = text.rfind("\n", 0, _DISCORD_MAX_LEN)
        if cut < _DISCORD_MAX_LEN // 2:
            cut = _DISCORD_MAX_LEN
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# Regex to find LaTeX math blocks: \[...\], $$...$$, or ```latex...```
_LATEX_BLOCK_RE = re.compile(
    r"```latex\s*(.*?)\s*```"      # ```latex ... ```
    r"|\$\$(.*?)\$\$"              # $$ ... $$
    r"|\\\[(.*?)\\\]",             # \[ ... \]
    re.DOTALL,
)

# Regex for HTML <img> tags with LaTeX (e.g. codecogs URLs the LLM likes to generate)
_IMG_TAG_RE = re.compile(
    r'<img\s[^>]*?alt="([^"]*)"[^>]*/?>',
    re.IGNORECASE,
)


def _clean_html_latex(text: str) -> str:
    """Replace <img> tags with inline code."""
    return _IMG_TAG_RE.sub(lambda m: f'`{m.group(1)}`', text)


def _render_latex_segments(text: str) -> list[tuple[str, str | None]]:
    """Split text into interleaved (text, image_path) segments.

    Returns a list of tuples:
      ("some text", None)       — plain text segment
      ("", "/path/to/math.png") — rendered math image
    Segments are in document order so Discord messages stay coherent.
    """
    from prax.services.latex_render import render_latex_snippet

    # First pass: convert HTML <img> tags to inline `code`
    text = _clean_html_latex(text)

    segments: list[tuple[str, str | None]] = []
    last_end = 0
    counter = 0

    for match in _LATEX_BLOCK_RE.finditer(text):
        # Text before this math block
        before = text[last_end:match.start()].strip()
        if before:
            segments.append((before, None))

        latex = match.group(1) or match.group(2) or match.group(3)
        if latex and latex.strip():
            # Strip stray backticks the LLM may have put inside the math block
            clean_latex = latex.strip().replace("`", "")
            counter += 1
            tmpfile = tempfile.mktemp(prefix=f"math{counter}_", suffix=".png")
            path = render_latex_snippet(clean_latex, tmpfile)
            if path:
                segments.append(("", path))
            else:
                # Render failed — keep raw LaTeX in a code block
                segments.append((f"```\n{latex.strip()}\n```", None))

        last_end = match.end()

    # Trailing text after last math block
    after = text[last_end:].strip()
    if after:
        segments.append((after, None))

    # If no math was found, return original text as-is
    if not segments:
        segments.append((text, None))

    return segments


def _build_bot():
    """Create and configure the discord.py bot.  Import is deferred so the
    module can be loaded even when discord.py is not installed.
    """
    import discord

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info("Discord bot connected as %s (id=%s)", client.user, client.user.id)

    @client.event
    async def on_message(message: discord.Message):
        # Ignore own messages.
        if message.author.id == client.user.id:
            return

        author_id = str(message.author.id)

        # Authorization check.
        if author_id not in _allowed_users:
            # If it's a DM, send a polite rejection.
            if isinstance(message.channel, discord.DMChannel):
                await message.channel.send("Sorry, you're not authorized to use this bot.")
            return

        # Channel check — if allowed_channels is set, only respond in those channels + DMs.
        if _allowed_channels and not isinstance(message.channel, discord.DMChannel):
            if message.channel.id not in _allowed_channels:
                return

        text_input = message.content.strip()
        if not text_input and not message.attachments:
            return

        user_id = _user_id_for_service(author_id)
        display_name = _allowed_users.get(author_id, message.author.display_name)
        _user_channels[user_id] = message.channel

        # Handle file attachments — download and include context.
        attachment_context = ""
        if message.attachments:
            for att in message.attachments:
                if att.content_type and "pdf" in att.content_type:
                    attachment_context += f"\n[PDF attachment: {att.filename}, URL: {att.url}]"
                elif att.content_type and att.content_type.startswith("image/"):
                    attachment_context += f"\n[Image attachment: {att.filename}, URL: {att.url}]"
                else:
                    attachment_context += f"\n[File attachment: {att.filename}, URL: {att.url}]"

        if attachment_context:
            text_input = (text_input or "User sent attachments") + attachment_context

        logger.info("Discord message from %s (%s): %s", display_name, author_id, text_input[:80])

        # Show typing indicator while processing.
        async with message.channel.typing():
            # Run the synchronous agent call in a thread pool.
            from prax.services.conversation_service import conversation_service
            try:
                response = await asyncio.to_thread(
                    conversation_service.reply, user_id, text_input
                )
            except Exception:
                logger.exception("Agent reply failed for Discord user %s", author_id)
                response = "Sorry, something went wrong processing your message. Please try again."

        # Render LaTeX math blocks as images, send interleaved with text.
        segments = await asyncio.to_thread(_render_latex_segments, response)
        for text_seg, img_path in segments:
            if text_seg:
                for chunk in _chunk_message(text_seg):
                    await message.channel.send(chunk)
            if img_path:
                try:
                    import discord as _discord
                    await message.channel.send(file=_discord.File(img_path))
                except Exception:
                    logger.exception("Failed to send rendered math image")


    return client


def start_bot() -> None:
    """Start the Discord bot in a background thread.

    Called during app startup.  Does nothing if ``DISCORD_BOT_TOKEN`` is
    not set.
    """
    global _bot_thread, _loop, _allowed_users, _allowed_channels, _discord_to_phone

    token = settings.discord_bot_token
    if not token:
        logger.debug("DISCORD_BOT_TOKEN not set — Discord bot disabled")
        return

    _allowed_users = _load_allowed_users()
    _allowed_channels = _load_allowed_channels()
    _discord_to_phone = _load_discord_to_phone()

    if _discord_to_phone:
        logger.info(
            "Discord→phone identity linking active for %d user(s)",
            len(_discord_to_phone),
        )

    if not _allowed_users:
        logger.warning(
            "DISCORD_BOT_TOKEN is set but DISCORD_ALLOWED_USERS is empty — "
            "the bot will reject all messages"
        )

    def _run():
        global _loop, _client
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _client = _build_bot()
        try:
            _loop.run_until_complete(_client.start(token))
        except Exception:
            logger.exception("Discord bot crashed")
        finally:
            try:
                _loop.close()
            except RuntimeError:
                pass

    _bot_thread = threading.Thread(target=_run, daemon=True, name="discord-bot")
    _bot_thread.start()
    logger.info("Discord bot starting in background thread")


def send_message(user_id: str, message: str) -> None:
    """Send a DM to a Discord user from outside the event loop.

    ``user_id`` should be in the ``D{discord_id}`` format used internally.
    Blocks until the message is sent (up to 30 s timeout).
    """
    if _loop is None or _client is None:
        raise RuntimeError("Discord bot is not running")

    discord_id = int(user_id[1:])

    async def _send():
        user = await _client.fetch_user(discord_id)
        for chunk in _chunk_message(message):
            await user.send(chunk)

    future = asyncio.run_coroutine_threadsafe(_send(), _loop)
    future.result(timeout=30)


def send_file(user_id: str, file_path: str, message: str = "") -> None:
    """Send a file to the Discord channel associated with a user.

    ``user_id`` is the service identity (phone or D{discord_id}).
    ``file_path`` is an absolute path to the file on disk.
    Blocks until the file is sent (up to 60 s timeout).
    """
    import discord as _discord

    if _loop is None or _client is None:
        raise RuntimeError("Discord bot is not running")

    channel = _user_channels.get(user_id)
    if channel is None:
        raise RuntimeError(f"No Discord channel on record for {user_id}")

    async def _send():
        await channel.send(content=message or None, file=_discord.File(file_path))

    future = asyncio.run_coroutine_threadsafe(_send(), _loop)
    future.result(timeout=60)


def stop_bot() -> None:
    """Signal the bot to disconnect (best-effort)."""
    global _loop, _client
    if _loop and _loop.is_running():
        _loop.call_soon_threadsafe(_loop.stop)
    _client = None
