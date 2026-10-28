"""Where Prax keeps the record of what it did: out of the agent's reach.

Monitors, audits and the user reconstruct what happened from these:

- the per-user trace log (every turn: input, tool calls, results, answer, and
  the governance audit) and its rotated archive;
- the execution graphs behind ``trace_search``, ``trace_detail`` and
  TeamWork's graph view;
- task trajectories;
- the parked approvals (what runs once a person says yes);
- user feedback on answers.

They used to live inside the workspace directory, which the sandbox container
mounts read-write (production mounts every user's workspace at ``/workspace``).
So one ``sandbox_shell`` call (``rm``, a truncation, an edit) could delete or
rewrite them, and the paper this answers ("LLM Agents Can Easily Tamper With
Their Own Traces", arXiv 2609.30266) found agents do exactly that when asked,
when a planted instruction tells them to, and unprompted when it raises their
score. They now live under ``RECORDS_DIR``, by default a ``records/``
directory next to the workspace directory: never inside it, so no sandbox
mount and no workspace file tool reaches it. Existing files move there the
first time each is used.

This holds against the agent's tools. It does not hold against code running on
the host as Prax's user (a plugin subprocess, a compromised Prax), which can
still write here. Against that, every write is journaled in a hash chain whose
head is anchored in the system journal under systemd
(``prax/services/record_chain.py``), so a change made outside Prax is evident,
not prevented; and the secrets proxy's hash-chained wire record is kept
outside Prax altogether. See
``docs/security/trace-integrity.md``.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_adopted: set[tuple[str, str]] = set()
_adopt_lock = threading.Lock()
_warned_inside = False
# Old locations already moved (or found empty), kept in RECORDS_DIR so a move
# happens once per deployment, not once per process: the old locations are in
# the workspace the sandbox writes to, and a file planted there after the move
# must not become a record at the next restart.
LEGACY_MARKER = ".legacy-moved.json"


def _workspace_dir() -> Path:
    from prax.settings import settings
    return Path(settings.workspace_dir).resolve()


def records_root() -> Path:
    """``RECORDS_DIR``, or ``records/`` beside the workspace directory."""
    from prax.settings import settings
    configured = (getattr(settings, "records_dir", "") or "").strip()
    root = Path(configured).resolve() if configured else _workspace_dir().parent / "records"
    _warn_if_inside_workspace(root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _warn_if_inside_workspace(root: Path) -> None:
    global _warned_inside
    ws = _workspace_dir()
    if not _warned_inside and (root == ws or ws in root.parents):
        _warned_inside = True
        logger.warning(
            "RECORDS_DIR (%s) is inside the workspace directory (%s), which the sandbox "
            "mounts: the agent can delete or rewrite its own records there. Set RECORDS_DIR "
            "to a directory outside it.", root, ws)


def _legacy_done(root: Path) -> dict:
    try:
        return json.loads((root / LEGACY_MARKER).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.error("records: %s is unreadable; not moving anything from old locations",
                     root / LEGACY_MARKER, exc_info=True)
        return {"*": "unreadable"}


def _mark_legacy_done(root: Path, old: Path) -> None:
    from datetime import UTC, datetime

    from prax.services import record_chain
    done = _legacy_done(root)
    done.pop("*", None)
    done[str(old)] = datetime.now(UTC).isoformat(timespec="seconds")
    record_chain.write(root / LEGACY_MARKER,
                       (json.dumps(done, indent=1, sort_keys=True) + "\n").encode("utf-8"),
                       note="legacy location checked")


def _adopt(old: Path, new: Path) -> None:
    """Move a record from where it used to live, once per deployment. Never
    overwrites. What moves in is journaled as it arrived
    (``record_chain.adopt``). Once an old location has been checked, moved or
    found empty, it is never looked at again (``LEGACY_MARKER``)."""
    key = (str(old), str(new))
    if key in _adopted:
        return
    with _adopt_lock:
        if key in _adopted:
            return
        root = records_root()
        done = _legacy_done(root)
        if str(old) in done or "*" in done:
            if old.exists() and str(old) in done:
                logger.warning("records: %s appeared at an old location after the move; "
                               "it is not a record and was left where it is", old)
            _adopted.add(key)
            return
        moved: list[Path] = []
        failed = False
        try:
            if old.is_dir():
                new.mkdir(parents=True, exist_ok=True)
                for child in old.iterdir():
                    target = new / child.name
                    if not target.exists():
                        shutil.move(str(child), str(target))
                        moved.append(target)
                try:
                    old.rmdir()
                except OSError:
                    pass  # something with the same name was already in the new place: keep both
            elif old.is_file() and not new.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(new))
                moved.append(new)
                logger.info("records: moved %s to %s", old, new)
        except Exception:
            failed = True
            logger.warning("records: could not move %s to %s", old, new, exc_info=True)
        if not failed:
            try:
                _mark_legacy_done(root, old)
            except Exception:
                logger.error("records: could not record that %s was moved", old, exc_info=True)
        if moved:
            from prax.services import record_chain
            for path in moved:
                try:
                    record_chain.adopt(path, source="legacy")
                except Exception:
                    logger.error("records: could not journal %s", path, exc_info=True)
        _adopted.add(key)


def shared_dir(name: str, *, legacy: Path | None = None) -> Path:
    """A records directory shared by all users (execution graphs, feedback)."""
    d = records_root() / name
    if legacy is not None:
        _adopt(legacy, d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def shared_file(name: str, *, legacy: Path | None = None) -> Path:
    p = records_root() / name
    if legacy is not None:
        _adopt(legacy, p)
    return p


def user_dir(user_id: str) -> Path:
    """One user's records. Keyed by the workspace directory's real name, so
    the identities that share a workspace (a phone number and a TeamWork id
    symlinked to it) share one record, as they shared one trace log."""
    from prax.services.workspace_service import workspace_root
    name = Path(workspace_root(user_id)).resolve().name
    d = records_root() / "users" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_path(user_id: str, name: str, *, legacy: str | None = None) -> Path:
    """``name`` in the user's records; ``legacy`` is where it lived under the
    user's workspace before (relative), moved here the first time."""
    p = user_dir(user_id) / name
    if legacy is not None:
        from prax.services.workspace_service import workspace_root
        _adopt(Path(workspace_root(user_id)) / legacy, p)
    return p


def graphs_dir() -> Path:
    """Execution graphs (``graphs-YYYY-MM-DD.jsonl``); were ``workspace_dir/.prax/graphs``."""
    return shared_dir("graphs", legacy=_workspace_dir() / ".prax" / "graphs")


def _reset_for_tests() -> None:
    global _warned_inside
    _adopted.clear()
    _warned_inside = False
    from prax.services import record_chain
    record_chain._reset_for_tests()
