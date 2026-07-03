# Git hygiene — keeping this repo clean of data & secrets

[← Guides](README.md)

Prax generates **runtime data next to the code** — SQLite databases
(`identity.db`, `conversations.db`, `test.db`), their timestamped backups,
`workspaces/`, logs. None of it belongs in git. This is the playbook for keeping
it out — and for the surgery if something slips in.

## What must NEVER be committed

- **Databases & backups** — `*.db`, `*.db-journal`, and every backup variant
  (`identity.db.bak2-010511`, `conversations.db.legacy-backup`, …). These contain
  **user data** (identity mappings, phone numbers, conversation history).
- **Secrets** — `.env` (only `.env-example` is tracked), tokens, `*.pem`, `*.key`.
- **Runtime dirs** — `workspaces/`, `library/`, logs, `__pycache__/`.

## The gitignore gotcha that caused a real leak

An **exact-name** ignore rule does **not** match backup suffixes:

```
conversations.db            # ignores conversations.db ONLY
                            # does NOT ignore conversations.db.legacy-backup
*.db                        # ignores identity.db
                            # does NOT ignore identity.db.bak2-010511  (ends in .bak2-010511, not .db)
```

Always ignore with **globs that cover the backups**:

```gitignore
identity.db*
conversations.db*
*.db.bak*
```

## Before every commit — do NOT blanket-add

`git add -A` is how data files sneak in. Stage explicitly, or scan first:

```bash
# scan what you're about to commit for data/secret patterns
git status --porcelain | grep -iE '\.db($|[.\-])|\.bak|backup|\.sqlite|\.legacy|\.env($|\.)|secret|token|\.pem$|\.key$'
```

If that prints anything that isn't `.env-example`, stop and fix `.gitignore` first.

**Standing sweep** — nothing data-like should ever be *tracked*:

```bash
git ls-files | grep -iE '\.db($|[.\-])|\.sqlite|\.bak|backup|\.legacy|\.dump|\.pem$|\.key$' \
  && echo 'LEAK: data/secret file is tracked' || echo 'clean'
```

## If a data/secret file already got committed — the surgery

This is the exact, smooth procedure used to purge `conversations.db.legacy-backup`
from public history. It uses **`git filter-repo`** (the tool the git project and
GitHub now recommend — supersedes `git filter-branch` and BFG).

```bash
# 0) Get filter-repo (self-contained single Python file; no install needed)
curl -fsSL https://raw.githubusercontent.com/newren/git-filter-repo/v2.47.0/git-filter-repo \
  -o /tmp/git-filter-repo
python3 /tmp/git-filter-repo --version          # verify before use

# 1) SAFETY NET — full backup of every ref, so the pre-rewrite state is recoverable
git bundle create ../prax-history-backup.bundle --all

# 2) THE SURGERY — strip the path from every commit on every branch
#    --invert-paths = keep everything EXCEPT these paths; --force = not a fresh clone
python3 /tmp/git-filter-repo --path conversations.db.legacy-backup --invert-paths --force

# 3) filter-repo deletes 'origin' on purpose (so you can't push a rewrite blindly)
git remote add origin git@github.com:praxagent/prax.git

# 4) VERIFY the blob is gone from ALL history (must print 0)
git log --all --oneline -- conversations.db.legacy-backup | wc -l

# 5) Force-push with a LEASE pinned to the current remote SHA (can't clobber a moved ref)
REMOTE=$(git ls-remote origin main | awk '{print $1}')
git push --force-with-lease=main:$REMOTE origin main
#    repeat per branch that contained the blob
```

Notes:
- `main` is usually **branch-protected** against force-push — temporarily enable
  "Allow force pushes" in **Settings → Branches/Rulesets**, push, then re-enable.
- Delete the backup bundle once satisfied — it still contains the file, locally.

## Purging ≠ un-exposing (if the repo is/was public)

Rewriting history removes the blob from your branches, but **cannot** reach:

- **Forks** — each keeps its own history. Check repo → Forks; only **GitHub
  Support** can purge a fork's copy.
- **Old SHAs on GitHub** — the pre-rewrite commit stays reachable by its SHA until
  GitHub garbage-collects. For sensitive data, **contact GitHub Support** to purge
  cached views and expire the SHA immediately.
- **Scrapers/caches** — public content may already be copied (search indexes,
  archives, dataset crawlers). Treat leaked data as **potentially compromised** and
  rotate/assume-exposed accordingly.

## Related

- [`.gitignore`](../../.gitignore) — the actual rules
- Never commit `.env` (see [CLAUDE.md](../../CLAUDE.md) Rules)
