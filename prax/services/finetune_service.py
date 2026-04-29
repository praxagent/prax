"""Self-improving fine-tuning service — LoRA training loop with vLLM hot-swap.

Harvests training data from conversation history, runs QLoRA fine-tuning via
Unsloth (as a subprocess), and hot-swaps LoRA adapters into vLLM without
restart.  The entire pipeline is gated behind ``FINETUNE_ENABLED=true``.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from prax.settings import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_training_process: subprocess.Popen | None = None
_training_status: dict[str, Any] = {"state": "idle"}

# Phrases that indicate the user was correcting the agent.
_CORRECTION_SIGNALS = [
    "no,", "no.", "no!", "that's wrong", "that's not", "not what i",
    "try again", "incorrect", "you got it wrong", "that doesn't",
    "actually,", "i meant", "i said", "wrong answer", "not right",
    "nope", "that is wrong", "fix it", "fix that", "redo",
]


# ---------------------------------------------------------------------------
# Guard — everything below is a no-op when the flag is off
# ---------------------------------------------------------------------------

def _enabled() -> bool:
    return settings.finetune_enabled


def _adapters_dir() -> Path:
    return Path(settings.finetune_output_dir)


def _registry_path() -> Path:
    return _adapters_dir() / "adapter_registry.json"


# ---------------------------------------------------------------------------
# Adapter registry (JSON on disk)
# ---------------------------------------------------------------------------

def _read_registry() -> dict:
    path = _registry_path()
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"active_adapter": None, "previous_adapter": None, "adapters": []}


def _write_registry(data: dict) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Training data harvesting
# ---------------------------------------------------------------------------

def _get_all_users() -> list[int]:
    """Return all user phone ints from the conversations DB."""
    from prax.services.state_paths import ensure_conversation_db

    db_path = ensure_conversation_db(database_name=settings.database_name)
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT DISTINCT id FROM conversations").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _get_conversations(phone_int: int) -> list[dict]:
    from prax.services.state_paths import ensure_conversation_db

    db_path = ensure_conversation_db(database_name=settings.database_name)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT data FROM conversations WHERE id = ?", (phone_int,)
        ).fetchone()
        if row:
            return json.loads(row[0])
        return []
    finally:
        conn.close()


def _is_correction(text: str) -> bool:
    lower = text.lower().strip()
    return any(signal in lower for signal in _CORRECTION_SIGNALS)


def harvest_corrections(since_hours: int = 24) -> list[dict]:
    """Scan conversation history for user corrections and build training pairs.

    Returns a list of ChatML-formatted training examples.
    """
    if not _enabled():
        return []

    cutoff = time.time() - (since_hours * 3600)
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
    training_examples = []

    for phone_int in _get_all_users():
        messages = _get_conversations(phone_int)
        for i, msg in enumerate(messages):
            if msg.get("role") != "user":
                continue
            if not _is_correction(msg.get("content", "")):
                continue
            # Check timestamp if available.
            msg_date = msg.get("date", "")
            if msg_date and msg_date < cutoff_iso:
                continue

            # Build correction pair: the messages AFTER the correction
            # (the user's correction + the agent's corrected response)
            # form the training example.
            context = messages[max(0, i - 4): i]  # up to 4 msgs of context
            # Look for the assistant response after the correction.
            corrected_response = None
            for j in range(i + 1, min(i + 3, len(messages))):
                if messages[j].get("role") == "assistant":
                    corrected_response = messages[j]
                    break

            if not corrected_response:
                continue

            example = {"messages": []}
            example["messages"].append({
                "role": "system",
                "content": f"You are {settings.agent_name}, a warm, capable AI assistant.",
            })
            for ctx_msg in context:
                example["messages"].append({
                    "role": ctx_msg["role"],
                    "content": ctx_msg["content"],
                })
            # Use the original user message that triggered the bad response,
            # paired with what the CORRECTED response should have been.
            if i >= 2 and messages[i - 2].get("role") == "user":
                example["messages"].append({
                    "role": "user",
                    "content": messages[i - 2]["content"],
                })
            example["messages"].append({
                "role": "assistant",
                "content": corrected_response["content"],
            })
            training_examples.append(example)

    return training_examples


def save_training_data(examples: list[dict], batch_name: str | None = None) -> str:
    """Save training examples as JSONL.  Returns the file path."""
    output_dir = _adapters_dir() / "training_data"
    output_dir.mkdir(parents=True, exist_ok=True)
    name = batch_name or f"batch_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}"
    path = output_dir / f"{name}.jsonl"
    with open(path, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")
    logger.info("Saved %d training examples to %s", len(examples), path)
    return str(path)


# ---------------------------------------------------------------------------
# Fine-tune execution (subprocess)
# ---------------------------------------------------------------------------

def start_training(data_path: str | None = None) -> dict[str, Any]:
    """Kick off a LoRA fine-tune job as a background subprocess."""
    global _training_process, _training_status

    if not _enabled():
        return {"error": "Fine-tuning is disabled (FINETUNE_ENABLED=false)"}

    with _lock:
        if _training_process and _training_process.poll() is None:
            return {"error": "A training job is already running"}

    adapter_name = f"adapter_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}"
    output_path = str(_adapters_dir() / adapter_name)

    # If no explicit data path, harvest fresh corrections.
    if not data_path:
        examples = harvest_corrections()
        if not examples:
            return {"error": "No training data found (no corrections in recent conversations)"}
        data_path = save_training_data(examples, adapter_name)

    script = str(Path(__file__).resolve().parent.parent.parent / "scripts" / "finetune_train.py")
    cmd = [
        "python", script,
        "--base-model", settings.finetune_base_model,
        "--data", data_path,
        "--output", output_path,
        "--max-steps", str(settings.finetune_max_steps),
        "--learning-rate", str(settings.finetune_learning_rate),
        "--lora-rank", str(settings.finetune_lora_rank),
    ]

    status_file = str(_adapters_dir() / f"{adapter_name}_status.json")

    with _lock:
        _training_status = {
            "state": "running",
            "adapter_name": adapter_name,
            "data_path": data_path,
            "output_path": output_path,
            "status_file": status_file,
            "started_at": datetime.now(tz=UTC).isoformat(),
        }
        env = os.environ.copy()
        env["FINETUNE_STATUS_FILE"] = status_file
        try:
            _training_process = subprocess.Popen(
                cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            _training_status = {"state": "idle"}
            return {"error": f"Training script not found at {script}"}

    logger.info("Started fine-tune job: %s (pid=%d)", adapter_name, _training_process.pid)
    return {
        "status": "started",
        "adapter_name": adapter_name,
        "data_path": data_path,
        "pid": _training_process.pid,
    }


def get_training_status() -> dict[str, Any]:
    """Check the current fine-tune job status."""
    global _training_process, _training_status

    if not _enabled():
        return {"state": "disabled"}

    with _lock:
        if _training_process is None:
            return {"state": "idle"}

        rc = _training_process.poll()
        if rc is None:
            # Still running — check status file for progress.
            status_file = _training_status.get("status_file")
            if status_file and os.path.exists(status_file):
                with open(status_file) as f:
                    progress = json.load(f)
                return {**_training_status, **progress}
            return _training_status

        # Process finished.
        _training_status["state"] = "completed" if rc == 0 else "failed"
        _training_status["return_code"] = rc
        if rc != 0 and _training_process.stderr:
            _training_status["error"] = _training_process.stderr.read().decode()[-500:]
        _training_process = None
        return _training_status


# ---------------------------------------------------------------------------
# vLLM adapter management
# ---------------------------------------------------------------------------

def _vllm_url(path: str) -> str:
    base = settings.vllm_base_url.rstrip("/")
    return f"{base}{path}"


def load_adapter(adapter_name: str, adapter_path: str | None = None) -> dict[str, Any]:
    """Load a LoRA adapter into vLLM via its REST API."""
    if not _enabled():
        return {"error": "Fine-tuning is disabled"}

    path = adapter_path or str(_adapters_dir() / adapter_name)
    if not os.path.isdir(path):
        return {"error": f"Adapter directory not found: {path}"}

    try:
        r = requests.post(
            _vllm_url("/v1/load_lora_adapter"),
            json={"lora_name": adapter_name, "lora_path": path},
            timeout=30,
        )
        r.raise_for_status()
    except requests.ConnectionError:
        return {"error": "Cannot connect to vLLM server — is it running?"}
    except requests.HTTPError as e:
        return {"error": f"vLLM rejected adapter load: {e}"}

    logger.info("Loaded adapter %s into vLLM", adapter_name)
    return {"status": "loaded", "adapter_name": adapter_name}


def unload_adapter(adapter_name: str) -> dict[str, Any]:
    """Unload a LoRA adapter from vLLM."""
    if not _enabled():
        return {"error": "Fine-tuning is disabled"}

    try:
        r = requests.post(
            _vllm_url("/v1/unload_lora_adapter"),
            json={"lora_name": adapter_name},
            timeout=30,
        )
        r.raise_for_status()
    except requests.ConnectionError:
        return {"error": "Cannot connect to vLLM server"}
    except requests.HTTPError as e:
        return {"error": f"vLLM rejected adapter unload: {e}"}

    logger.info("Unloaded adapter %s from vLLM", adapter_name)
    return {"status": "unloaded", "adapter_name": adapter_name}


def list_adapters() -> list[dict]:
    """List all available adapters from the registry."""
    registry = _read_registry()
    return registry.get("adapters", [])


def verify_adapter(adapter_name: str, test_cases: list[dict] | None = None) -> dict[str, Any]:
    """Run test prompts through an adapter and check quality.

    Each test case: {"prompt": str, "expected_contains": str | list[str]}
    """
    if not _enabled():
        return {"error": "Fine-tuning is disabled"}

    if not test_cases:
        test_cases = [
            {"prompt": "What is 2 + 2?", "expected_contains": ["4"]},
            {"prompt": "Say hello in French", "expected_contains": ["bonjour"]},
        ]

    passed = 0
    failed = 0
    results = []

    for tc in test_cases:
        try:
            r = requests.post(
                _vllm_url("/v1/chat/completions"),
                json={
                    "model": adapter_name,
                    "messages": [{"role": "user", "content": tc["prompt"]}],
                    "max_tokens": 200,
                },
                timeout=30,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].lower()
            expected = tc["expected_contains"]
            if isinstance(expected, str):
                expected = [expected]
            ok = any(e.lower() in content for e in expected)
            if ok:
                passed += 1
            else:
                failed += 1
            results.append({"prompt": tc["prompt"], "passed": ok, "response": content[:200]})
        except Exception as e:
            failed += 1
            results.append({"prompt": tc["prompt"], "passed": False, "error": str(e)})

    verdict = "pass" if failed == 0 else "fail"
    return {
        "adapter_name": adapter_name,
        "verdict": verdict,
        "passed": passed,
        "failed": failed,
        "results": results,
    }


def promote_adapter(adapter_name: str) -> dict[str, Any]:
    """Mark an adapter as the active default and update the registry."""
    if not _enabled():
        return {"error": "Fine-tuning is disabled"}

    registry = _read_registry()
    registry["previous_adapter"] = registry.get("active_adapter")
    registry["active_adapter"] = adapter_name

    # Ensure the adapter is in the list.
    existing = {a["name"] for a in registry["adapters"]}
    if adapter_name not in existing:
        registry["adapters"].append({
            "name": adapter_name,
            "path": str(_adapters_dir() / adapter_name),
            "created_at": datetime.now(tz=UTC).isoformat(),
            "verified": True,
        })

    _write_registry(registry)
    logger.info("Promoted adapter %s as active", adapter_name)
    return {"status": "promoted", "active_adapter": adapter_name}


def rollback_adapter() -> dict[str, Any]:
    """Roll back to the previous adapter."""
    if not _enabled():
        return {"error": "Fine-tuning is disabled"}

    registry = _read_registry()
    prev = registry.get("previous_adapter")
    if not prev:
        return {"error": "No previous adapter to roll back to"}

    current = registry.get("active_adapter")

    # Unload current, load previous.
    if current:
        unload_adapter(current)
    result = load_adapter(prev)
    if "error" in result:
        return result

    registry["active_adapter"] = prev
    registry["previous_adapter"] = None
    _write_registry(registry)

    logger.info("Rolled back from %s to %s", current, prev)
    return {"status": "rolled_back", "active_adapter": prev, "previous_adapter": current}


def get_active_adapter() -> str | None:
    """Return the name of the currently promoted adapter, or None."""
    registry = _read_registry()
    return registry.get("active_adapter")


# ---------------------------------------------------------------------------
# Full self-improvement pipeline (called from scheduler)
# ---------------------------------------------------------------------------

def run_self_improvement_cycle() -> dict[str, Any]:
    """Run the complete harvest → train → verify → promote cycle.

    Designed to be called from a scheduled cron job.
    """
    if not _enabled():
        return {"error": "Fine-tuning is disabled"}

    # 1. Harvest
    examples = harvest_corrections(since_hours=24)
    if not examples:
        return {"status": "skipped", "reason": "No corrections found in last 24h"}

    # 2. Save training data
    data_path = save_training_data(examples)

    # 3. Train (blocking — this is called from a background scheduler)
    result = start_training(data_path)
    if "error" in result:
        return result

    # Wait for training to complete (poll every 10s, max 1h).
    for _ in range(360):
        status = get_training_status()
        if status.get("state") in ("completed", "failed"):
            break
        time.sleep(10)
    else:
        return {"error": "Training timed out after 1 hour"}

    if status.get("state") != "completed":
        return {"error": f"Training failed: {status.get('error', 'unknown')}"}

    adapter_name = status["adapter_name"]

    # 4. Load + verify
    load_result = load_adapter(adapter_name)
    if "error" in load_result:
        return load_result

    verify_result = verify_adapter(adapter_name)
    if verify_result["verdict"] != "pass":
        unload_adapter(adapter_name)
        return {"status": "rejected", "reason": "Verification failed", "details": verify_result}

    # 5. Promote
    promote_adapter(adapter_name)

    return {
        "status": "improved",
        "adapter_name": adapter_name,
        "training_samples": len(examples),
        "verification": verify_result,
    }
