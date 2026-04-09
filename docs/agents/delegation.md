# Agent Delegation

[← Agents](README.md)

Prax keeps its main conversation loop lean by delegating domain-specific work to focused **spoke agents**.  Each spoke runs its own LangGraph ReAct loop with a specialized system prompt and curated tool set — the orchestrator sees only a single `delegate_*` tool per spoke.

Research shows that LLM tool-selection accuracy degrades past 20--30 tools ([see Research section](../research/README.md)).  The hub-and-spoke pattern keeps the orchestrator's tool count low while giving each spoke deep domain capabilities.

Key infrastructure that makes this work:

- **Execution tracing** -- every delegation chain gets a UUID.  Individual agent invocations get span IDs.  The execution graph tracks the full tree: timing, status, tool call counts, and parent/child relationships.  Governing agents see the big picture via the graph summary appended to `delegate_parallel` results.
- **Read guard** -- spokes can verify preconditions before starting work (inspired by [smux](https://github.com/ShawnPana/smux)'s read-before-act pattern).  If the guard fails, the spoke aborts without wasting an LLM call.
- **Identity injection** -- each agent receives execution context in its system prompt: trace ID, depth in the delegation tree, who delegated it, and what parallel peers are doing.
- **Self-diagnostics** -- `prax_doctor` checks LLM configuration, sandbox health, plugin status, spoke availability, workspace integrity, TeamWork connectivity, and scheduler state in one call.

## Hub-and-Spoke Architecture

```mermaid
graph TB
    User([User]) --> Prax[Prax Orchestrator<br/>~24 core tools]

    Prax -->|delegate_browser| Browser[Browser Agent<br/>16 tools: CDP + Playwright]
    Prax -->|delegate_content_editor| Content[Content Editor<br/>sub-hub: research → write → review]
    Prax -->|delegate_sysadmin| Sysadmin[Sysadmin Agent<br/>30+ tools: plugins, config, source]
    Prax -->|delegate_sandbox| Sandbox[Sandbox Agent<br/>9 tools: Docker + OpenCode]
    Prax -->|delegate_finetune| Finetune[Finetune Agent<br/>8 tools: LoRA pipeline]
    Prax -->|delegate_knowledge| Knowledge[Knowledge Agent<br/>13 tools: notes + projects]
    Prax -->|delegate_research| Research[Research Agent<br/>web search + plugins + professor]
    Prax -->|delegate_task| Generic[Generic Sub-Agent<br/>category-routed]

    Research -->|hard questions| Professor[Professor<br/>multi_model_query<br/>pro-tier models]

    Sysadmin -->|delegate_self_improve| SelfImprove[Self-Improve Agent<br/>source + codegen]
    Sysadmin -->|delegate_plugin_fix| PluginFix[Plugin Engineer<br/>plugin lifecycle + sandbox]

    Content -->|blog mode| BlogPipeline[Research → Write → Review]
    Content -->|course_module mode| CourseAuthor[Course Author<br/>sandbox + course tools]

    BlogPipeline -->|Phase 1| Researcher[Research Sub-Agent]
    BlogPipeline -->|Phase 2/4| Writer[Writer Sub-Agent]
    BlogPipeline -->|Phase 3| Publisher[Publisher]
    BlogPipeline -->|Phase 4| Reviewer[Reviewer Sub-Agent<br/>cross-provider critique]

    Browser -->|CDP fast path| Chrome[Sandbox Chrome]
    Browser -->|Playwright reliable path| Chrome
```

## Spoke Agents

| Spoke | Delegation Tool | Tools | Purpose |
|-------|----------------|-------|---------|
| **Browser** | `delegate_browser` | 16: CDP read/act + Playwright navigate/click/fill/login/VNC | Web navigation, page reading, login flows, screenshots |
| **Content Editor** | `delegate_content_editor` | Sub-hub: blog pipeline (research → write → review) or course author mode | Blog posts, publication-quality content, and course module content |
| **Sysadmin** | `delegate_sysadmin` | 30+: plugin mgmt, prompts, LLM config, source, workspace sync | Plugin install/update, config changes, self-improvement |
| **Sandbox** | `delegate_sandbox` | 9: session lifecycle, archive, package management | Code execution in isolated Docker containers |
| **Finetune** | `delegate_finetune` | 8: harvest, train, verify, promote, rollback | LoRA fine-tuning pipeline (requires FINETUNE_ENABLED) |
| **Knowledge** | `delegate_knowledge` | 13: note CRUD, search, linking, URL/PDF-to-note, project management | Notes, knowledge graph, research projects |
| **Research** | `delegate_research` | Web search, URL fetch, datetime, reader plugins, multi_model_query | Multi-source investigation with citations; professor escalation for hard questions |
| **Generic** | `delegate_task(category=...)` | Category-routed (research, workspace, scheduler, codegen) | Ad-hoc delegation for categories without a dedicated spoke |

## Sub-Hubs: Spokes That Spawn Agents

Some spokes are **sub-hubs** — they don't just run a single ReAct loop, they orchestrate multiple sub-agents in a pipeline.  This gives them richer behavior than a flat tool set while keeping the main orchestrator unaware of the internal complexity.

**Content Editor** is the primary example.  It has two modes controlled by a `mode` parameter:

**Blog mode** (default) — a procedural coordinator that runs a multi-phase pipeline:

```mermaid
flowchart LR
    A[Research] --> B[Write]
    B --> C[Publish]
    C --> D[Review]
    D -->|APPROVED| E[Done]
    D -->|REVISE| F[Write\nwith feedback]
    F --> G[Re-publish]
    G --> D
    style E fill:#2d6,stroke:#1a4
```
*Max 3 revision cycles.*

Each phase uses a different sub-agent:
- **Researcher** — generic research sub-agent via `_run_subagent(query, "research")`
- **Writer** — ReAct agent with search tools; takes research findings + optional revision feedback
- **Reviewer** — ReAct agent that uses `delegate_browser` to visually inspect the published page; **deliberately uses a different LLM provider** than the writer for adversarial diversity
- **Publisher** — utility functions wrapping the note/Hugo system

**Course module mode** (`mode="course_module"`) — routes to the Course Author sub-agent for rich, sandbox-based content with Mermaid diagrams, LaTeX equations, code examples, and structured pedagogy.

**Sysadmin** is another sub-hub.  It holds ~30 plugin/config tools directly, but can further delegate to:
- `delegate_self_improve` — for bug fixes requiring source + sandbox + codegen
- `delegate_plugin_fix` — for plugin creation/fixes requiring sandbox iteration

This hierarchical pattern means the orchestrator calls one tool (`delegate_sysadmin`), the sysadmin tries to handle it directly, and only escalates to a sub-agent when the task requires code-level changes.

## Browser Spoke Detail

The browser spoke is the reference implementation for the **simple spoke pattern** (single ReAct agent).  It demonstrates CDP-first routing with Playwright fallback:

```mermaid
graph LR
    Prax[Orchestrator] -->|"delegate_browser('read this tweet')"| BA[Browser Agent]

    BA -->|Fast path| CDP[sandbox_browser_read<br/>sandbox_browser_act]
    BA -->|Reliable path| PW[browser_open<br/>browser_fill<br/>browser_click<br/>...]
    BA -->|Login flow| Login[browser_credentials<br/>browser_login<br/>browser_request_login]

    CDP --> Chrome[Sandbox Chrome<br/>shared with TeamWork]
    PW --> Chrome
```

The browser agent decides internally:
- **CDP first** — page reads, screenshots, quick navigation, simple clicks (faster)
- **Playwright when needed** — login flows, form filling, auto-waiting, complex selectors (more reliable)
- Both APIs hit the **same Chrome instance** the user sees in TeamWork

## Reusable Synthesis Pipeline

Both the content spoke (blog posts) and the knowledge spoke (deep-dive notes) share a reusable `SynthesisPipeline` at `prax/agent/pipelines/synthesis.py`. It encapsulates the multi-agent research → write → publish → review → revise loop with pluggable phase callables.

```mermaid
flowchart LR
    A[Research<br/>optional] --> B[Write]
    B --> C[Publish]
    C --> D[Review<br/>cross-provider]
    D -->|APPROVED| E[Done]
    D -->|REVISE| F[Write<br/>with feedback]
    F --> G[Re-publish]
    G --> D
    style E fill:#2d6,stroke:#1a4
    style D fill:#E8543E,color:#fff
```

Each phase is a simple callable the spoke injects:

```python
from prax.agent.pipelines import SynthesisPipeline

pipeline = SynthesisPipeline(
    researcher=my_research_fn,  # (topic, notes) → str
    writer=my_write_fn,          # (topic, research, feedback, previous) → str
    publisher=my_publish_fn,     # (title, content, tags, slug) → dict
    reviewer=my_review_fn,       # (draft, url, pass_num) → str
    max_revisions=3,
    status_callback=post_to_teamwork,
    item_kind="Note",  # or "Blog post"
    skip_research=True,  # when source is pre-fetched
    pre_fetched_research=article_text,
)
result = pipeline.run(topic, notes="", tags=[])
```

**Content spoke blog mode** uses it with the blog writer/reviewer. **Knowledge spoke** uses it via `note_deep_dive(topic, source_content)` for explainer/deep-dive requests. Cross-provider reviewer diversity is handled by `_pick_reviewer_llm` from the content spoke, shared across both pipelines.

## Note Deep-Dive (Multi-Agent)

When the user asks for a "deep dive", "explainer", or "break down" style note, the knowledge spoke routes to `note_deep_dive` which runs the SynthesisPipeline:

```mermaid
flowchart LR
    Prax[Orchestrator] -->|delegate_knowledge| KA[Knowledge Agent]
    KA -->|note_deep_dive| NW[Note Writer<br/>high tier]
    NW --> P[Publish]
    P --> NR[Note Reviewer<br/>different provider]
    NR -->|APPROVED| D[Done]
    NR -->|REVISE| NW
```

- **Writer**: `subagent_note_writer` (high tier, 0.5 temp) — no tools, just writes
- **Reviewer**: cross-provider via `_pick_reviewer_llm` — rejects raw dumps, broken LaTeX, missing toy examples, shallow content
- **Max 3 revisions** before the pipeline gives up and publishes as-is
- **Pre-fetched source**: if Prax already has the article content, it's passed in and research is skipped

This replaces the old "single ReAct agent writes whatever" approach that produced raw-dumped notes.

## Research Decomposition (Parallel Sub-Agents)

For broad multi-topic research questions, the research agent can decompose into parallel sub-research via `research_subtopics`:

```mermaid
flowchart TD
    Prax[Orchestrator] -->|delegate_research| RA[Research Agent<br/>depth 0]
    RA -->|research_subtopics JSON array| Split{decompose?}
    Split -->|Yes| T1[Subtopic 1<br/>depth 1]
    Split -->|Yes| T2[Subtopic 2<br/>depth 1]
    Split -->|Yes| T3[Subtopic 3<br/>depth 1]
    T1 & T2 & T3 --> Collect[Collect + combine]
    Collect --> RA
    RA --> Prax

    style RA fill:#4A90D9,color:#fff
```

**Rules:**
- **Max depth 2** — sub-agents cannot further decompose (`research_subtopics` is only added to top-level agent's tool list)
- **Max 5 subtopics** per call (cost control; extras truncated with a warning)
- **Parallel execution**: `ThreadPoolExecutor` with 3 workers
- **Per-subtopic timeout**: 90 seconds
- **Context isolation**: each worker runs in a `copy_context()` so the incremented `_research_depth` contextvar doesn't leak back to the parent
- **Error isolation**: if one subtopic fails or times out, the others still complete and the failure is reported in that section

The research agent's prompt explicitly directs it to only decompose when the question naturally splits (e.g., "compare X, Y, and Z") and NOT for single-topic deep dives.

## Professor Capability (Multi-Model Consensus)

The research agent has an internal escalation path for hard questions: `multi_model_query`. This queries multiple AI models (OpenAI, Anthropic, Google) with the same question and synthesizes a structured consensus.

```mermaid
flowchart LR
    Prax[Orchestrator] -->|delegate_research| RA[Research Agent]
    RA -->|normal work| Tools[web_search, fetch_url, plugins]
    RA -->|hard question| MMQ[multi_model_query]
    MMQ --> M1[OpenAI Pro]
    MMQ --> M2[Claude Pro]
    MMQ --> M3[Gemini Pro]
    M1 & M2 & M3 --> Synth[Synthesis LLM]
    Synth --> Consensus[Structured Consensus]

    style MMQ fill:#9013FE,color:#fff
    style Consensus fill:#2d6,color:#fff
```

**How it works:**
- The research agent does its own work first (search, read, cite)
- If the topic is genuinely contested, uncertain, or high-stakes, it calls `multi_model_query`
- Each available provider is queried with a pro-tier model
- A synthesis step produces a structured report: Agreement / Disagreement / Unique Insights / Synthesis

**Availability gating:**
- Requires at least 2 LLM providers with API keys configured (e.g., `OPENAI_KEY` + `ANTHROPIC_KEY`)
- If only one provider is available, `multi_model_query` is not added to the research agent's tools
- Uses expensive pro-tier models — the research agent is instructed to use it sparingly

**Orchestrator escalation:**
- If Prax delegates research and the result seems weak or contradictory, Prax can re-delegate with explicit instructions: "Use multi_model_query to get multi-model consensus on: [question]"
- The professor capability is NOT a separate spoke — it's a tool inside the research agent

**Cost control:**
- Pro-tier models are 5-10x more expensive than standard models
- The research agent's system prompt has strict guidelines: do your own research first, only escalate genuinely hard problems
- Simple factual lookups, subjective topics, and well-documented answers should never go through multi_model_query

## Office Document Export

The content spoke includes three tools for generating downloadable office documents:

| Tool | Output | Library |
|------|--------|---------|
| `create_presentation` | .pptx (PowerPoint) | python-pptx |
| `create_spreadsheet` | .xlsx (Excel) | openpyxl |
| `create_pdf` | .pdf | fpdf2 |

These accept structured JSON input (slide data, sheet data) or markdown (PDF) and save to the user's workspace. The orchestrator can call them directly or the content editor can use them as part of a publishing workflow.

## Image Generation Plugin

The `imagegen` plugin (in `prax-plugins/imagegen/`) provides two tools:

| Tool | What it does |
|------|-------------|
| `generate_image` | Text-to-image via OpenAI gpt-image-1 (DALL-E). Supports size, quality, and style parameters. |
| `edit_image` | Edit an existing image — add, remove, or modify elements via AI. |

Requires `OPENAI_KEY`. Images are saved as PNG to the user's workspace. The plugin runs through the PluginCapabilities gateway and never touches API keys directly.

## What Stays on the Orchestrator

The orchestrator keeps tools that are **conversational** (require back-and-forth with the user) or **foundational** (used by many workflows):

- **Conversation** — interactive Q&A, pacing, tone
- **Workspace** — file CRUD, todos, planning (11 tools)
- **Courses** — tutoring is conversational; the orchestrator IS the tutor (6 tools)
- **Scheduling** — cron jobs, reminders (9 tools)
- **URL handling** — lightweight `fetch_url_content` (no browser needed)
- **Routing decisions** — choosing which spoke to delegate to
- **Spoke delegation** — 6 spoke tools + 2 generic sub-agent tools + 1 research delegate + 1 vision tool

## Adding a New Spoke

The spoke system lives in `prax/agent/spokes/` with one folder per spoke:

```
prax/agent/spokes/
├── __init__.py          # Registry — imports all spokes
├── _runner.py           # Shared delegation engine (LLM, invoke, logging, TeamWork)
├── browser/             # Simple spoke: single ReAct agent (reference implementation)
│   ├── __init__.py
│   └── agent.py         # Prompt, tools, delegate function
├── content/             # Sub-hub spoke: blog pipeline + course author mode
│   ├── __init__.py
│   ├── agent.py          # Pipeline coordinator (blog mode) + course author routing
│   ├── writer.py         # Writer sub-agent
│   ├── reviewer.py       # Reviewer sub-agent (cross-provider)
│   ├── publisher.py      # Hugo publishing utilities
│   └── prompts.py        # System prompts for sub-agents
├── finetune/            # Simple spoke: LoRA training pipeline
├── knowledge/           # Simple spoke: notes + research projects
├── sandbox/             # Simple spoke: Docker code execution
└── sysadmin/            # Sub-hub spoke: delegates to self-improve + plugin-fix
```

**Two spoke patterns:**

1. **Simple spoke** — single ReAct agent with curated tools.  Use `run_spoke()` from `_runner.py`.  See `browser/agent.py`.
2. **Sub-hub spoke** — procedural coordinator that spawns sub-agents.  Write your own orchestration logic.  See `content/agent.py`.

To add a new spoke:

1. Create `prax/agent/spokes/<name>/agent.py` with `SYSTEM_PROMPT`, `build_tools()`, `delegate_<name>()`, `build_spoke_tools()`
2. Register in `prax/agent/spokes/__init__.py`
3. Remove the spoke's direct tools from `tools.py:build_default_tools()`

See `prax/agent/spokes/browser/agent.py` for a simple spoke, or `prax/agent/spokes/content/agent.py` for a sub-hub.

## Source Code in the Sandbox

The app source is mounted in the sandbox container at `/source/` so OpenCode can read and modify Prax's own code:

| Mode | Mount | Access |
|------|-------|--------|
| Production | `./prax:/source/prax` | Read-only — OpenCode can inspect but not modify directly |
| Dev mode | `./prax:/source/prax` | Read-write — changes propagate via bind mount, Werkzeug auto-reloads |

## Key Files

| File | Purpose |
|------|---------|
| `prax/agent/spokes/` | Spoke agent directory — one folder per spoke |
| `prax/agent/spokes/_runner.py` | Shared delegation engine — LLM config, invocation, logging, TeamWork hooks |
| `prax/agent/spokes/browser/agent.py` | Browser spoke — CDP-first routing, Playwright fallback, login flows |
| `prax/agent/spokes/content/agent.py` | Content Editor sub-hub — multi-agent pipeline (research → write → review) |
| `prax/agent/spokes/sysadmin/agent.py` | Sysadmin sub-hub — plugin/config mgmt, delegates to self-improve + plugin-fix |
| `prax/agent/spokes/sandbox/agent.py` | Sandbox spoke — Docker code execution sessions |
| `prax/agent/spokes/finetune/agent.py` | Finetune spoke — LoRA training pipeline |
| `prax/agent/spokes/knowledge/agent.py` | Knowledge spoke — notes, knowledge graph, research projects |
| `prax/agent/self_improve_agent.py` | Self-improvement sub-agent (used by sysadmin spoke) |
| `prax/agent/plugin_fix_agent.py` | Plugin engineering sub-agent (used by sysadmin spoke) |
| `prax/agent/course_author_agent.py` | Course author sub-agent (used by content editor in course_module mode) |
| `prax/agent/research_agent.py` | Research delegate — multi-source investigation with citations + professor escalation |
| `prax/agent/spokes/professor/agent.py` | Professor module — `multi_model_query` tool + provider detection + synthesis |
| `prax/agent/office_tools.py` | Office document export — .pptx, .xlsx, .pdf generation |
| `prax/agent/subagent.py` | Generic delegation (`delegate_task`, `delegate_parallel`) with category and spoke routing |
| `prax/agent/trace.py` | Execution tracing — chain UUIDs, named spans, execution graphs |
| `prax/agent/doctor.py` | Self-diagnostics (`prax_doctor`) — LLM, sandbox, plugins, spokes, TeamWork |
| `scripts/watchdog.py` | Supervisor process — health checks, crash rollback, restart |
