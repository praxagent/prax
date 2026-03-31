# Workspace

[← Architecture](README.md)

### Workspace Layout

```
workspaces/{user_id}/          ← phone number or Discord user ID
├── .git/                  ← full version history
├── schedules.yaml         ← cron schedule definitions (YAML)
├── user_notes.md          ← dynamic notes about the user (timezone, preferences, personality)
├── links.md               ← running log of every URL the user has shared
├── todos.json             ← user's personal to-do list
├── instructions.md        ← system prompt reference (agent can re-read)
├── agent_plan.json        ← current task decomposition (transient)
├── trace.log              ← conversation trace (rotated at 0.5 MB)
├── feeds.yaml             ← RSS/Atom feed subscriptions
├── notes/                 ← markdown notes with YAML frontmatter
│   ├── eigenvalues.md
│   └── bayesian-prob.md
├── projects/              ← research projects (notes + links + sources)
│   └── {project-slug}/
│       ├── project.yaml
│       └── paper.md
├── active/                ← files the agent is currently aware of
│   ├── 2301.12345.md      ← extracted PDF with frontmatter
│   └── sessions/          ← live sandbox coding sessions
│       └── {session_id}/
└── archive/               ← agent moves files here when done
    ├── trace_logs/        ← rotated trace logs (plain text, grep-able)
    │   └── trace.20250301-120000.log
    ├── 2301.12345.pdf     ← original PDF preserved
    └── code/              ← completed coding solutions
        └── pdf_to_beamer/
            ├── SOLUTION.md
            ├── session_log.json
            ├── convert.py
            └── build.sh

adapters/                  ← LoRA adapter storage (FINETUNE_OUTPUT_DIR)
├── adapter_registry.json  ← active/previous adapter tracking
├── training_data/         ← JSONL training batches
│   └── batch_20260320_140000.jsonl
├── adapter_20260319_140000/  ← previous LoRA weights
│   ├── adapter_config.json
│   └── adapter_model.safetensors
└── adapter_20260320_140000/  ← active LoRA weights
    ├── adapter_config.json
    └── adapter_model.safetensors
```

### TeamWork Workspace Integration

When running with [TeamWork](https://github.com/praxagent/teamwork), Prax's workspace directory is shared directly with TeamWork's file browser, terminal, and backup features. No copying or syncing — both systems read from the same directory on disk.

**How it works:** Prax passes its `workspace_dir` (the user's directory name) to TeamWork when creating the project via `/api/external/projects`. Set TeamWork's `WORKSPACE_PATH` environment variable to the same parent directory as Prax's `WORKSPACE_DIR`. For example:

```bash
# Prax .env
WORKSPACE_DIR=./workspaces

# TeamWork .env (or docker-compose volume mount)
WORKSPACE_PATH=./workspaces    # Same directory, shared via volume mount
```

TeamWork expects the workspace directory layout documented in its [Workspace Structure](https://github.com/praxagent/teamwork#workspace-structure) section. Prax's layout (`active/`, `archive/`, `plugins/`, `user_notes.md`, `.git/`) is fully compatible — TeamWork doesn't prescribe internal structure, it just serves what's there.

**Backup:** TeamWork provides a one-click zip download of the workspace from Settings (200 MB cap). This includes all workspace files except `.git/`, caches, and `.env`. For full backups including git history, use `git clone` or `git bundle`.

### Dropbox Sync

> **Warning:** Do not mount Dropbox (or Google Drive, OneDrive, etc.) as a filesystem under the workspace directory. Dropbox syncs by overwriting files, which is incompatible with SQLite's WAL mode and git's lock files. This will cause database corruption and git conflicts.

**Safe alternatives:**
- **TeamWork backup** — one-click zip download from Settings (no sync conflicts)
- **Litestream** — continuous SQLite replication to S3 ([litestream.io](https://litestream.io/))
- **Rclone** — scheduled sync to any cloud provider as an object store (not a mounted filesystem)
- **Git remote** — push the workspace repo to GitHub/GitLab for version-controlled backup

If you still want Dropbox for convenience, sync only a *copy* of the workspace on a schedule, not the live directory:

```bash
# Safe: periodic rsync to Dropbox (not a live mount)
rsync -a --exclude='.git' --exclude='*.db' workspaces/ ~/Dropbox/prax-backups/
```

If you move the project, remove the old symlink and re-link:

```bash
rm ~/Dropbox/prax-workspaces
ln -s "$PWD/workspaces" ~/Dropbox/prax-workspaces
```

The Dropbox desktop app will sync all workspace files (notes, todos, links, archives) in real time. No API keys or code changes needed.
