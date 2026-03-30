"""CDP (Chrome DevTools Protocol) service — talks to the sandbox Chrome instance.

This connects to the same headless Chrome the user sees via the TeamWork
browser panel.  Prax uses this for reading page content, taking screenshots,
and controlling the browser when the user hasn't taken over.

Uses only stdlib (urllib + socket) so no extra dependencies are needed.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import time
import urllib.request
from base64 import b64encode
from threading import Lock

logger = logging.getLogger(__name__)

CDP_HOST = os.getenv("SANDBOX_HOST", "sandbox")
CDP_PORT = int(os.getenv("CDP_PORT", "9223"))

_lock = Lock()
_msg_counter = 0


# ---------------------------------------------------------------------------
# Minimal WebSocket client (RFC 6455) — just enough for CDP request/response
# ---------------------------------------------------------------------------

def _ws_connect(url: str, timeout: float = 10) -> socket.socket:
    """Open a WebSocket connection using raw sockets."""
    # Parse ws://host:port/path
    assert url.startswith("ws://")
    rest = url[5:]
    host_port, path = rest.split("/", 1)
    path = "/" + path
    if ":" in host_port:
        host, port = host_port.split(":")
        port = int(port)
    else:
        host, port = host_port, 80

    sock = socket.create_connection((host, port), timeout=timeout)
    # WebSocket upgrade handshake
    key = b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{CDP_PORT}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(handshake.encode())

    # Read response headers
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("WebSocket handshake failed — no response")
        buf += chunk

    if b"101" not in buf.split(b"\r\n")[0]:
        raise ConnectionError(f"WebSocket handshake rejected: {buf[:200]}")

    return sock


def _ws_send(sock: socket.socket, data: str) -> None:
    """Send a WebSocket text frame (masked, as client)."""
    payload = data.encode("utf-8")
    frame = bytearray()
    frame.append(0x81)  # FIN + text opcode

    length = len(payload)
    if length < 126:
        frame.append(0x80 | length)  # MASK bit set
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack("!H", length))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack("!Q", length))

    mask = os.urandom(4)
    frame.extend(mask)
    frame.extend(bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
    sock.sendall(frame)


def _ws_recv(sock: socket.socket) -> str:
    """Receive a WebSocket text frame."""
    def _read_exact(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("WebSocket connection closed")
            buf += chunk
        return buf

    header = _read_exact(2)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F

    if length == 126:
        length = struct.unpack("!H", _read_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(8))[0]

    if masked:
        mask = _read_exact(4)
        data = _read_exact(length)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    else:
        data = _read_exact(length)

    if opcode == 0x08:  # close
        raise ConnectionError("WebSocket closed by server")
    if opcode == 0x01:  # text
        return data.decode("utf-8")
    # For ping/pong/continuation, just return empty and let caller retry
    return ""


# ---------------------------------------------------------------------------
# CDP protocol helpers
# ---------------------------------------------------------------------------

def _cdp_http(path: str, timeout: float = 5) -> dict | list | None:
    """Make an HTTP request to the CDP proxy."""
    url = f"http://{CDP_HOST}:{CDP_PORT}{path}"
    req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{CDP_PORT}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning("CDP HTTP failed (%s): %s", path, e)
        return None


def _discover_ws_url() -> str | None:
    """Find the WebSocket debugger URL for the most relevant page target.

    Picks the *last* page target in the list — Chrome appends new tabs/popups
    at the end, so this naturally targets auth popups and modal windows that
    open in a new tab.  When the popup closes, the next call falls back to
    the original tab.
    """
    targets = _cdp_http("/json")
    if not targets or not isinstance(targets, list):
        return None
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        return None
    # Prefer the last (most recently opened) page target
    page = pages[-1]
    ws_url: str = page["webSocketDebuggerUrl"]
    ws_url = ws_url.replace("localhost", CDP_HOST)
    ws_url = ws_url.replace("127.0.0.1", CDP_HOST)
    ws_url = ws_url.replace(":9222/", f":{CDP_PORT}/")
    return ws_url


def _send_cdp(method: str, params: dict | None = None, timeout: float = 10) -> dict:
    """Send a CDP command and wait for the response."""
    global _msg_counter

    ws_url = _discover_ws_url()
    if not ws_url:
        return {"error": "Chrome not reachable — no page target found"}

    _msg_counter += 1
    msg_id = _msg_counter
    payload: dict = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params

    try:
        sock = _ws_connect(ws_url, timeout=timeout)
        sock.settimeout(timeout)
        _ws_send(sock, json.dumps(payload))

        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = _ws_recv(sock)
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("id") == msg_id:
                sock.close()
                if "error" in data:
                    return {"error": data["error"].get("message", str(data["error"]))}
                return data.get("result", {})
        sock.close()
        return {"error": "CDP command timed out"}
    except Exception as e:
        return {"error": f"CDP command failed: {e}"}


# ---------------------------------------------------------------------------
# Public API — called by Prax tools
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Check if Chrome is reachable."""
    info = _cdp_http("/json/version")
    return info is not None and isinstance(info, dict)


def get_page_text(max_length: int = 30_000) -> dict:
    """Get the visible text content of the current page."""
    with _lock:
        result = _send_cdp("Runtime.evaluate", {
            "expression": "document.body?.innerText || ''",
            "returnByValue": True,
        })
    if "error" in result:
        return result

    text = result.get("result", {}).get("value", "")
    if len(text) > max_length:
        text = text[:max_length] + "\n\n[Content truncated]"

    url_result = _send_cdp("Runtime.evaluate", {
        "expression": "document.location.href",
        "returnByValue": True,
    })
    title_result = _send_cdp("Runtime.evaluate", {
        "expression": "document.title",
        "returnByValue": True,
    })
    url = url_result.get("result", {}).get("value", "unknown")
    title = title_result.get("result", {}).get("value", "Untitled")

    return {"text": text, "url": url, "title": title}


def get_page_url() -> dict:
    """Get the current page URL and title."""
    result = _send_cdp("Runtime.evaluate", {
        "expression": "JSON.stringify({url: location.href, title: document.title})",
        "returnByValue": True,
    })
    if "error" in result:
        return result
    try:
        return json.loads(result.get("result", {}).get("value", "{}"))
    except (json.JSONDecodeError, TypeError):
        return {"url": "unknown", "title": "unknown"}


def navigate(url: str) -> dict:
    """Navigate to a URL and wait for the page to finish loading.

    Uses DOM readyState polling instead of a fixed sleep so fast pages
    return immediately and slow pages (HN discussion threads, JS-heavy
    sites) get enough time to render.
    """
    with _lock:
        result = _send_cdp("Page.navigate", {"url": url}, timeout=15)
    if "error" in result:
        return result

    # Poll readyState until "complete" (max ~8s).
    deadline = time.time() + 8
    while time.time() < deadline:
        time.sleep(0.5)
        rs = _send_cdp("Runtime.evaluate", {
            "expression": "document.readyState",
            "returnByValue": True,
        })
        state = rs.get("result", {}).get("value", "")
        if state == "complete":
            break

    # Extra settle time for JS-rendered content.
    time.sleep(1)
    return get_page_text(max_length=5000)


def screenshot() -> dict:
    """Take a screenshot and return base64 JPEG data."""
    with _lock:
        result = _send_cdp("Page.captureScreenshot", {
            "format": "jpeg",
            "quality": 80,
        })
    if "error" in result:
        return result
    b64 = result.get("data", "")
    if not b64:
        return {"error": "No screenshot data returned"}

    # Save to temp file so tools can reference it
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"cdp_screenshot_{int(time.time())}.jpg")
    import base64
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))
    return {"path": path, "format": "jpeg"}


def click_element(selector: str) -> dict:
    """Click an element by CSS selector."""
    with _lock:
        result = _send_cdp("Runtime.evaluate", {
            "expression": f"""
                (() => {{
                    const el = document.querySelector({json.dumps(selector)});
                    if (!el) return JSON.stringify({{error: 'Element not found: {selector}'}});
                    const rect = el.getBoundingClientRect();
                    return JSON.stringify({{
                        x: Math.round(rect.x + rect.width / 2),
                        y: Math.round(rect.y + rect.height / 2),
                    }});
                }})()
            """,
            "returnByValue": True,
        })
    if "error" in result:
        return result

    try:
        pos = json.loads(result.get("result", {}).get("value", "{}"))
    except (json.JSONDecodeError, TypeError):
        return {"error": "Failed to parse element position"}
    if "error" in pos:
        return pos
    if "x" not in pos or "y" not in pos:
        return {"error": f"Element not found or not visible for selector: {selector}"}

    return click_at(pos["x"], pos["y"])


def click_text(text: str) -> dict:
    """Click an element by its visible text content.

    Searches all clickable elements (a, button, input, [role=button], etc.)
    for one whose visible text contains the given string (case-insensitive).
    """
    js = f"""
        (() => {{
            const target = {json.dumps(text)}.toLowerCase();
            // Walk ALL elements — modern SPAs use custom tags, roles, etc.
            const all = document.querySelectorAll('*');
            let best = null;
            let bestLen = Infinity;
            for (const el of all) {{
                // Skip non-leaf containers with too much text (body, main, etc.)
                const t = (el.innerText || el.textContent || el.value || '').trim();
                if (!t || t.length > 500) continue;
                if (t.toLowerCase().includes(target) && t.length < bestLen) {{
                    const rect = el.getBoundingClientRect();
                    // Must be visible and have size
                    if (rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0) {{
                        best = {{ x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2), matched: t.substring(0, 80) }};
                        bestLen = t.length;
                    }}
                }}
            }}
            if (!best) return JSON.stringify({{ error: 'No visible element with text: ' + {json.dumps(text)} }});
            return JSON.stringify(best);
        }})()
    """
    with _lock:
        result = _send_cdp("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
        })
    if "error" in result:
        return result

    try:
        pos = json.loads(result.get("result", {}).get("value", "{}"))
    except (json.JSONDecodeError, TypeError):
        return {"error": "Failed to parse element position"}
    if "error" in pos:
        return pos
    if "x" not in pos or "y" not in pos:
        return {"error": f"No clickable element found with text: {text}"}

    matched = pos.get("matched", text)
    click_result = click_at(pos["x"], pos["y"])
    if "error" in click_result:
        return click_result
    return {"status": f"Clicked '{matched}' at ({pos['x']}, {pos['y']})"}


def click_at(x: int, y: int) -> dict:
    """Click at absolute coordinates."""
    with _lock:
        _send_cdp("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        time.sleep(0.05)
        _send_cdp("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
    time.sleep(0.5)
    return {"status": f"Clicked at ({x}, {y})"}


def type_text(text: str) -> dict:
    """Type text character by character."""
    with _lock:
        for ch in text:
            _send_cdp("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": ch, "text": "",
            })
            _send_cdp("Input.dispatchKeyEvent", {
                "type": "char", "key": ch, "text": ch,
            })
            _send_cdp("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": ch, "text": "",
            })
            time.sleep(0.03)
    return {"status": f"Typed {len(text)} characters"}


def press_key(key: str) -> dict:
    """Press a special key (Enter, Tab, Escape, Backspace, etc.)."""
    key_map = {
        "Enter": 13, "Tab": 9, "Escape": 27, "Backspace": 8,
        "ArrowDown": 40, "ArrowUp": 38, "ArrowLeft": 37, "ArrowRight": 39,
    }
    kc = key_map.get(key, 0)
    with _lock:
        _send_cdp("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": key, "code": key,
            "windowsVirtualKeyCode": kc, "text": "",
        })
        _send_cdp("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": key, "code": key,
            "windowsVirtualKeyCode": kc, "text": "",
        })
    return {"status": f"Pressed {key}"}


def scroll_page(direction: str = "down", amount: int = 300) -> dict:
    """Scroll the page. direction: 'up' or 'down'."""
    delta = amount if direction == "down" else -amount
    with _lock:
        result = _send_cdp("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": 640, "y": 450,
            "deltaX": 0, "deltaY": delta,
        })
    if "error" in result:
        return result
    return {"status": f"Scrolled {direction} by {abs(delta)}px"}


def evaluate_js(expression: str) -> dict:
    """Evaluate arbitrary JavaScript in the page context."""
    with _lock:
        result = _send_cdp("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
    if "error" in result:
        return result
    value = result.get("result", {}).get("value")
    return {"value": value}
