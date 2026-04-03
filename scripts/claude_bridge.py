#!/usr/bin/env python3
"""Claude Code Bridge — HTTP server that gives Prax conversational access to Claude Code.

Runs on the HOST machine (not in Docker). Prax calls it from inside Docker
via http://host.docker.internal:<port>/... to maintain multi-turn sessions
with Claude Code working on the live codebase.

Usage:
    ./scripts/start_claude_bridge.sh          # recommended
    uv run python scripts/claude_bridge.py    # direct

Environment:
    CLAUDE_BRIDGE_PORT   — port to listen on (default: 9819)
    CLAUDE_BRIDGE_REPO   — repo path for Claude Code to work in (default: cwd)
    CLAUDE_BRIDGE_SECRET — shared secret for auth (optional but recommended)

The bridge maintains stateful sessions — Prax can have back-and-forth
conversations with Claude Code, not just fire-and-forget prompts.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s %(message)s",
)
logger = logging.getLogger("claude_bridge")

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("CLAUDE_BRIDGE_PORT", "9819"))
REPO_PATH = os.environ.get("CLAUDE_BRIDGE_REPO", str(Path(__file__).resolve().parent.parent))
SHARED_SECRET = os.environ.get("CLAUDE_BRIDGE_SECRET", "")
SESSION_TIMEOUT = 1800  # 30 minutes of inactivity

# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _check_auth():
    """Validate shared secret if configured."""
    if not SHARED_SECRET:
        return None  # No auth required
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token != SHARED_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    return None


def _cleanup_stale_sessions():
    """Remove sessions that have been idle too long."""
    now = time.time()
    with _sessions_lock:
        stale = [
            sid for sid, s in _sessions.items()
            if now - s["last_active"] > SESSION_TIMEOUT
        ]
        for sid in stale:
            logger.info("Cleaning up stale session %s", sid[:8])
            _sessions.pop(sid, None)


# ---------------------------------------------------------------------------
# Claude Code invocation
# ---------------------------------------------------------------------------

def _run_claude(prompt: str, session_id: str | None = None, timeout: int = 300) -> dict:
    """Invoke Claude Code CLI and return the result.

    Uses `claude -p` for single-turn or `claude --resume` for continuing
    a conversation. The session_id maps to a Claude Code conversation.
    """
    cmd = ["claude"]

    if session_id and session_id in _sessions:
        session = _sessions[session_id]
        conv_id = session.get("conversation_id")
        if conv_id:
            cmd.extend(["--resume", conv_id])

    cmd.extend([
        "-p", prompt,
        "--output-format", "json",
        "--max-turns", "50",
    ])

    logger.info(
        "Running Claude Code (session=%s): %s",
        (session_id or "none")[:8], prompt[:100],
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=REPO_PATH,
            env={**os.environ, "CLAUDE_CODE_DISABLE_NONINTERACTIVE_CHECK": "1"},
        )

        # Parse JSON output
        output = result.stdout.strip()
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            data = {"result": output}

        # Extract conversation ID for session continuity
        conv_id = data.get("session_id") or data.get("conversation_id")

        response_text = ""
        if isinstance(data.get("result"), str):
            response_text = data["result"]
        elif isinstance(data.get("result"), list):
            # Claude Code JSON output is a list of content blocks
            for block in data["result"]:
                if isinstance(block, dict) and block.get("type") == "text":
                    response_text += block.get("text", "")
        elif "result" not in data:
            response_text = output

        return {
            "response": response_text,
            "conversation_id": conv_id,
            "exit_code": result.returncode,
            "cost": data.get("cost_usd"),
            "duration_ms": data.get("duration_ms"),
        }

    except subprocess.TimeoutExpired:
        return {"response": "[TIMEOUT] Claude Code did not respond within the time limit.", "exit_code": -1}
    except FileNotFoundError:
        return {"response": "[ERROR] Claude Code CLI not found. Install it first.", "exit_code": -1}
    except Exception as e:
        return {"response": f"[ERROR] {e}", "exit_code": -1}


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    """Health check — also verifies Claude Code CLI is available."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        claude_version = result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        claude_version = "not installed"

    return jsonify({
        "status": "ok",
        "repo_path": REPO_PATH,
        "claude_version": claude_version,
        "active_sessions": len(_sessions),
        "auth_required": bool(SHARED_SECRET),
    })


@app.route("/session/start", methods=["POST"])
def session_start():
    """Start a new conversation session with Claude Code.

    JSON body: {context?: str}
    context: Optional initial context to set up the conversation.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    _cleanup_stale_sessions()

    session_id = uuid.uuid4().hex[:16]
    data = request.get_json(silent=True) or {}
    context = data.get("context", "")

    with _sessions_lock:
        _sessions[session_id] = {
            "created_at": time.time(),
            "last_active": time.time(),
            "conversation_id": None,
            "turn_count": 0,
        }

    # If initial context provided, send it as the first message
    if context:
        result = _run_claude(context, session_id)
        with _sessions_lock:
            if session_id in _sessions:
                _sessions[session_id]["conversation_id"] = result.get("conversation_id")
                _sessions[session_id]["turn_count"] = 1
                _sessions[session_id]["last_active"] = time.time()
        return jsonify({
            "session_id": session_id,
            "response": result["response"],
            "turn": 1,
        }), 201

    return jsonify({"session_id": session_id}), 201


@app.route("/session/message", methods=["POST"])
def session_message():
    """Send a message in an existing session.

    JSON body: {session_id: str, message: str, timeout?: int}
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")
    message = data.get("message", "")
    timeout = data.get("timeout", 300)

    if not session_id or not message:
        return jsonify({"error": "session_id and message are required"}), 400

    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session:
            return jsonify({"error": f"Session {session_id} not found or expired"}), 404

    result = _run_claude(message, session_id, timeout=timeout)

    with _sessions_lock:
        if session_id in _sessions:
            if result.get("conversation_id"):
                _sessions[session_id]["conversation_id"] = result["conversation_id"]
            _sessions[session_id]["turn_count"] += 1
            _sessions[session_id]["last_active"] = time.time()
            turn = _sessions[session_id]["turn_count"]

    return jsonify({
        "session_id": session_id,
        "response": result["response"],
        "turn": turn,
        "exit_code": result.get("exit_code", 0),
        "cost": result.get("cost"),
    })


@app.route("/session/end", methods=["POST"])
def session_end():
    """End a conversation session."""
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")

    with _sessions_lock:
        session = _sessions.pop(session_id, None)

    if not session:
        return jsonify({"error": "Session not found"}), 404

    return jsonify({
        "ended": True,
        "session_id": session_id,
        "turns": session["turn_count"],
    })


@app.route("/ask", methods=["POST"])
def ask():
    """One-shot question — no session management.

    JSON body: {prompt: str, timeout?: int}
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")
    timeout = data.get("timeout", 300)

    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    result = _run_claude(prompt, timeout=timeout)
    return jsonify({
        "response": result["response"],
        "exit_code": result.get("exit_code", 0),
        "cost": result.get("cost"),
    })


@app.route("/sessions", methods=["GET"])
def list_sessions():
    """List active sessions."""
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    _cleanup_stale_sessions()

    with _sessions_lock:
        sessions = [
            {
                "session_id": sid,
                "turn_count": s["turn_count"],
                "idle_seconds": int(time.time() - s["last_active"]),
            }
            for sid, s in _sessions.items()
        ]
    return jsonify({"sessions": sessions})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Claude Code Bridge starting on port %d", PORT)
    logger.info("Repo path: %s", REPO_PATH)
    logger.info("Auth: %s", "enabled" if SHARED_SECRET else "disabled (set CLAUDE_BRIDGE_SECRET)")
    app.run(host="0.0.0.0", port=PORT, debug=False)
