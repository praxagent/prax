#!/usr/bin/env python3
"""Clipboard bridge — WebSocket server that syncs X11 clipboard with browser clients."""

import asyncio
import json
import logging
import subprocess

import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s clipboard-bridge: %(message)s")
log = logging.getLogger("clipboard-bridge")

POLL_INTERVAL = 0.5
PORT = 6090
clients: set = set()
last_clipboard = ""


def xsel_get() -> str:
    """Read X11 clipboard via xsel."""
    try:
        r = subprocess.run(
            ["xsel", "--clipboard", "--output"],
            capture_output=True, text=True, timeout=2,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception as e:
        log.debug("xsel read failed: %s", e)
        return ""


def xsel_set(text: str) -> None:
    """Write text to X11 clipboard via xsel."""
    try:
        subprocess.run(
            ["xsel", "--clipboard", "--input"],
            input=text, text=True, timeout=2, check=True,
        )
    except Exception as e:
        log.warning("xsel write failed: %s", e)


async def poll_clipboard():
    """Poll X11 clipboard and broadcast changes to all connected clients."""
    global last_clipboard
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        current = await asyncio.to_thread(xsel_get)
        if current and current != last_clipboard:
            last_clipboard = current
            msg = json.dumps({"type": "clipboard", "text": current})
            if clients:
                await asyncio.gather(
                    *(c.send(msg) for c in clients.copy()),
                    return_exceptions=True,
                )


async def handler(ws):
    """Handle a single WebSocket client."""
    global last_clipboard
    clients.add(ws)
    log.info("Client connected (%d total)", len(clients))
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
                if msg.get("type") == "set" and "text" in msg:
                    text = msg["text"]
                    last_clipboard = text
                    await asyncio.to_thread(xsel_set, text)
                    log.info("Clipboard set from browser (%d chars)", len(text))
                elif msg.get("type") == "get":
                    current = await asyncio.to_thread(xsel_get)
                    await ws.send(json.dumps({"type": "clipboard", "text": current}))
            except json.JSONDecodeError:
                pass
    except websockets.ConnectionClosed:
        pass
    finally:
        clients.discard(ws)
        log.info("Client disconnected (%d remaining)", len(clients))


async def main():
    log.info("Starting clipboard bridge on port %d", PORT)
    async with websockets.serve(handler, "0.0.0.0", PORT):
        await poll_clipboard()


if __name__ == "__main__":
    asyncio.run(main())
