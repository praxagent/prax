# Hub-and-Spoke Architecture

[← Architecture](README.md)

Prax uses a hub-and-spoke model: the orchestrator holds ~24 core tools (workspace, scheduling, courses) and delegates domain-specific work to focused spoke agents.  This keeps the orchestrator's context lean — [research shows](#9-tool-overload-and-selection-degradation) that tool selection accuracy degrades significantly past 20–50 tools.

> **Fallback:** If a delegated agent fails or can't handle the task, Prax can read the full tool catalog from a generated markdown file and call any tool directly. The spoke system is the fast path; direct tool access is the safety net.

#### Orchestrator (Hub)

```mermaid
graph TB
    Prax["Prax Orchestrator\n~24 tools"]

    Prax --> Core["Core\nsearch, datetime, URL fetch, image analysis"]
    Prax --> Workspace["Workspace\nfiles, todos, planning"]
    Prax --> Courses["Courses\ncreate, tutor, publish"]

    Prax -->|delegate_browser| Browser["Browser Spoke"]
    Prax -->|delegate_content_editor| Content["Content Editor\nsub-hub"]
    Prax -->|delegate_sysadmin| Sysadmin["Sysadmin\nsub-hub"]
    Prax -->|delegate_sandbox| Sandbox["Sandbox Spoke"]
    Prax -->|delegate_finetune| Finetune["Finetune Spoke"]
    Prax -->|delegate_knowledge| Knowledge["Knowledge Spoke"]
    Prax -->|delegate_research| Research["Research Spoke\n+ Professor capability"]
    Prax -->|delegate_task| Generic["Generic Sub-Agent"]

    Research -->|hard questions| Professor["multi_model_query\nOpenAI + Claude + Gemini\npro-tier models"]

    style Prax fill:#4A90D9,color:#fff
    style Browser fill:#F5A623,color:#fff
    style Content fill:#E8543E,color:#fff
    style Sysadmin fill:#E8543E,color:#fff
    style Sandbox fill:#F5A623,color:#fff
    style Finetune fill:#F5A623,color:#fff
    style Knowledge fill:#F5A623,color:#fff
    style Research fill:#F5A623,color:#fff
    style Generic fill:#F5A623,color:#fff
```

#### Media Agent

Handles images, PDFs, audio, video transcripts, and web content extraction.

```mermaid
graph LR
    Media["📰 Media Agent"] --> analyze["analyze_image"]
    Media --> pdf["pdf_summary_tool"]
    Media --> yt["youtube_transcribe"]
    Media --> arxiv["arxiv_fetch_papers"]
    Media --> npr["npr_podcast_tool"]
    Media --> web["web_summary_tool"]
    Media --> dlf["deutschlandfunk_tool"]
    Media --> fetch["fetch_url_content"]
    style Media fill:#9013FE,color:#fff
```

#### Sandbox Agent

Executes code in an isolated Docker container with a full dev environment.

```mermaid
graph LR
    Sandbox["🐳 Sandbox Agent"] --> exec["sandbox_execute"]
    Sandbox --> start["sandbox_start"]
    Sandbox --> msg["sandbox_message"]
    Sandbox --> review["sandbox_review"]
    Sandbox --> finish["sandbox_finish"]
    Sandbox --> abort["sandbox_abort"]
    Sandbox --> search["sandbox_search"]
    Sandbox --> install["sandbox_install"]
    style Sandbox fill:#4A90D9,color:#fff
```

#### Browser Agent

Automates web interactions via Playwright with persistent profiles and credential management.

```mermaid
graph LR
    Browser["🌐 Browser Agent"] --> open["browser_open"]
    Browser --> read["browser_read_page"]
    Browser --> shot["browser_screenshot"]
    Browser --> click["browser_click"]
    Browser --> fill["browser_fill"]
    Browser --> press["browser_press"]
    Browser --> find["browser_find"]
    Browser --> creds["browser_credentials"]
    Browser --> login["browser_login"]
    Browser --> close["browser_close"]
    Browser --> profiles["browser_profiles"]
    style Browser fill:#F5A623,color:#fff
```

#### Workspace Agent

Manages per-user file storage, git-backed workspaces, and link history.

```mermaid
graph LR
    Workspace["📁 Workspace Agent"] --> save["workspace_save"]
    Workspace --> rd["workspace_read"]
    Workspace --> ls["workspace_list"]
    Workspace --> arch["workspace_archive"]
    Workspace --> srch["workspace_search"]
    Workspace --> restore["workspace_restore"]
    Workspace --> link["log_link"]
    Workspace --> hist["links_history"]
    Workspace --> push["workspace_push"]
    Workspace --> share["workspace_share_file"]
    style Workspace fill:#7ED321,color:#fff
```

#### Scheduler Agent

Manages recurring cron jobs and one-time reminders.

```mermaid
graph LR
    Scheduler["⏰ Scheduler Agent"] --> create["schedule_create"]
    Scheduler --> list["schedule_list"]
    Scheduler --> update["schedule_update"]
    Scheduler --> delete["schedule_delete"]
    Scheduler --> tz["schedule_set_timezone"]
    Scheduler --> reload["schedule_reload"]
    Scheduler --> remind["schedule_reminder"]
    Scheduler --> rlist["reminder_list"]
    Scheduler --> rdel["reminder_delete"]
    style Scheduler fill:#BD10E0,color:#fff
```

#### Plugin Engineering Agent

Creates, tests, and manages hot-swappable plugins.

```mermaid
graph LR
    Plugin["🔌 Plugin Agent"] --> plist["plugin_list"]
    Plugin --> pread["plugin_read"]
    Plugin --> pwrite["plugin_write"]
    Plugin --> ptest["plugin_test"]
    Plugin --> activate["plugin_activate"]
    Plugin --> rollback["plugin_rollback"]
    Plugin --> catalog["plugin_catalog"]
    Plugin --> src["source_read / source_list"]
    Plugin --> prompt["prompt_read / prompt_write"]
    style Plugin fill:#D0021B,color:#fff
```

#### Self-Improvement Agent

Diagnoses bugs in Prax's own code, writes patches in the sandbox, and deploys fixes.

```mermaid
graph LR
    SelfImprove["🔧 Self-Improve Agent"] --> start["self_improve_start"]
    SelfImprove --> rd["self_improve_read"]
    SelfImprove --> wr["self_improve_write"]
    SelfImprove --> test["self_improve_test"]
    SelfImprove --> lint["self_improve_lint"]
    SelfImprove --> verify["self_improve_verify"]
    SelfImprove --> deploy["self_improve_deploy"]
    SelfImprove --> logs["read_logs"]
    style SelfImprove fill:#417505,color:#fff
```

### Key Modules

| Module | Purpose |
|--------|---------|
| `prax/agent/orchestrator.py` | LangGraph ReAct agent with hot-swappable system prompt, plugin-aware graph rebuild, and per-component LLM routing |
| `prax/agent/subagent.py` | General sub-agent delegation: spawns focused LangGraph sub-graphs with per-category LLM config |
| `prax/agent/self_improve_agent.py` | Self-improvement sub-agent: diagnose bugs, patch via sandbox, deploy via codegen |
| `prax/agent/plugin_fix_agent.py` | Plugin engineering sub-agent: create/fix/test/activate plugins autonomously |
| `prax/agent/course_author_agent.py` | Content author sub-agent: produces rich course materials (mermaid, code, LaTeX) via iterative sandbox drafting |
| `prax/agent/tools.py` | Kernel tool wrappers (search, datetime, fetch_url) — reader tools migrated to plugins |
| `prax/agent/plugin_tools.py` | 17 plugin management tools: plugin CRUD, catalog, prompt CRUD, LLM config, source_read/list |
| `prax/agent/workspace_tools.py` | 24 workspace tools: notes, files, links, todos, task planning, instructions, conversation history/search, system status, diff-aware patch |
| `prax/agent/sandbox_tools.py` | 7 sandbox tools for code execution sessions |
| `prax/agent/scheduler_tools.py` | 9 scheduler tools: recurring cron + one-time reminders |
| `prax/agent/finetune_tools.py` | 8 fine-tuning tools (harvest, train, verify, promote, rollback) |
| `prax/agent/codegen_tools.py` | 10 self-improvement tools (worktree, edit, test, lint, verify, deploy, PR) |
| `prax/agent/note_tools.py` | 7 note tools (create, update, list, search, note_from_url, pdf_to_note, note_link) |
| `prax/agent/project_tools.py` | 6 research project tools (create, status, add note/link/source, brief) |
| `prax/agent/browser_tools.py` | 14 browser tools (navigate, click, fill, screenshot, login, VNC) |
| `prax/agent/tool_registry.py` | Tool aggregation: built-in + plugin-provided + manually registered |
| `prax/agent/llm_factory.py` | Multi-provider LLM factory (OpenAI, Anthropic, Google, Ollama, vLLM) |
| `prax/plugins/loader.py` | Recursive plugin discovery (folder-per-plugin + flat), hot-swap, version tracking, auto-rollback, catalog generation |
| `prax/plugins/sandbox.py` | Subprocess-isolated plugin validation before activation |
| `prax/plugins/registry.py` | JSON-based version registry with rollback and failure monitoring |
| `prax/plugins/repo.py` | Plugin repository service: SSH deploy key auth, clone, commit, push to private repo branch |
| `prax/plugins/catalog.py` | Auto-generated CATALOG.md listing all available plugins with metadata |
| `prax/plugins/prompt_manager.py` | Hot-swappable system prompt loading with variable expansion |
| `prax/plugins/llm_config.py` | Per-component LLM routing (YAML-based, hot-reloaded) |
| `prax/plugins/monitored_tool.py` | Runtime monitoring wrapper: failure counting + auto-rollback |
| `prax/plugins/tools/*/plugin.py` | Built-in reader plugins (NPR, web summary, PDF, YouTube, arXiv, RSS, Deutschlandfunk) |
| `prax/services/sms_service.py` | SMS workflow: media handling, PDF pipeline, agent routing |
| `prax/services/voice_service.py` | Voice workflow: speech processing, TTS buffer management |
| `prax/services/conversation_service.py` | Shared conversation layer with workspace context injection |
| `prax/services/sandbox_service.py` | Docker + OpenCode sandbox lifecycle, archiving, budget control |
| `prax/services/scheduler_service.py` | APScheduler-backed cron service reading YAML definitions |
| `prax/services/finetune_service.py` | LoRA fine-tuning pipeline: harvest → train → verify → hot-swap |
| `prax/services/note_service.py` | Note CRUD, search, knowledge graph (related notes), Hugo page generation |
| `prax/services/project_service.py` | Research project CRUD, note/link/source aggregation, brief generation |
| `prax/services/codegen_service.py` | Self-modification via staging clone + verify + hot-swap / PR workflow |
| `prax/services/discord_service.py` | Discord bot: message handling, authorization, response delivery |
| `prax/services/browser_service.py` | Playwright browser automation with per-user sessions |
| `prax/services/pdf_service.py` | PDF download, extraction (opendataloader-pdf), arxiv detection |
| `prax/services/youtube_service.py` | YouTube audio download (yt-dlp) + Whisper transcription |
| `prax/services/workspace_service.py` | Git-backed per-user file operations with per-user locking |
| `scripts/watchdog.py` | Supervisor process: health checks Flask, auto-rollback on crash after self-improve deploy |
| `scripts/finetune_train.py` | Standalone Unsloth QLoRA training script (runs in GPU subprocess) |
| `prax/settings.py` | Pydantic BaseSettings — all config from `.env` |
| `prax/clients.py` | Shared lazy-initialized Twilio client |
| `prax/sms.py` | SMS chunking and sending utilities |
| `prax/call_state.py` | `CallStateManager` — typed call state with `ensure()` |
| `prax/conversation_memory.py` | SQLite storage with auto-summarization at 100k tokens |
