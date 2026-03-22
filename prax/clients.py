"""Shared Twilio client for the application."""
from __future__ import annotations

from twilio.rest import Client

from prax.settings import settings

_twilio_client: Client | None = None


def get_twilio_client() -> Client:
    """Return a lazily-initialized, cached Twilio Client.

    Raises RuntimeError if Twilio credentials are not configured.
    """
    global _twilio_client
    if _twilio_client is None:
        if not settings.twilio_account_sid or not settings.twilio_auth_token:
            raise RuntimeError(
                "Twilio credentials not configured. "
                "Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env, "
                "or use Discord instead."
            )
        _twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    return _twilio_client
