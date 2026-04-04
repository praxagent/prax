"""Application settings loaded via Pydantic for validation and reuse."""
from functools import lru_cache

from pydantic import Field
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
    port: int = Field(default=5001, alias="PORT")
    database_name: str = Field(default="conversations.db", alias="DATABASE_NAME")
    identity_db: str = Field(default="identity.db", alias="IDENTITY_DB")

    # Providers / API Keys
    openai_key: str | None = Field(default=None, alias="OPENAI_KEY")
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
    high_model: str = Field(default="gpt-5.4", alias="HIGH_MODEL")
    high_enabled: bool = Field(default=True, alias="HIGH_ENABLED")
    pro_model: str = Field(default="gpt-5.4-pro", alias="PRO_MODEL")
    pro_enabled: bool = Field(default=False, alias="PRO_ENABLED")

    # Vision / image understanding
    vision_model: str = Field(default="gpt-image-1.5", alias="VISION_MODEL")
    vision_provider: str = Field(default="openai", alias="VISION_PROVIDER")

    # Workspace
    workspace_dir: str = Field(default="../workspaces", alias="WORKSPACE_DIR")

    # Runtime environment
    running_in_docker: bool = Field(default=False, alias="RUNNING_IN_DOCKER")

    # Sandbox
    sandbox_image: str = Field(default="prax-sandbox:latest", alias="SANDBOX_IMAGE")
    sandbox_host: str = Field(default="localhost", alias="SANDBOX_HOST")
    sandbox_timeout: int = Field(default=1800, alias="SANDBOX_TIMEOUT")
    sandbox_max_concurrent: int = Field(default=5, alias="SANDBOX_MAX_CONCURRENT")
    sandbox_default_model: str = Field(default="openai/gpt-5.4", alias="SANDBOX_DEFAULT_MODEL")
    sandbox_mem_limit: str = Field(default="1g", alias="SANDBOX_MEM_LIMIT")
    sandbox_cpu_limit: int = Field(default=2_000_000_000, alias="SANDBOX_CPU_LIMIT")
    sandbox_max_rounds: int = Field(default=10, alias="SANDBOX_MAX_ROUNDS")

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
            "Hard wall-clock timeout (seconds) for a single agent.run() "
            "invocation.  If the agent hasn't finished within this time, "
            "the run is aborted.  Prevents unbounded API spend."
        ),
    )

    @property
    def sandbox_persistent(self) -> bool:
        """True when the sandbox is always-on (docker-compose deployment)."""
        return self.running_in_docker

    # Fine-tuning / Local Models (optional — GPU required)
    finetune_enabled: bool = Field(default=False, alias="FINETUNE_ENABLED")
    vllm_base_url: str = Field(default="http://localhost:8000/v1", alias="VLLM_BASE_URL")
    local_model: str = Field(default="Qwen/Qwen3-8B", alias="LOCAL_MODEL")
    finetune_base_model: str = Field(default="unsloth/Qwen3-8B-unsloth-bnb-4bit", alias="FINETUNE_BASE_MODEL")
    finetune_output_dir: str = Field(default="./adapters", alias="FINETUNE_OUTPUT_DIR")
    finetune_max_steps: int = Field(default=60, alias="FINETUNE_MAX_STEPS")
    finetune_learning_rate: float = Field(default=2e-4, alias="FINETUNE_LEARNING_RATE")
    finetune_lora_rank: int = Field(default=16, alias="FINETUNE_LORA_RANK")

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

    # Health monitoring watchdog — periodic self-checks every N turns.
    # Set to false to disable for minimal RAM / lightweight deployments.
    health_monitor_enabled: bool = Field(default=True, alias="HEALTH_MONITOR_ENABLED")

    # TeamWork integration (web UI)
    teamwork_url: str = Field(default="", alias="TEAMWORK_URL")  # e.g. "http://teamwork:8000"
    teamwork_api_key: str = Field(default="", alias="TEAMWORK_API_KEY")
    teamwork_user_phone: str = Field(default="", alias="TEAMWORK_USER_PHONE")

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
