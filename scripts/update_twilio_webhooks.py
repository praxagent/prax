"""Fetch the ngrok public URL and update Twilio phone number webhooks.

Designed to run inside the app container at startup. Prints the ngrok URL
to stdout so the entrypoint script can capture and export it.
"""

import os
import sys
import time

import requests
from twilio.rest import Client


def get_ngrok_url(ngrok_api_base: str, retries: int = 30, delay: float = 2) -> str:
    """Poll ngrok's local API until the HTTPS tunnel URL is available."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(f"{ngrok_api_base}/api/tunnels", timeout=3)
            resp.raise_for_status()
            for tunnel in resp.json().get("tunnels", []):
                if tunnel.get("proto") == "https":
                    return tunnel["public_url"]
        except Exception:
            pass
        print(f"[ngrok-init] Waiting for ngrok tunnel... (attempt {attempt}/{retries})", file=sys.stderr)
        time.sleep(delay)
    raise RuntimeError(f"Could not get ngrok URL after {retries} attempts")


def update_twilio_webhooks(ngrok_url: str) -> None:
    """Look up the Twilio phone number by E.164 and set voice/SMS webhook URLs."""
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    phone_number = os.environ["ROOT_PHONE_NUMBER"]

    client = Client(account_sid, auth_token)

    numbers = client.incoming_phone_numbers.list(phone_number=phone_number)
    if not numbers:
        raise ValueError(f"No Twilio phone number found matching {phone_number}")

    number = numbers[0]
    number.update(
        voice_url=f"{ngrok_url}/transcribe",
        voice_method="POST",
        sms_url=f"{ngrok_url}/sms",
        sms_method="POST",
    )
    print(f"[ngrok-init] Updated Twilio webhooks for {phone_number}:", file=sys.stderr)
    print(f"[ngrok-init]   Voice -> {ngrok_url}/transcribe", file=sys.stderr)
    print(f"[ngrok-init]   SMS   -> {ngrok_url}/sms", file=sys.stderr)


def main():
    ngrok_api = os.environ.get("NGROK_API_URL", "http://ngrok:4040")

    ngrok_url = get_ngrok_url(ngrok_api)

    update_twilio_webhooks(ngrok_url)

    # Print to stdout so the entrypoint can capture it
    print(ngrok_url)


if __name__ == "__main__":
    main()
