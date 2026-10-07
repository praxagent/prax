"""Parked approvals: an unattended run waits for a person instead of giving up.

Pattern credit: OpenWorker (Andrew Ng and contributors,
https://github.com/andrewyng/openworker) — unattended runs "never
self-approve; requests park in an inbox".

A scheduled or task-runner turn that reaches an action needing a person used
to wait ``APPROVAL_WAIT_SECONDS`` for someone who wasn't there, be refused,
and lose the work. With ``PARKED_APPROVALS_ENABLED`` it instead:

1. creates the TeamWork request with a long lifetime
   (``PARKED_APPROVAL_HOURS``), records here what would re-run the task — the
   *recipe* — and which exact action is waiting, and ends the turn saying so;
2. a background poller watches the request. On approval it re-runs the task
   with that approval attached to that exact action, spent once when the
   re-run reaches it. On refusal or expiry it tells the user the task was
   not done.

Nothing here decides anything: the person decides in TeamWork. A re-run that
reaches a *different* action asks again (and may park again, up to
``PARKED_MAX_RESUMES`` times per task).

The store is one JSON file under the workspace root, so parked requests
survive a restart.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

POLL_SECONDS = 30.0

# What would re-run the current turn, set by the scheduler / task runner around
# an unattended turn: {"kind": "schedule"|"task_pickup", "args": {...}, "resumes": n}.
current_recipe: ContextVar[dict | None] = ContextVar("prax_parked_recipe", default=None)
# Approvals a person already gave for this re-run: action key -> {"approval_id", "decided_by"}.
current_preapproved: ContextVar[dict | None] = ContextVar("prax_parked_preapproved", default=None)

_lock = threading.Lock()
_thread: threading.Thread | None = None


def enabled() -> bool:
    try:
        from prax.settings import settings
        return bool(getattr(settings, "parked_approvals_enabled", False))
    except Exception:
        return False


def action_key(capability: str, payload: dict) -> str:
    canonical = json.dumps({"c": capability, "p": payload}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- store -------------------------------------------------------------------

def _path() -> Path:
    """In the records directory: what runs once a person approves must not be
    editable from the sandbox, which mounts the workspace directory."""
    from prax.services import records
    from prax.settings import settings
    return records.shared_file(
        "parked_approvals.json",
        legacy=Path(settings.workspace_dir).resolve() / ".prax" / "parked_approvals.json")


def _load() -> list[dict]:
    try:
        return json.loads(_path().read_text())
    except FileNotFoundError:
        return []
    except Exception:
        logger.warning("parked approvals store unreadable; starting empty", exc_info=True)
        return []


def _save(entries: list[dict]) -> None:
    """Replace the store atomically; journaled (``prax/services/record_chain.py``),
    so an edit made outside Prax is evident."""
    from prax.services import record_chain
    record_chain.write(_path(), json.dumps(entries, indent=1).encode("utf-8"))


def pending() -> list[dict]:
    with _lock:
        return list(_load())


# --- parking ------------------------------------------------------------------

def park(*, user_id: str, approval_id: str, tool_name: str, capability: str,
         payload: dict, recipe: dict, channel: str | None, expires_at: float) -> dict:
    entry = {
        "id": uuid.uuid4().hex[:12], "user_id": user_id, "approval_id": approval_id,
        "tool": tool_name, "capability": capability, "payload": payload,
        "key": action_key(capability, payload), "recipe": recipe,
        "channel": channel, "parked_at": time.time(), "expires_at": expires_at,
    }
    with _lock:
        entries = [e for e in _load() if e["approval_id"] != approval_id]
        entries.append(entry)
        _save(entries)
    logger.info("Parked %s for %s until a person decides (approval %s)",
                tool_name, user_id, approval_id)
    return entry


def parked_message(tool_name: str, hours: float) -> str:
    return (
        f"PARKED — {tool_name} needs the user's approval, and this run is unattended. "
        f"The request is waiting in TeamWork for up to {hours:g} h; the task will run "
        "again by itself once they approve it. Do not retry it or work around it. "
        "Finish your reply by saying the task is waiting on their approval for this action."
    )


# --- resuming ------------------------------------------------------------------

def _notify(entry: dict, text: str) -> None:
    try:
        from prax.services.scheduler_service import _deliver_message
        _deliver_message(entry["user_id"], text, channel=entry.get("channel"))
    except Exception:
        logger.warning("could not tell the user about parked approval %s", entry["approval_id"],
                       exc_info=True)


def _resume(entry: dict, decided_by: str) -> None:
    recipe = dict(entry["recipe"])
    recipe["resumes"] = int(recipe.get("resumes", 0)) + 1
    preapproved = {entry["key"]: {"approval_id": entry["approval_id"], "decided_by": decided_by}}
    kind, args = recipe.get("kind"), recipe.get("args") or {}

    def run() -> None:
        from prax.agent.user_context import current_user_id

        uid_token = current_user_id.set(entry["user_id"])
        rec_token = current_recipe.set(recipe)
        pre_token = current_preapproved.set(preapproved)
        try:
            if kind == "schedule":
                from prax.services.scheduler_service import _on_fire
                _on_fire(entry["user_id"], args["schedule_id"], args["prompt"],
                         args.get("channel"))
            elif kind == "task_pickup":
                from prax.services.task_runner_service import _run_pickup
                _run_pickup(entry["user_id"], args["pickup"])
            else:
                logger.warning("parked approval %s has unknown recipe %r", entry["approval_id"], kind)
        except Exception:
            logger.exception("resuming parked approval %s failed", entry["approval_id"])
        finally:
            current_preapproved.reset(pre_token)
            current_recipe.reset(rec_token)
            current_user_id.reset(uid_token)

    threading.Thread(target=run, name=f"prax-parked-{entry['approval_id'][:8]}",
                     daemon=True).start()


def tick() -> None:
    """One pass: act on every parked request a person has decided or that expired."""
    from prax.services.teamwork_service import get_teamwork_client

    client = get_teamwork_client()
    if not client.enabled:
        return
    with _lock:
        entries = _load()
    keep: list[dict] = []
    for entry in entries:
        try:
            status = client.approval_status(entry["approval_id"])
        except Exception as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (403, 404):  # gone or not ours: nothing left to wait for
                _notify(entry, f"⏹ {entry['tool']} was not done: its approval request is gone.")
                continue
            keep.append(entry)      # TeamWork unreachable: try again next tick
            continue
        state = status.get("status")
        if state == "approved":
            _resume(entry, str(status.get("decided_by") or ""))
        elif state in ("rejected", "consumed"):
            _notify(entry, f"⏹ {entry['tool']} was not done: the approval was declined.")
        elif status.get("expired") or time.time() > entry["expires_at"]:
            _notify(entry, f"⏹ {entry['tool']} was not done: nobody approved it before the "
                           "request expired. Run the task again if you still want it.")
        else:
            keep.append(entry)
    with _lock:
        # Keep entries parked by other threads while we were polling.
        current = {e["id"] for e in keep}
        fresh = [e for e in _load() if e["id"] not in {x["id"] for x in entries}]
        _save(keep + [e for e in fresh if e["id"] not in current])


def start() -> bool:
    global _thread
    if not enabled() or (_thread is not None and _thread.is_alive()):
        return False

    def loop() -> None:
        while True:
            try:
                tick()
            except Exception:
                logger.exception("parked approvals tick failed")
            time.sleep(POLL_SECONDS)

    _thread = threading.Thread(target=loop, name="prax-parked-approvals", daemon=True)
    _thread.start()
    logger.info("Parked approvals: watching for decisions every %ss", int(POLL_SECONDS))
    return True


def recipe_snapshot() -> dict[str, Any]:
    """The context a governed tool must carry into worker threads and spokes."""
    return {"recipe": current_recipe.get(), "preapproved": current_preapproved.get()}
