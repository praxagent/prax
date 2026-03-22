"""Twilio request signature validation middleware.

Validates that incoming webhook requests actually originate from Twilio
by checking the ``X-Twilio-Signature`` header against the request URL
and POST parameters using the account's auth token.

Gated behind ``TWILIO_AUTH_TOKEN`` — if not configured, validation is
skipped (development mode) with a warning on first request.
"""
from __future__ import annotations

import functools
import logging

from flask import abort, request

from prax.settings import settings

logger = logging.getLogger(__name__)

_warned_no_token = False


def validate_twilio_request(f):
    """Decorator that validates Twilio webhook signatures.

    If ``TWILIO_AUTH_TOKEN`` is not set, requests are allowed through
    with a one-time warning (for local development without Twilio).
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        global _warned_no_token

        auth_token = settings.twilio_auth_token
        if not auth_token:
            if not _warned_no_token:
                logger.warning(
                    "TWILIO_AUTH_TOKEN not set — Twilio request validation disabled. "
                    "Set it in production to prevent spoofed webhook calls."
                )
                _warned_no_token = True
            return f(*args, **kwargs)

        from twilio.request_validator import RequestValidator

        validator = RequestValidator(auth_token)

        # Build the full URL Twilio signed against.
        url = request.url
        # Twilio signs against the POST body params (not JSON).
        post_vars = request.form.to_dict()
        signature = request.headers.get("X-Twilio-Signature", "")

        if not validator.validate(url, post_vars, signature):
            logger.warning(
                "Twilio signature validation failed for %s from %s",
                request.path, request.remote_addr,
            )
            abort(403)

        return f(*args, **kwargs)

    return decorated
