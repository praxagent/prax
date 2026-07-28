#!/usr/bin/env python3
"""Move a workspace off a directory named after a phone number.

The primary user's workspace directory used to be ``PRAX_USER_ID`` verbatim,
which for a phone-signup is the phone number. That put it in the shell prompt,
in every ``cd`` and ``pwd``, in the sandbox mount, and on screen in any demo or
recorded video. A personal phone number is not something a tool should print
because of how someone happened to sign up.

``_canonical_workspace`` now always mints ``usr_<id8>``, so no NEW user can land
in that state. This migrates an existing one.

    python scripts/migrate_workspace_id.py --dry-run     # show the plan
    python scripts/migrate_workspace_id.py --apply       # do it

**Stop Prax and TeamWork first.** Both write into the workspace continuously and
a rename underneath a running process leaves it writing to a deleted inode —
the data is not lost so much as invisible, which is worse.

The new id is derived from the user's existing UUID, so it is stable and
reversible on paper: nothing is regenerated, only renamed and repointed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

HOME = Path.home()
PRAX_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACES = PRAX_ROOT / "workspaces"
IDENTITY_DB = PRAX_ROOT / "prax" / "identity.db"
TEAMWORK_DB = PRAX_ROOT / "teamwork" / "data" / "vteam.db"
PRAX_ENV = PRAX_ROOT / "prax" / ".env"
BACKUP_DIR = HOME / ".prax-migrations"


def _backup_sqlite(src: Path, dest: Path) -> None:
    """Copy a live SQLite DB safely.

    Never ``cp`` these: they run in WAL mode, so a file copy captures the main
    database without the write-ahead log and silently loses recent writes. The
    backup API checkpoints properly.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(src))
    try:
        out = sqlite3.connect(str(dest))
        try:
            con.backup(out)
        finally:
            out.close()
    finally:
        con.close()


def _find_phone_named_user() -> tuple[str, str] | None:
    """Return ``(user_id, workspace_dir)`` for a user whose dir is all digits."""
    if not IDENTITY_DB.exists():
        return None
    con = sqlite3.connect(str(IDENTITY_DB))
    try:
        for uid, wdir in con.execute("SELECT id, workspace_dir FROM users"):
            if wdir and wdir.lstrip("+").isdigit():
                return uid, wdir
    finally:
        con.close()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="perform the migration")
    ap.add_argument("--dry-run", action="store_true", help="show the plan only")
    args = ap.parse_args()
    if not (args.apply or args.dry_run):
        ap.error("choose --dry-run or --apply")

    found = _find_phone_named_user()
    if not found:
        print("Nothing to migrate: no workspace is named after a phone number.")
        return 0

    user_id, old_dir = found
    new_dir = f"usr_{user_id[:8]}"
    old_path = WORKSPACES / old_dir
    new_path = WORKSPACES / new_dir

    print(f"user          {user_id}")
    print(f"workspace     {old_dir}  ->  {new_dir}")
    print(f"path          {old_path}")
    print(f"              {new_path}")

    if not old_path.is_dir():
        print(f"\nERROR: {old_path} does not exist — refusing to guess.")
        return 1
    if new_path.exists():
        print(f"\nERROR: {new_path} already exists — refusing to merge.")
        return 1

    steps = [
        f"rename    {old_path} -> {new_path}",
        f"identity  users.workspace_dir = {new_dir!r} where id = {user_id!r}",
        f"teamwork  projects.workspace_dir = {new_dir!r} where it is {old_dir!r}",
        f"env       PRAX_USER_ID = {new_dir}  (in {PRAX_ENV})",
    ]
    print("\nplan:")
    for s in steps:
        print(f"  - {s}")

    if args.dry_run:
        print("\nDry run — nothing changed.")
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Databases first: if anything later fails, these are the hard-to-rebuild
    # part. The directory can be renamed back by hand; a corrupted identity DB
    # orphans the workspace entirely.
    if IDENTITY_DB.exists():
        _backup_sqlite(IDENTITY_DB, BACKUP_DIR / f"identity-{stamp}.db")
    if TEAMWORK_DB.exists():
        _backup_sqlite(TEAMWORK_DB, BACKUP_DIR / f"vteam-{stamp}.db")
    if PRAX_ENV.exists():
        shutil.copy2(PRAX_ENV, BACKUP_DIR / f"prax-env-{stamp}")
    print(f"\nbacked up to {BACKUP_DIR}")

    old_path.rename(new_path)
    print(f"renamed  {old_dir} -> {new_dir}")

    con = sqlite3.connect(str(IDENTITY_DB))
    try:
        con.execute("UPDATE users SET workspace_dir = ? WHERE id = ?", (new_dir, user_id))
        con.commit()
    finally:
        con.close()
    print("identity.db updated")

    if TEAMWORK_DB.exists():
        con = sqlite3.connect(str(TEAMWORK_DB))
        try:
            con.execute(
                "UPDATE projects SET workspace_dir = ? WHERE workspace_dir = ?",
                (new_dir, old_dir),
            )
            con.commit()
        finally:
            con.close()
        print("teamwork vteam.db updated")

    # PRAX_USER_ID must become the DIRECTORY name, not the user's UUID:
    # prax/utils/shell.py strips `/app/workspaces/<PRAX_USER_ID>/` to map host
    # paths onto the sandbox's /workspace, and that prefix is the directory.
    if PRAX_ENV.exists():
        lines = PRAX_ENV.read_text().splitlines(keepends=True)
        out, seen = [], False
        for line in lines:
            if line.startswith("PRAX_USER_ID="):
                out.append(f"PRAX_USER_ID={new_dir}\n")
                seen = True
            else:
                out.append(line)
        if not seen:
            out.append(f"PRAX_USER_ID={new_dir}\n")
        PRAX_ENV.write_text("".join(out))
        print("prax/.env PRAX_USER_ID updated")

    print(json.dumps({"user_id": user_id, "old": old_dir, "new": new_dir}, indent=2))
    print("\nDone. Restart TeamWork first, then Prax.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
