# Workspace

[← Architecture](README.md)

### Workspace Layout

> Illustrative layout (updated 2026-09 from `workspace_service.py`, `library_service.py` and the compose mounts); a real workspace contains whichever of these the user has touched.

```
workspaces/usr_{id8}/          ← opaque id from identity_service (pre-existing phone-number / D{discord_id} dirs are honoured as legacy only)
├── .git/                  ← full version history
├── schedules.yaml         ← cron schedule definitions (YAML)
├── user_notes.md          ← compact quick-reference facts about the user (timezone, aliases, key preferences)
├── links.md               ← running log of every URL the user has shared
├── todos.json             ← user's personal to-do list
├── instructions.md        ← system prompt reference (agent can re-read)
├── agent_plan.yaml        ← current task decomposition (transient; agent_plan.json is the legacy fallback)
├── trace.log              ← conversation trace (rotated at 0.5 MB)
├── feeds.yaml             ← RSS/Atom feed subscriptions
├── library/               ← the Library (see ../library.md)
│   ├── LIBRARY.md         ← schema / rules
│   ├── INDEX.md           ← auto-maintained index
│   ├── raw/  outputs/  archive/
│   └── spaces/{slug}/     ← .space.yaml, .tasks.yaml, notebooks/{nb}/{note}.md
├── .services/             ← per-user Qdrant / Neo4j / TeamWork data (compose mounts)
├── .sandbox/              ← the sandbox container's persisted home dirs (compose mounts)
├── plugins/               ← custom/ and shared/ per-user plugin dirs
├── notes/                 ← markdown notes with YAML frontmatter
│   ├── eigenvalues.md
│   └── bayesian-prob.md
├── news/                  ← daily briefings with YAML frontmatter
│   └── tech-news-2026-04-01.md
├── courses/               ← structured learning content
│   └── {course-id}/
│       ├── course.yaml    ← course metadata, lessons, progress
│       └── _site/         ← Hugo-generated static site
├── projects/              ← research projects (notes + links + sources)
│   └── {project-slug}/
│       ├── project.yaml
│       └── paper.md
├── active/                ← files the agent is currently aware of
│   └── 2301.12345.md      ← extracted PDF with frontmatter
└── archive/               ← agent moves files here when done
    ├── trace_logs/        ← rotated trace logs (plain text, grep-able)
    │   └── trace.20250301-120000.log
    └── 2301.12345.pdf     ← original PDF preserved

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

`user_notes.md` is git-backed and compacted by Prax after oversized or duplicate-heavy updates. The full file is not injected into every prompt; Prax retrieves only request-relevant snippets to avoid context pollution. Durable dropped details may be selectively promoted to LTM when memory infrastructure is available. See [Memory System: Quick-Reference User Notes](../infrastructure/memory.md#quick-reference-user-notes).

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

Do **not** symlink the live `workspaces/` directory into Dropbox — that is exactly the live-mount the warning above rules out (an older version of this page suggested it). The scheduled `rsync` of a copy is the supported shape.
