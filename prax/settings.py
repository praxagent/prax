"""Application settings loaded via Pydantic for validation and reuse."""
import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Central configuration object loaded from environment or .env file."""

    # Flask / Server
    flask_secret_key: str = Field(alias="FLASK_SECRET_KEY")
    session_type: str = Field(default="filesystem", alias="SESSION_TYPE")
    ngrok_url: str | None = Field(default=None, alias="NGROK_URL")
    root_phone_number: str | None = Field(default=None, alias="ROOT_PHONE_NUMBER")
    debug: bool = Field(default=False, alias="DEBUG")
    log_path: str = Field(default="app.log", alias="LOG_PATH")
    # Where the metacognitive store persists learned failure patterns at
    # runtime.  Kept out of the git tree by default (a gitignored ``runtime/``
    # subdir of the shipped seeds) so live learning never dirties the tracked
    # seed profiles.  Set to relocate runtime state (e.g. onto a mounted volume).
    metacognitive_dir: str | None = Field(default=None, alias="METACOGNITIVE_DIR")
    port: int = Field(default=5001, alias="PORT")
    # Interface the Flask app binds. Secure-by-default: 127.0.0.1 (loopback only),
    # so a native/host deployment is NOT reachable from the network — a reverse
    # proxy (e.g. `tailscale serve`, which dials localhost) still works. Set to
    # 0.0.0.0 ONLY when something else owns the security boundary: the container
    # image sets it (its netns is isolated; exposure = the published port/Service),
    # or when you front Prax with an AUTHENTICATING reverse proxy (IAP / Cloudflare
    # Access / oauth2-proxy) and a firewall that admits only that proxy. See
    # docs/security/network-exposure.md.
    bind_host: str = Field(default="127.0.0.1", alias="PRAX_HOST")
    database_name: str = Field(default="conversations.db", alias="DATABASE_NAME")
    identity_db: str = Field(default="identity.db", alias="IDENTITY_DB")

    # Providers / API Keys
    openai_key: str | None = Field(default=None, alias="OPENAI_KEY")
    # Point the OpenAI-compatible client at a THIRD-PARTY provider (OpenRouter,
    # DeepSeek, Groq, …) — the cheap/prepaid path for running evals without
    # bill-shock. Default None = OpenAI. Set OPENAI_KEY to that provider's key.
    # See docs/guides/cheap-evals.md.
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    # Point the Anthropic client at a base URL — used to route Claude calls through
    # the KEYLESS secrets-proxy (the separate praxagent/prax-secrets-proxy service).
    # When set, Prax needs only a
    # placeholder ANTHROPIC_KEY; the proxy injects the real one. Default None =
    # api.anthropic.com. See docs/security/secrets-proxy.md.
    anthropic_base_url: str | None = Field(default=None, alias="ANTHROPIC_BASE_URL")
    # OpenRouter key for the dedicated `openrouter` provider (LLM_PROVIDER=openrouter,
    # or `make eval CHEAP=1`). Presence alone does NOT redirect traffic — you must
    # select the provider — so production stays on OpenAI even with this set.
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    anthropic_key: str | None = Field(default=None, alias="ANTHROPIC_KEY")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    google_cse_id: str | None = Field(default=None, alias="GOOGLE_CSE_ID")
    google_vertex_project: str | None = Field(default=None, alias="GOOGLE_VERTEX_PROJECT")
    google_vertex_location: str | None = Field(default=None, alias="GOOGLE_VERTEX_LOCATION")
    elevenlabs_api_key: str | None = Field(default=None, alias="ELEVENLABS_API_KEY")
    amadeus_api_key: str | None = Field(default=None, alias="AMADEUS_API_KEY")
    amadeus_api_secret: str | None = Field(default=None, alias="AMADEUS_API_SECRET")
    twilio_account_sid: str | None = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str | None = Field(default=None, alias="TWILIO_AUTH_TOKEN")
    # Jina AI Reader — clean HTML→markdown fetcher used for URL→note,
    # auto-capture, and fetch_url_content.  Works without a key on the
    # free tier (lower rate limits, ~20 req/min).  Set JINA_API_KEY to
    # use your paid quota for higher throughput and better reliability.
    # Sign up at https://jina.ai.
    jina_api_key: str | None = Field(default=None, alias="JINA_API_KEY")
    # HuggingFace read-only token — for downloading GATED eval datasets
    # (e.g. GPQA-Diamond) via scripts/fetch_eval_datasets.py. Read-only + used
    # only at dataset-fetch time; never at agent runtime.
    hf_token_ro: str | None = Field(default=None, alias="HF_TOKEN_RO")
    # Web-search provider API keys (see SEARCH_PROVIDER). Each is a real,
    # supported Search API — unlike the keyless ddgs/legacy paths which scrape
    # DuckDuckGo's frontend and hang when it rate-limits. Brave: an independent
    # index (https://brave.com/search/api). Tavily: LLM/agent-optimised, returns
    # extracted content + an optional synthesised answer (https://tavily.com).
    # Jina search (SEARCH_PROVIDER=jina) uses JINA_API_KEY above. NOTE: unlike
    # the keyless Jina *reader*, the *search* endpoint requires the key (401
    # otherwise — verified live 2026-07-08).
    brave_api_key: str | None = Field(default=None, alias="BRAVE_API_KEY")
    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")
    serper_dev_api_key: str | None = Field(default=None, alias="SERPER_DEV_API_KEY")

    # X / Twitter API v2 bearer token.  When set, x.com/twitter.com STATUS links
    # are fetched via the API instead of the web reader — X has locked down
    # unauthenticated scraping, so the Jina/browser path fails on tweets.
    twitter_api: str | None = Field(default=None, alias="TWITTER_API")

    # Enhanced tweet fetching: expand t.co links to their real URLs, and fetch
    # the author's full self-thread (root + self-replies, in order) when a
    # linked tweet is part of one.  Costs 1–2 extra API calls per tweet fetch
    # and thread search needs an API tier with /2/tweets/search/recent access
    # (Basic and up); the search window only covers the last 7 days, so older
    # threads degrade to the single linked tweet.  Only genuine self-threads
    # are expanded — a reply into someone else's conversation never pulls in
    # the other author's posts.
    twitter_thread_fetch: bool = Field(default=False, alias="TWITTER_THREAD_FETCH")

    # Label fetch_url_content results with their true provenance: posts fetched
    # via a platform's native API (X / Bluesky / Threads) get a SOCIAL POST tag
    # (text/links/handles/metrics are verbatim from the API; claims inside the
    # post remain the author's own, NOT verified) and a "(fetched via ...)"
    # suffix, instead of being mislabeled as scraped web content.  Without this
    # the agent cannot tell an API fetch from a web scrape and misreports its
    # own sources.
    url_fetch_source_tags: bool = Field(default=False, alias="URL_FETCH_SOURCE_TAGS")

    # Threads (Meta) Graph API access token.  When set, threads.net post links are
    # fetched via graph.threads.net.  NOTE: reading third-party public posts needs
    # an app with Advanced Access for threads_basic; otherwise it falls back to the
    # web reader.  (Bluesky needs NO token — its public AppView is open.)
    threads_api: str | None = Field(default=None, alias="THREADS_API")

    # Models / Agents
    agent_name: str = Field(default="Prax", alias="AGENT_NAME")
    base_model: str = Field(default="gpt-5.4-nano", alias="BASE_MODEL")
    default_llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    agent_temperature: float = Field(default=0.7, alias="AGENT_TEMPERATURE")

    # Model tiers — provider-agnostic intelligence levels.
    # Each tier maps to a concrete model name.  Set *_ENABLED=false to disable.
    # The agent sees which tiers are available and can upgrade/downgrade as needed.
    low_model: str = Field(default="gpt-5.4-nano", alias="LOW_MODEL")
    low_enabled: bool = Field(default=True, alias="LOW_ENABLED")
    medium_model: str = Field(default="gpt-5.4-mini", alias="MEDIUM_MODEL")
    medium_enabled: bool = Field(default=True, alias="MEDIUM_ENABLED")
    high_model: str = Field(default="gpt-5.5", alias="HIGH_MODEL")
    high_enabled: bool = Field(default=True, alias="HIGH_ENABLED")
    pro_model: str = Field(default="gpt-5.5-pro", alias="PRO_MODEL")
    pro_enabled: bool = Field(default=False, alias="PRO_ENABLED")

    # Vision / image understanding.  ``vision_provider`` selects the routing:
    # ``openai`` works against the real OpenAI API *and* any OpenAI-compatible
    # endpoint (llama.cpp ``llama-server``, vLLM, Ollama's /v1, LM Studio, …) —
    # set ``VISION_BASE_URL`` to point at the local server.  When a base URL
    # is set, ``VISION_API_KEY`` is optional (most local servers ignore it,
    # but we still pass a placeholder because the OpenAI SDK requires one).
    # Must be a vision-capable CHAT model. The old default gpt-image-1.5 is an
    # image-GENERATION model - every analysis call 500ed at OpenAI.
    vision_model: str = Field(default="gpt-5.4-mini", alias="VISION_MODEL")
    vision_provider: str = Field(default="openai", alias="VISION_PROVIDER")
    # Image GENERATION model (distinct from vision_model, which is analysis-only).
    # Used by the builtin generate_image tool and Library cover generation.
    # Non-image names fall back to dall-e-3 at the call site.
    image_model: str = Field(default="gpt-image-1", alias="IMAGE_MODEL")
    vision_base_url: str | None = Field(default=None, alias="VISION_BASE_URL")
    vision_api_key: str | None = Field(default=None, alias="VISION_API_KEY")

    # Workspace
    workspace_dir: str = Field(default="../workspaces", alias="WORKSPACE_DIR")

    # User identity — which user this Prax instance serves.
    # When set, the sandbox mounts only this user's workspace folder
    # and all sandbox-persistent data lives inside it.
    prax_user_id: str = Field(default="", alias="PRAX_USER_ID")

    # Runtime environment
    running_in_docker: bool = Field(default=False, alias="RUNNING_IN_DOCKER")

    # Sandbox
    sandbox_enabled: bool = Field(
        default=True, alias="SANDBOX_ENABLED",
        description=(
            "Master switch for the Docker sandbox (coding agents, browser, "
            "desktop). Set false to run Prax as a pure harness with no sandbox "
            "tools, spokes, or container dependency."
        ),
    )
    sandbox_image: str = Field(default="prax-sandbox:latest", alias="SANDBOX_IMAGE")
    sandbox_host: str = Field(default="localhost", alias="SANDBOX_HOST")
    sandbox_timeout: int = Field(default=1800, alias="SANDBOX_TIMEOUT")
    sandbox_max_concurrent: int = Field(default=5, alias="SANDBOX_MAX_CONCURRENT")
    sandbox_default_model: str = Field(default="openai/gpt-5.4", alias="SANDBOX_DEFAULT_MODEL")
    sandbox_mem_limit: str = Field(default="1g", alias="SANDBOX_MEM_LIMIT")
    sandbox_cpu_limit: int = Field(default=2_000_000_000, alias="SANDBOX_CPU_LIMIT")
    sandbox_max_rounds: int = Field(default=10, alias="SANDBOX_MAX_ROUNDS")
    # Remote sandbox daemon — empty = in-process (local), the default. Set to a
    # daemon URL (https://host:8843) to drive a sandbox on a remote box.
    sandbox_daemon_url: str = Field(default="", alias="SANDBOX_DAEMON_URL")
    sandbox_daemon_token: str = Field(default="", alias="SANDBOX_DAEMON_TOKEN")
    # TLS verification for the daemon: "true"/"false" or a path to a CA bundle.
    sandbox_tls_verify: str = Field(default="true", alias="SANDBOX_TLS_VERIFY")
    sandbox_client_cert: str = Field(default="", alias="SANDBOX_CLIENT_CERT")  # opt-in mTLS
    sandbox_client_key: str = Field(default="", alias="SANDBOX_CLIENT_KEY")

    # Agent autonomy level — controls how constrained the agent is.
    # guided:    current behavior, all safety gates, prescriptive workflow rules
    # balanced:  removes prescriptive workflow rules, agent uses judgment
    # autonomous: also relaxes recursion limits, lets agent self-upgrade tier
    autonomy: str = Field(default="guided", alias="PRAX_AUTONOMY")

    # Active Inference — Semantic Entropy Gate (Phase 4)
    # When enabled, HIGH-risk tool calls are re-queried k=3 times at T=0.7
    # to detect divergence.  Expensive (3x LLM cost) — off by default.
    semantic_entropy_enabled: bool = Field(
        default=False, alias="ACTIVE_INFERENCE_SEMANTIC_GATE",
    )

    # Agent guardrails
    agent_max_tool_calls: int = Field(
        default=40, alias="AGENT_MAX_TOOL_CALLS",
        description=(
            "Maximum number of tool-call steps (recursion limit) the main agent "
            "can take per user message.  Prevents runaway loops.  Sub-agents and "
            "spokes have their own separate limits."
        ),
    )
    auto_tier_escalation: bool = Field(
        default=False, alias="AUTO_TIER_ESCALATION",
        description=(
            "When a turn thrashes into the tool-call recursion limit — the "
            "signal that the current model is too weak for this task — "
            "automatically rebuild the orchestrator one tier up (low→medium→"
            "high) and retry the turn, instead of failing. Escalation is "
            "turn-local (each turn starts back at the base tier, keeping simple "
            "turns cheap) and capped by AUTO_TIER_ESCALATION_CEILING. Default "
            "off preserves prior behaviour (fail gracefully at the base tier)."
        ),
    )
    auto_tier_escalation_ceiling: str = Field(
        default="high", alias="AUTO_TIER_ESCALATION_CEILING",
        description="Top tier auto-escalation may climb to (low|medium|high|pro).",
    )
    agent_max_delegation_depth: int = Field(
        default=4, alias="AGENT_MAX_DELEGATION_DEPTH",
        description=(
            "Maximum nesting depth for agent delegation chains. "
            "Orchestrator=0, first sub-agent=1, etc.  Prevents infinite "
            "recursive delegation.  A depth of 4 allows orchestrator → spoke "
            "→ sub-agent → sub-sub-agent."
        ),
    )
    agent_run_timeout: int = Field(
        default=300, alias="AGENT_RUN_TIMEOUT",
        description=(
            "Idle timeout (seconds) for a single agent.run() invocation. "
            "If no tool/span/status heartbeat is observed within this time, "
            "the run is abandoned. Healthy long-running work can continue "
            "until agent_run_max_timeout."
        ),
    )
    agent_run_max_timeout: int = Field(
        default=1800, alias="AGENT_RUN_MAX_TIMEOUT",
        description=(
            "Maximum wall-clock runtime (seconds) for one agent.run() "
            "invocation even if heartbeat activity continues. Prevents "
            "unbounded API spend while allowing long healthy tasks."
        ),
    )
    web_search_timeout_s: int = Field(
        default=0, alias="WEB_SEARCH_TIMEOUT_S",
        description=(
            "Wall-clock timeout (seconds) for background_search_tool. The "
            "DuckDuckGo search backends occasionally hang instead of erroring, "
            "which parks the whole turn indefinitely (observed live "
            "2026-07-08 via a dead leta backend). When >0 the search is "
            "abandoned after this many seconds and the agent gets a clear "
            "error string instead. 0 (default) preserves prior no-timeout "
            "behavior."
        ),
    )
    search_provider: str = Field(
        default="legacy", alias="SEARCH_PROVIDER",
        description=(
            "Web-search backend for background_search_tool. Keyless: 'legacy' "
            "(default, prior behaviour) uses langchain_community's "
            "DuckDuckGoSearchRun on the sunset duckduckgo-search package — "
            "backends observed hanging/erroring; 'ddgs' uses the maintained "
            "ddgs successor directly (better rotation, still a scraper of "
            "DuckDuckGo's frontend, so still fragile). Keyed, real Search APIs "
            "(recommended for reliability — predictable rate limits + timeouts "
            "instead of silent hangs): 'brave' (BRAVE_API_KEY, independent "
            "index), 'tavily' (TAVILY_API_KEY, LLM/agent-optimised, includes a "
            "synthesised answer), 'jina' (JINA_API_KEY — the search endpoint, "
            "unlike the keyless Jina reader, rejects keyless requests with 401), "
            "'serper' (SERPER_DEV_API_KEY — Google results via serper.dev, "
            "prepaid so it can't overspend; surfaces an answer/knowledge box "
            "when present). A keyed provider with no key returns an actionable "
            "message rather than failing silently."
        ),
    )
    search_max_results: int = Field(
        default=6, alias="SEARCH_MAX_RESULTS",
        description="Result count for the keyed/ddgs search providers.",
    )
    llm_request_timeout: int = Field(
        default=300, alias="LLM_REQUEST_TIMEOUT",
        description=(
            "Per-call HTTP timeout (seconds) for LLM provider requests.  "
            "Prevents a stalled connection from hanging the orchestrator "
            "indefinitely — without this, a hung OpenAI/Anthropic call "
            "blocks the entire agent turn (and the agent_run_timeout "
            "check above doesn't fire because the invoke never returns)."
        ),
    )
    llm_fallback_enabled: bool = Field(
        default=False, alias="LLM_FALLBACK_ENABLED",
        description=(
            "When true, the orchestrator transparently fails over to an "
            "alternate LLM provider after a provider-side failure (rate "
            "limit, overload, connection error, or an OPEN circuit breaker) "
            "instead of surfacing the error to the user.  Off by default so "
            "single-provider deployments behave exactly as before."
        ),
    )
    llm_fallback_chain: str = Field(
        default="", alias="LLM_FALLBACK_CHAIN",
        description=(
            "Ordered, comma-separated 'provider[:model]' fallback chain used "
            "when LLM_FALLBACK_ENABLED is set — e.g. "
            "'anthropic:claude-sonnet-4-20250514,google:gemini-2.5-pro'.  "
            "When empty, the chain is auto-derived from whichever providers "
            "have credentials configured (excluding the primary)."
        ),
    )
    llm_provider_denylist_enabled: bool = Field(
        default=True, alias="LLM_PROVIDER_DENYLIST_ENABLED",
        description=(
            "When cross-provider failover is on (LLM_FALLBACK_ENABLED), a "
            "*terminal* provider failure — auth / billing / access / "
            "decommissioned, which a retry won't fix — denylists that provider "
            "from the pool (so it isn't hammered every turn) and surfaces a "
            "user-facing notice explaining the likely cause (e.g. an unpaid "
            "bill or a revoked key), instead of silently retrying. Set false to "
            "treat every failure as transient. No effect when LLM_FALLBACK_ENABLED "
            "is off."
        ),
    )
    llm_provider_denylist_cooldown_seconds: int = Field(
        default=1800, alias="LLM_PROVIDER_DENYLIST_COOLDOWN_SECONDS",
        description=(
            "How long a terminally-failed provider stays denylisted before Prax "
            "re-probes it once (default 1800s = 30 min). 0 = stay denylisted "
            "until the process restarts."
        ),
    )
    llm_failover_backoff_ms: int = Field(
        default=0, alias="LLM_FAILOVER_BACKOFF_MS",
        description=(
            "Milliseconds to wait before a cross-provider failover retry, with "
            "±25%% jitter, so a shared transient blip (a regional network wobble, "
            "a rate-limit wave) doesn't turn into a tight retry storm across the "
            "provider chain. 0 (default) = fail over immediately, i.e. prior "
            "behaviour. Only applies when LLM_FALLBACK_ENABLED is on. "
            "(FailureAtlas: retry storms / rate-limit deadlocks.)"
        ),
    )
    llm_record_answering_model: bool = Field(
        default=False, alias="LLM_RECORD_ANSWERING_MODEL",
        description=(
            "When true, after each successful turn the orchestrator reads the "
            "model that ACTUALLY answered from the response metadata and, if it "
            "differs from the model requested, logs a warning + telemetry event "
            "('silent model substitution'). Guards against a provider or gateway "
            "quietly serving a different (often cheaper/weaker) model than asked "
            "for — a silent, HTTP-200 corruption. Off by default (pure "
            "observability, no behaviour change). (FailureAtlas.)"
        ),
    )
    recovery_context_injection_enabled: bool = Field(
        default=True, alias="RECOVERY_CONTEXT_INJECTION",
        description=(
            "When true, the structured multi-perspective failure diagnosis "
            "(error_recovery.build_recovery_context) is injected back into "
            "the message stream on an orchestrator retry so the model "
            "re-plans the current trajectory with the diagnosis in context, "
            "rather than blindly re-running the failed step."
        ),
    )
    autonomy_followthrough_enabled: bool = Field(
        default=True, alias="AUTONOMY_FOLLOWTHROUGH_ENABLED",
        description=(
            "When true (the default), the orchestrator enforces follow-through: "
            "(1) if the agent produced an artifact (screenshot/download/file) then "
            "merely OFFERED to use it ('I can take the next step and inspect it'), "
            "it is nudged to actually take that step; (2) a plan-housekeeping ack "
            "(e.g. 'the plan is cleared') is never allowed to be the user-facing "
            "reply — the agent is re-prompted to answer the real request. Default "
            "ON deliberately: the user shouldn't have to keep telling Prax to go. "
            "Set false to disable (kill switch)."
        ),
    )
    retrieval_query_expansion_enabled: bool = Field(
        default=False, alias="RETRIEVAL_QUERY_EXPANSION",
        description=(
            "When true, hybrid memory retrieval generates a few paraphrase / "
            "HyDE query variants (cheap LOW-tier LLM), embeds each, and unions "
            "their dense hits before RRF fusion — a recall win for queries "
            "whose phrasing differs from how the memory was stored. "
            "Eval gate 2026-07-08 (docs/research/flag-eval-campaign-2026-07-08.md): DEFERRED — the capability "
            "suite has no retrieval coverage to measure lift, and this "
            "adds an LLM call per query; needs a retrieval-specific eval."
        ),
    )
    retrieval_query_expansion_n: int = Field(
        default=3, alias="RETRIEVAL_QUERY_EXPANSION_N",
        description="Number of query variants (including the original) to union when expansion is on.",
    )
    retrieval_rerank_enabled: bool = Field(
        default=False, alias="RETRIEVAL_RERANK",
        description=(
            "When true, a relevance-rerank pass (LLM-judge) re-scores the "
            "fused candidate set against the query before truncating to "
            "top_k, so low-relevance-but-recent/important memories can't "
            "outrank on-topic ones. "
            "Eval gate 2026-07-08 (docs/research/flag-eval-campaign-2026-07-08.md): DEFERRED — no suite coverage "
            "to measure lift; adds an LLM call per query."
        ),
    )
    retrieval_rerank_candidates: int = Field(
        default=20, alias="RETRIEVAL_RERANK_CANDIDATES",
        description="Max number of top fused candidates to send through the rerank pass.",
    )
    ssrf_protection_enabled: bool = Field(
        default=True, alias="SSRF_PROTECTION_ENABLED",
        description=(
            "When true (default), outbound HTTP from the plugin gateway and the "
            "URL reader is validated against an SSRF guard: only http/https, and "
            "the host must not be (or resolve to) a private/loopback/link-local/"
            "reserved address — blocking access to localhost and the cloud "
            "metadata endpoint (169.254.169.254). Redirects are re-validated."
        ),
    )
    ssrf_allowed_hosts: str = Field(
        default="", alias="SSRF_ALLOWED_HOSTS",
        description=(
            "Comma-separated hostnames/IPs allowed through the SSRF guard even if "
            "they resolve to a private range — e.g. 'localhost,127.0.0.1' for local "
            "development against a self-hosted service."
        ),
    )
    mcp_server_enabled: bool = Field(
        default=False, alias="MCP_SERVER_ENABLED",
        description=(
            "When true, expose a curated subset of Prax tools to OTHER agents over "
            "the Model Context Protocol (JSON-RPC at POST /mcp). Fail-closed: the "
            "endpoint is only registered when a bearer token is also set."
        ),
    )
    mcp_bearer_token: str = Field(
        default="", alias="MCP_BEARER_TOKEN", repr=False,
        description="Required bearer token for the MCP endpoint (constant-time checked).",
    )
    mcp_user_id: str = Field(
        default="", alias="MCP_USER_ID",
        description=(
            "The Prax user identity the MCP server acts as — every exposed tool runs "
            "under this user's context (workspace, memory, approved secrets)."
        ),
    )
    mcp_tool_allowlist: str = Field(
        default="", alias="MCP_TOOL_ALLOWLIST",
        description=(
            "Comma-separated tool names exposed to the legacy single-token client "
            "(MCP_BEARER_TOKEN). Empty uses a small safe read-only default set. "
            "HIGH-risk tools are refused even if listed."
        ),
    )
    mcp_clients_path: str = Field(
        default="", alias="MCP_CLIENTS_PATH",
        description=(
            "Path to a JSON MCP client registry ({\"clients\":[{name, token|"
            "token_sha256, user_id, allow}]}) for PER-CALLER identity: each "
            "caller's token maps to its own Prax user_id and tool allowlist. "
            "Merged with the legacy single-token client when MCP_BEARER_TOKEN is set."
        ),
    )
    mcp_token_expiry_enabled: bool = Field(
        default=False, alias="MCP_TOKEN_EXPIRY_ENABLED",
        description=(
            "When true, enforce an optional 'expires_at' (ISO-8601) on MCP client "
            "tokens — in MCP_CLIENTS_PATH entries, or MCP_TOKEN_EXPIRES_AT for the "
            "legacy single-token client. Expired tokens are rejected exactly as if "
            "invalid. Default OFF → tokens never expire (backward compatible)."
        ),
    )
    mcp_token_expires_at: str = Field(
        default="", alias="MCP_TOKEN_EXPIRES_AT",
        description=(
            "Optional ISO-8601 expiry for the legacy single-token client "
            "(MCP_BEARER_TOKEN), e.g. 2026-12-31T00:00:00Z. Only enforced when "
            "MCP_TOKEN_EXPIRY_ENABLED is true."
        ),
    )
    share_link_ttl_enabled: bool = Field(
        default=False, alias="SHARE_LINK_TTL_ENABLED",
        description=(
            "When true, public share links (workspace_share_file, course/note "
            "publish) get an expiry stamped at creation and are auto-revoked "
            "(404 + purged on next listing) once expired. Default OFF → shares "
            "live until explicitly revoked (backward compatible)."
        ),
    )
    share_link_ttl_seconds: int = Field(
        default=604800, alias="SHARE_LINK_TTL_SECONDS",
        description=(
            "Lifetime in seconds for new public share links when "
            "SHARE_LINK_TTL_ENABLED is true (default 604800 = 7 days). "
            "Re-publishing a course/note renews its lease."
        ),
    )
    knowledge_hybrid_enabled: bool = Field(
        default=True, alias="KNOWLEDGE_HYBRID_ENABLED",
        description=(
            "When true (default), knowledge-graph concept search fuses semantic "
            "vector retrieval (Qdrant) with keyword matching instead of relying "
            "on substring matching alone.  Degrades automatically to keyword "
            "search when Qdrant/the embedder is unavailable."
        ),
    )
    eval_auditor_enabled: bool = Field(
        default=False, alias="EVAL_AUDITOR_ENABLED",
        description=(
            "When true, the golden suite runs a high-tier supervising AUDITOR "
            "that re-checks only the criteria the cheap (low-tier) judge passed "
            "and may veto impressive-but-vacuous answers (1->0). Eval-time only, "
            "off by default — see docs/research/diffuse-ai-control-judge-robustness.md."
        ),
    )
    eval_nightly_enabled: bool = Field(
        default=False, alias="EVAL_NIGHTLY_ENABLED",
        description=(
            "When true, a nightly scheduler job samples recent execution "
            "traces, scores them with a reference-free judge, and publishes "
            "aggregate quality to Prometheus (prax_eval_quality) — continuous "
            "drift detection on live traffic."
        ),
    )
    eval_nightly_sample_size: int = Field(
        default=25, alias="EVAL_NIGHTLY_SAMPLE_SIZE",
        description="How many recent traces the nightly live-traffic eval samples.",
    )
    eval_nightly_cron: str = Field(
        default="0 4 * * *", alias="EVAL_NIGHTLY_CRON",
        description="5-field cron expression for the nightly live-traffic eval job.",
    )
    self_regen_enabled: bool = Field(
        default=False, alias="SELF_REGEN_ENABLED",
        description=(
            "Gate for the self-regeneration loop (#29) AUTO-APPLYING a winning "
            "system-prompt overlay. Off by default: run_self_regen still produces "
            "reviewable proposals + full lineage, but only auto-applies when this "
            "is true AND apply=True (graded autonomy). The verifier (capability "
            "suite) and overseer (anti-spike auditor) always live outside the "
            "editable surface — see prax/eval/self_regen.py."
        ),
    )
    eval_task_timeout_s: int = Field(
        default=0, alias="PRAX_EVAL_TASK_TIMEOUT_S",
        description=(
            "Per-task wall-clock cap (seconds) for benchmark runs (GAIA, "
            "capability). 0 = DISABLED (no kill) — the right default for a slow "
            "local model on ds4/vLLM/Ollama where a single task can legitimately "
            "take minutes to hours and you run the suite overnight or over days. "
            "Set a positive value only as a safety rail against hung tool calls."
        ),
    )
    eval_concurrency: int = Field(
        default=1, alias="PRAX_EVAL_CONCURRENCY",
        description=(
            "Parallel tasks in a benchmark batch. 1 (default) suits a single "
            "local model server; raise it only for API models that tolerate "
            "concurrent requests."
        ),
    )
    eval_usd_in_per_1m: float = Field(
        default=0.0, alias="PRAX_EVAL_USD_IN_PER_1M",
        description="USD per 1M prompt tokens for the cost rail. 0 (default) = a "
                    "local/self-hosted model (cost in tokens + wall-time, not $).",
    )
    eval_usd_out_per_1m: float = Field(
        default=0.0, alias="PRAX_EVAL_USD_OUT_PER_1M",
        description="USD per 1M completion tokens for the cost rail. 0 = local model.",
    )
    checkpoint_backend: str = Field(
        default="memory", alias="CHECKPOINT_BACKEND",
        description=(
            "LangGraph checkpointer backend: 'memory' (default, ephemeral) or "
            "'sqlite' (durable — checkpoint data survives a process restart). "
            "Falls back to memory if the durable backend can't be constructed."
        ),
    )
    checkpoint_db_path: str = Field(
        default=".prax/checkpoints.sqlite", alias="CHECKPOINT_DB_PATH",
        description="On-disk path for the SQLite checkpointer when CHECKPOINT_BACKEND=sqlite.",
    )
    checkpoint_resume_enabled: bool = Field(
        default=False, alias="CHECKPOINT_RESUME_ENABLED",
        description=(
            "When true, a failed/timed-out turn's checkpoints are retained (for "
            "CHECKPOINT_RESUME_TTL seconds) instead of purged, so the user can "
            "resume from the failure point — skipping completed steps — instead "
            "of restarting the whole turn."
        ),
    )
    checkpoint_resume_ttl_seconds: int = Field(
        default=3600, alias="CHECKPOINT_RESUME_TTL",
        description="How long (seconds) a failed turn stays resumable.",
    )
    checkpoint_resume_state_path: str = Field(
        default=".prax/resumable.json", alias="CHECKPOINT_RESUME_STATE_PATH",
        description=(
            "Where the resumable-turn pointers are persisted (only when "
            "CHECKPOINT_RESUME_ENABLED).  Lets a failed turn be resumed after a "
            "process restart with CHECKPOINT_BACKEND=sqlite.  Delete this file "
            "to discard all pending resumes."
        ),
    )
    unknown_tool_high_risk: bool = Field(
        default=False, alias="UNKNOWN_TOOL_HIGH_RISK",
        description=(
            "Deny-by-default: when true, a tool with no static risk "
            "classification (and not an imported plugin) defaults to HIGH risk "
            "— requiring confirmation — instead of MEDIUM-and-run. "
            "Eval gate 2026-07-08 (docs/research/flag-eval-campaign-2026-07-08.md): REJECTED — measured "
            "correctness regression (blocked a needed tool; a capability "
            "case failed). Keep off until tuned; don't flip without new "
            "evidence."
        ),
    )
    high_risk_scoped_confirm: bool = Field(
        default=False, alias="HIGH_RISK_SCOPED_CONFIRM",
        description=(
            "When true, confirming a HIGH-risk tool unlocks ONLY that tool for "
            "the turn, instead of unlocking every HIGH-risk tool after the "
            "first confirmation. "
            "Eval gate 2026-07-08 (docs/research/flag-eval-campaign-2026-07-08.md): REJECTED alongside "
            "UNKNOWN_TOOL_HIGH_RISK (measured as a pair) — correctness "
            "regression. Keep off until tuned."
        ),
    )
    lethal_trifecta_guard: bool = Field(
        default=False, alias="LETHAL_TRIFECTA_GUARD",
        description=(
            "When true, the capability gateway enforces the lethal-trifecta "
            "invariant: once a turn has ingested UNTRUSTED content (browser/"
            "research/fetch) AND read PRIVATE data (memory/knowledge/workspace), "
            "any EXTERNAL-SINK tool (send/share/publish/browser-action) is "
            "escalated to HIGH and requires confirmation — the architectural "
            "defense against indirect prompt-injection exfiltration. Default off "
            "(prior behaviour); opt in for high-security deployments."
        ),
    )
    agent_middleware_enabled: bool = Field(
        default=False, alias="AGENT_MIDDLEWARE_ENABLED",
        description=(
            "When true, agent loops are built with in-loop LangChain middleware "
            "(prax/agent/loop_middleware.py): untrusted-source tool results are "
            "provenance-tainted before re-entering the model's context, and the "
            "trace heartbeat is touched on every model step — the in-loop "
            "counterpart to the perimeter governance wrapper. Default off "
            "(prior behaviour: no middleware, identical compiled graph). "
            "Eval gate 2026-07-08 (docs/research/flag-eval-campaign-2026-07-08.md): "
            "FLIPPED ON in the recommended config (.env-example) — no capability "
            "regression, ~7% fewer tokens; measured injection lift was within "
            "noise at the sample size, so the flip rests on no-regression + "
            "cost + defense-in-depth design."
        ),
    )
    tool_memoize_enabled: bool = Field(
        default=False, alias="TOOL_MEMOIZE_ENABLED",
        description=(
            "When true, adds the IdempotentToolCache middleware to the agent loop: "
            "within a single turn, an identical repeat of a pure, side-effect-free "
            "READ (web search/fetch, memory/workspace/conversation/trace lookups) "
            "returns the prior result instead of re-executing — saving the latency, "
            "external call, and tokens of a redundant fetch. Correct by construction: "
            "the cache is per-invoke (never reused in a later turn) and ONLY "
            "idempotent reads are eligible (run_python/shell/writes/browser "
            "navigation always pass through). The structural 'verify once' lever "
            "(prompt hints raised verification but not efficiency — see the A/B in "
            "docs/research/verify-and-commit-discipline.md). Default off; independent "
            "of AGENT_MIDDLEWARE_ENABLED."
        ),
    )
    claim_audit_attended_quarantine: bool = Field(
        default=False, alias="CLAIM_AUDIT_ATTENDED_QUARANTINE",
        description=(
            "When true, ungrounded-claim warnings are appended to the "
            "user-facing reply on attended (interactive) turns too — not only "
            "posted to the internal Auditor channel. Scheduled turns always "
            "quarantine regardless of this flag. "
            "Eval gate 2026-07-08 (docs/research/flag-eval-campaign-2026-07-08.md): DEFERRED — capability-clean "
            "but the sycophancy A/B was inconclusive (run aborted on a "
            "dead search backend); rerun before flipping."
        ),
    )
    verify_published_links: bool = Field(
        default=False, alias="VERIFY_PUBLISHED_LINKS",
        description=(
            "When true, save_and_publish does a best-effort HEAD/GET on the note "
            "URL right after publishing and ANNOTATES the result with a warning if "
            "it does not resolve — so Prax never hands the user a link it hasn't "
            "confirmed works (the journalclub 404 incident). Never blocks the save; "
            "default off (needs the serving route reachable + network)."
        ),
    )
    epistemic_vigilance_enabled: bool = Field(
        default=False, alias="EPISTEMIC_VIGILANCE_ENABLED",
        description=(
            "When true, appends an epistemic-vigilance principle to the system "
            "prompt: pause and verify a user's factual/health/safety PREMISE before "
            "accepting it, and correct false/unsafe premises instead of "
            "accommodating them (anti-sycophancy). Weighted by source reliability, "
            "with low false-positives (don't over-challenge correct premises). "
            "Inspired by 'Accommodation and Epistemic Vigilance' (arXiv 2601.04435). "
            "Default off; grade with the `sycophancy` benchmark adapter."
        ),
    )
    tool_economy_enabled: bool = Field(
        default=False, alias="TOOL_ECONOMY_ENABLED",
        description=(
            "When true, appends a 'tool economy' principle to the system prompt: "
            "answer from your own knowledge when a question doesn't need external "
            "information, and reserve search/fetch/browser tools for what you "
            "genuinely lack (current facts, user/system data, given documents, "
            "genuinely-uncertain claims). A deliberate counterweight to the "
            "persistence/'try another source' persona, which otherwise leads the "
            "agent to over-fetch on closed-book questions until the turn balloons "
            "and times out. Default off; grade with the closed-book benchmark "
            "adapters (gsm8k/mmlu_pro/gpqa/truthfulqa) + the capability suite to "
            "confirm it doesn't suppress genuinely-needed tool use."
        ),
    )
    budget_aware_answering_enabled: bool = Field(
        default=False, alias="BUDGET_AWARE_ANSWERING_ENABLED",
        description=(
            "When true, appends a 'budget-aware answering' principle to the system "
            "prompt: reason efficiently and COMMIT your conclusion within the "
            "time/token budget instead of spiralling into endless re-analysis or "
            "over-tooling until the turn times out with nothing committed. Crucially "
            "honesty-preserving — the committed conclusion may be an honest \"I don't "
            "know\" (never a fabricated guess to look decisive), so it does NOT reward "
            "bluffing or spike multiple-choice benchmarks; it only recovers cases "
            "where the agent genuinely reached an answer but spiralled before "
            "committing it. Addresses the dominant hard-problem failure mode on GPQA/"
            "MMLU-Pro (180s timeouts, no answer). Default off; grade with the "
            "closed-book benchmarks + a hallucination/abstention check to confirm it "
            "doesn't induce premature or fabricated answers."
        ),
    )
    verify_discipline_enabled: bool = Field(
        default=False, alias="VERIFY_DISCIPLINE_ENABLED",
        description=(
            "When true, appends a 'verify what your answer rests on — with a tool, "
            "once' principle to the system prompt: when the conclusion depends on a "
            "load-bearing, tool-checkable claim (a calculation, a symbolic result, "
            "code output, a lookup), verify it WITH the tool rather than mental "
            "arithmetic — and verify it ONCE, not repeatedly. Targets the observed "
            "process variance where the same task is sometimes hand-asserted "
            "(under-verified) and sometimes over-verified (redundant tool calls). "
            "Scoped against tool economy — it's about closing a checkable load-bearing "
            "gap, not general tool use. Default off; grade with the closed-book "
            "benchmarks AND the trace-grade (prax/eval/trace_grade.py) to confirm it "
            "raises verification without wrecking efficiency. Design: "
            "docs/research/verify-and-commit-discipline.md."
        ),
    )
    spiral_recovery_enabled: bool = Field(
        default=False, alias="SPIRAL_RECOVERY_ENABLED",
        description=(
            "When true, adds the 'steadying counsel' middleware to the agent loop: "
            "detects a spiral in flight (repeating a tool call, burning the tool-call "
            "budget, or circling without converging) and injects a calm, data-driven "
            "'pause and try a different route' into the next model call — the "
            "structural rescue for loops that run to timeout with nothing committed. "
            "Honesty-preserving (tells the agent an honest 'I don't know' is valid, "
            "never to fabricate) and rate-limited so it nudges, not nags. Requires the "
            "middleware seam; independent of AGENT_MIDDLEWARE_ENABLED. Default off; "
            "grade with the closed-book benchmarks (timeout/no-answer rate) + the "
            "capability suite."
        ),
    )
    intent_clarification_enabled: bool = Field(
        default=False, alias="INTENT_CLARIFICATION_ENABLED",
        description=(
            "When true, a cheap LOW-tier pre-flight gate runs before the main "
            "agent loop: if a request is BOTH ambiguous AND potentially "
            "irreversible/costly, it returns a single clarifying question "
            "instead of guessing. Biased strongly toward proceeding. "
            "Eval gate 2026-07-08 (docs/research/flag-eval-campaign-2026-07-08.md): REJECTED — +11% tokens with "
            "no pass-rate gain. Leave off unless traffic is "
            "ambiguity-heavy; don't flip without new evidence."
        ),
    )
    prompt_selectivity_enabled: bool = Field(
        default=False, alias="PROMPT_SELECTIVITY_ENABLED",
        description=(
            "When true, topic-specific optional sections of the orchestrator "
            "system prompt (e.g. document pipelines, math/LaTeX, teaching) are "
            "dropped when the request shows no signal of needing them — "
            "shrinking the base prompt on simple turns. Off by default ships "
            "the full prompt unchanged. "
            "Eval gate 2026-07-08 (docs/research/flag-eval-campaign-2026-07-08.md): FLIPPED ON in the recommended "
            "config (.env-example) — no regression, ~2% fewer tokens."
        ),
    )

    @property
    def sandbox_persistent(self) -> bool:
        """True when the sandbox is always-on (docker-compose deployment)."""
        return self.running_in_docker

    @property
    def sandbox_available(self) -> bool:
        """True when sandbox tooling should be wired into the agent.

        The one switch callers check before registering sandbox tools/spokes,
        running ``run_python`` in the sandbox, or probing sandbox health.
        """
        return self.sandbox_enabled

    @property
    def sandbox_remote(self) -> bool:
        """True when the sandbox is driven via a remote control daemon."""
        return bool(self.sandbox_daemon_url.strip())

    # Lean 4 proof-checking tool (lean_check in the sandbox spoke). Compiles Lean
    # source in the sandbox container and runs cdc-lean's axiom-audit trust gate.
    # Needs the Lean toolchain in the sandbox image (elan at /opt/elan); the tool
    # degrades with a clear message when the toolchain or sandbox is absent.
    # Assessment: docs/research/cdc-lean-teach-prax-lean.md
    lean_tools_enabled: bool = Field(default=False, alias="LEAN_TOOLS_ENABLED")

    # data_query — DuckDB SQL / number-crunching over CSV/Parquet/JSON in the
    # sandbox. Needs duckdb + pandas in the sandbox image (/opt/prax-venv); the
    # tool degrades with a clear message when the libs or sandbox are absent.
    data_tools_enabled: bool = Field(default=False, alias="DATA_TOOLS_ENABLED")

    # Fine-tuning / Local Models (optional — GPU required)
    finetune_enabled: bool = Field(default=False, alias="FINETUNE_ENABLED")
    vllm_base_url: str = Field(default="http://localhost:8000/v1", alias="VLLM_BASE_URL")
    local_model: str = Field(default="Qwen/Qwen3-8B", alias="LOCAL_MODEL")
    finetune_base_model: str = Field(default="unsloth/Qwen3-8B-unsloth-bnb-4bit", alias="FINETUNE_BASE_MODEL")
    finetune_output_dir: str = Field(default="./adapters", alias="FINETUNE_OUTPUT_DIR")
    finetune_max_steps: int = Field(default=60, alias="FINETUNE_MAX_STEPS")
    finetune_learning_rate: float = Field(default=2e-4, alias="FINETUNE_LEARNING_RATE")
    finetune_lora_rank: int = Field(default=16, alias="FINETUNE_LORA_RANK")

    # Cloud GPU power control (optional, default off — plug-and-play). Prax holds
    # ONLY a bearer token to a user-run power-broker that can do nothing but
    # start/stop one pre-provisioned GPU. Unset ⇒ no GPU-power capability (the
    # gpu_power plugin registers no tools). See docs/guides/cloud-gpu.md.
    gpu_provider: str = Field(default="", alias="GPU_PROVIDER",
        description="GPU power backend: none|broker|aws|gcp. Empty = no capability.")
    gpu_power_broker_url: str = Field(default="", alias="GPU_POWER_BROKER_URL",
        description="URL of the least-privilege power-broker (on/off only).")
    gpu_power_broker_token: str = Field(default="", alias="GPU_POWER_BROKER_TOKEN", repr=False,
        description="Bearer token for the power-broker — the ONLY GPU credential Prax holds.")
    gpu_instance_id: str = Field(default="", alias="GPU_INSTANCE_ID",
        description="Optional instance label for status (the broker hard-codes the real ID).")

    # Browser
    browser_headless: bool = Field(default=True, alias="BROWSER_HEADLESS")
    browser_timeout: int = Field(default=30000, alias="BROWSER_TIMEOUT")
    sites_credentials_path: str | None = Field(default=None, alias="SITES_CREDENTIALS_PATH")
    browser_profile_dir: str | None = Field(default=None, alias="BROWSER_PROFILE_DIR")
    browser_vnc_enabled: bool = Field(default=False, alias="BROWSER_VNC_ENABLED")
    browser_vnc_base_port: int = Field(default=5900, alias="BROWSER_VNC_BASE_PORT")
    # CDP endpoint — when set, Playwright connects to this Chrome instance
    # instead of launching its own.  In Docker this points to the sandbox Chrome,
    # unifying the agent's browser with TeamWork's screencast.
    browser_cdp_url: str | None = Field(default=None, alias="BROWSER_CDP_URL")
    # Sandbox-only browsing: never run a Chromium on the harness host.  When the
    # sandbox Chrome (BROWSER_CDP_URL) is unreachable, browser tools report the
    # sandbox as down instead of silently falling back to a locally-launched
    # browser, and manual logins route to the TeamWork browser panel instead of
    # a host Xvfb/x11vnc session.  Keeps the harness host dedicated to
    # orchestration — browser rendering stays in the sandbox container.
    browser_sandbox_only: bool = Field(default=False, alias="BROWSER_SANDBOX_ONLY")

    # Self-improvement (code modification via PRs)
    self_improve_enabled: bool = Field(default=False, alias="SELF_IMPROVE_ENABLED")
    self_improve_repo_path: str | None = Field(default=None, alias="SELF_IMPROVE_REPO_PATH")
    self_improve_agent: str = Field(
        default="claude-code", alias="SELF_IMPROVE_AGENT",
        description=(
            "Preferred coding agent for self-improvement tasks in the sandbox. "
            "Options: claude-code (Anthropic), codex (OpenAI), opencode (multi-provider). "
            "All three are installed in the sandbox and use provider API tokens — "
            "monitor your API spend when self-improvement is enabled."
        ),
    )
    git_author_email: str = Field(default="prax@localhost", alias="GIT_AUTHOR_EMAIL")
    git_author_name: str = Field(default="Prax", alias="GIT_AUTHOR_NAME")

    # Prax SSH key — base64-encoded private key for pushing workspaces
    prax_ssh_key_b64: str | None = Field(default=None, alias="PRAX_SSH_KEY_B64")

    # Legacy plugin repo settings (deprecated — use PRAX_SSH_KEY_B64 + workspace push)
    plugin_repo_url: str | None = Field(default=None, alias="PLUGIN_REPO_URL")
    plugin_repo_ssh_key_b64: str | None = Field(default=None, alias="PLUGIN_REPO_SSH_KEY_B64")
    plugin_repo_branch: str = Field(default="plugins", alias="PLUGIN_REPO_BRANCH")
    plugin_repo_local_path: str = Field(default="./plugin_repo", alias="PLUGIN_REPO_LOCAL_PATH")

    @property
    def ssh_key_b64(self) -> str | None:
        """Return the SSH key, preferring PRAX_SSH_KEY_B64 over the legacy setting."""
        return self.prax_ssh_key_b64 or self.plugin_repo_ssh_key_b64

    # Memory system (vector store + knowledge graph)
    memory_enabled: bool = Field(default=True, alias="MEMORY_ENABLED")
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="prax-memory", alias="NEO4J_PASSWORD")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    embedding_provider: str = Field(default="openai", alias="EMBEDDING_PROVIDER")
    ollama_base_url: str = Field(
        default="http://localhost:11434", alias="OLLAMA_BASE_URL",
        description="Ollama endpoint for local embeddings (when EMBEDDING_PROVIDER=ollama).",
    )
    memory_consolidation_interval: int = Field(
        default=3600, alias="MEMORY_CONSOLIDATION_INTERVAL",
        description="Seconds between automatic consolidation runs.",
    )
    memory_stm_max_entries: int = Field(
        default=50, alias="MEMORY_STM_MAX_ENTRIES",
        description="Max scratchpad entries before LLM compaction kicks in.",
    )
    memory_decay_halflife_days: float = Field(
        default=7.0, alias="MEMORY_DECAY_HALFLIFE_DAYS",
        description="Half-life in days for Ebbinghaus-style memory importance decay.",
    )

    # Observability (OTel tracing, Prometheus metrics, Grafana dashboards)
    observability_enabled: bool = Field(default=False, alias="OBSERVABILITY_ENABLED")
    grafana_url: str = Field(default="", alias="GRAFANA_URL")  # e.g. "http://localhost:3001"
    # LGTM datasource endpoints — populated by docker-compose in full mode,
    # empty in lite mode.  The obs_* agent tools degrade gracefully when
    # these are empty ("observability not available in this deployment mode").
    loki_url: str = Field(default="", alias="LOKI_URL")  # e.g. "http://loki:3100"
    prometheus_url: str = Field(default="", alias="PROMETHEUS_URL")  # e.g. "http://prometheus:9090"
    tempo_url: str = Field(default="", alias="TEMPO_URL")  # e.g. "http://tempo:3200"

    # Health monitoring watchdog — periodic self-checks every N turns.
    # Set to false to disable for minimal RAM / lightweight deployments.
    health_monitor_enabled: bool = Field(default=True, alias="HEALTH_MONITOR_ENABLED")

    # Task runner — background worker that picks up Kanban and todo
    # items assigned to Prax and executes them via a synthetic
    # orchestrator turn.  Opt-in per deployment.  Polls every
    # ``task_runner_interval_minutes`` minutes.
    task_runner_enabled: bool = Field(default=False, alias="TASK_RUNNER_ENABLED")
    task_runner_interval_minutes: int = Field(
        default=5, alias="TASK_RUNNER_INTERVAL_MINUTES",
    )

    # TeamWork integration (web UI) — disabled by default for standalone use.
    # Docker Compose sets TEAMWORK_ENABLED=true automatically.
    teamwork_enabled: bool = Field(default=False, alias="TEAMWORK_ENABLED")
    teamwork_url: str = Field(default="", alias="TEAMWORK_URL")  # e.g. "http://teamwork:8000"
    teamwork_api_key: str = Field(default="", alias="TEAMWORK_API_KEY")
    teamwork_user_phone: str = Field(default="", alias="TEAMWORK_USER_PHONE")
    # User-facing base URL for TeamWork — what Prax pastes into chat when
    # surfacing course/note links.  Defaults to the local Docker port mapping;
    # users on Tailscale should set this to https://<host>.<tailnet>.ts.net so
    # links work from their laptop without rewriting.
    teamwork_base_url: str = Field(default="http://localhost:8000", alias="TEAMWORK_BASE_URL")
    # When TEAMWORK_BASE_URL is unset/localhost, auto-derive the public base URL
    # for shareable links from the live deployment (Tailscale MagicDNS / ngrok)
    # so a Tailscale deploy "just works" without editing .env.  An explicit,
    # non-local TEAMWORK_BASE_URL always wins.  Set false for strict config-only.
    public_url_autodetect: bool = Field(default=True, alias="PUBLIC_URL_AUTODETECT")

    # Discord
    discord_bot_token: str | None = Field(default=None, alias="DISCORD_BOT_TOKEN")
    discord_allowed_users: str | None = Field(default=None, alias="DISCORD_ALLOWED_USERS")
    discord_allowed_channels: str | None = Field(default=None, alias="DISCORD_ALLOWED_CHANNELS")
    discord_to_phone_map: str | None = Field(default=None, alias="DISCORD_TO_PHONE_MAP")

    # External logins
    nyt_username: str | None = Field(default=None, alias="NYT_USERNAME")
    nyt_password: str | None = Field(default=None, alias="NYT_PASSWORD")

    # Phone metadata
    phone_to_name_map: str | None = Field(default=None, alias="PHONE_TO_NAME_MAP")
    phone_to_email_map: str | None = Field(default=None, alias="PHONE_TO_EMAIL_MAP")
    phone_to_greeting_map: str | None = Field(default=None, alias="PHONE_TO_GREETING_MAP")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("workspace_dir")
    @classmethod
    def _absolute_workspace_dir(cls, v: str) -> str:
        """Ensure ``workspace_dir`` is always an absolute path.

        Relative paths (like ``./workspaces`` or ``../workspaces``) are
        resolved at settings load time. If any code changes the process
        CWD later (git subprocesses, Hugo, etc.), all workspace lookups
        still resolve to the original absolute path — preventing nested
        ``workspaces/user1/workspaces/user2/`` path corruption.
        """
        if not v:
            return v
        return os.path.abspath(v)


_WEAK_SECRET_KEYS = frozenset({"change-me", "changeme", "secret", "dev", "test", ""})


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return cached settings instance."""
    s = AppSettings()
    if s.flask_secret_key.lower().strip() in _WEAK_SECRET_KEYS:
        import logging
        logging.getLogger(__name__).warning(
            "FLASK_SECRET_KEY is set to a weak placeholder ('%s'). "
            "Generate a strong key for production: python -c \"import secrets; print(secrets.token_urlsafe(32))\"",
            s.flask_secret_key,
        )
    return s


settings = get_settings()


# Non-secret OS-level networking vars that requests/httpx read from ``os.environ``
# (NOT from Pydantic). Prax deliberately does not blanket-load ``.env`` into the
# environment — that would leak API keys to child processes (the sandbox / agent
# could ``printenv`` them, the opposite of keyless). But the secrets-proxy needs
# these few to be process-level so egress routes through it. So export ONLY this
# allowlist from ``.env`` — never a key.
_PROXY_ENV_ALLOWLIST = (
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
)


def _export_proxy_env_from_dotenv(env_file: str = ".env") -> None:
    """Export only the allow-listed proxy/TLS vars from ``.env`` into ``os.environ``.

    Idempotent + safe: skips anything already set (so Docker's ``env_file`` wins),
    and NEVER exports a secret — only the fixed networking allowlist. Makes the
    secrets-proxy's ``HTTPS_PROXY`` setup work in host-process mode, not just Docker.
    """
    from pathlib import Path
    p = Path(env_file)
    if not p.exists():
        return
    try:
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            ku = key.strip().upper()
            if ku in _PROXY_ENV_ALLOWLIST and ku not in os.environ and ku.lower() not in os.environ:
                os.environ[ku] = val.strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001 - best-effort; never break startup on a malformed .env
        pass


# NOTE: not called here. Exporting HTTPS_PROXY at settings-import time would route
# egress for EVERY process that imports settings (tests, CLI, eval runs) through the
# proxy — wrong. Only the live server should. app.py calls this at startup.
