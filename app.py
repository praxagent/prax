import logging
import os

from flask import Flask

#from flask_session import Session
from prax.blueprints.conference_routes import conference_routes
from prax.blueprints.main_routes import main_routes
from prax.blueprints.plugin_routes import plugin_routes
from prax.blueprints.reader_routes import reader_routes
from prax.blueprints.teamwork_routes import teamwork_routes
from prax.blueprints.textchat_routes import textchat_routes
from prax.blueprints.user_routes import user_routes
from prax.conversation_memory import init_database
from prax.services.discord_service import start_bot as start_discord_bot
from prax.services.identity_service import init_identity_db, migrate_legacy_users, reconcile_workspace_dir
from prax.services.scheduler_service import init_scheduler
from prax.settings import settings
from prax.token_management import get_encoding_for_model


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    # Initialize OpenTelemetry tracing (gated on OBSERVABILITY_ENABLED).
    if settings.observability_enabled:
        try:
            from prax.observability import init_observability
            init_observability(service_name=settings.agent_name.lower())
        except Exception:
            pass

    init_database(app.config['DATABASE_NAME'])

    app.register_blueprint(main_routes)
    app.register_blueprint(conference_routes)
    app.register_blueprint(plugin_routes)
    app.register_blueprint(reader_routes)
    app.register_blueprint(teamwork_routes)
    app.register_blueprint(textchat_routes)
    app.register_blueprint(user_routes)

    # Prometheus metrics endpoint — scraped by Prometheus every 10s.
    if settings.observability_enabled:
        @app.route("/metrics")
        def metrics():
            from flask import Response

            from prax.observability.metrics import CONTENT_TYPE_LATEST, generate_latest
            return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


    log_path = settings.log_path

    # File handler for persistent logs.
    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] - %(message)s"))
    # Console handler so Werkzeug/startup banners still print to stdout.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] - %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])

    # Demote /health access logs to DEBUG (watchdog hits every 10s).
    class _HealthFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if "/health" in record.getMessage():
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
            return True

    logging.getLogger("werkzeug").addFilter(_HealthFilter())

    # Quiet third-party INFO chatter that fires on tight polling loops.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(
        "Starting %s — provider=%s default_model=%s temperature=%s encoding=%s",
        settings.agent_name,
        settings.default_llm_provider,
        settings.base_model,
        settings.agent_temperature,
        get_encoding_for_model(settings.base_model),
    )

    from prax.agent.model_tiers import tier_summary
    logger.info("Model tiers:\n%s", tier_summary())

    init_database(settings.database_name)
    init_identity_db()
    migrate_legacy_users()

    # Fail fast if running in Docker without PRAX_USER_ID — the sandbox
    # mounts break without it (no user isolation, wrong paths).
    if settings.running_in_docker and not settings.prax_user_id:
        raise RuntimeError(
            "PRAX_USER_ID is required when running in Docker. "
            "Set it in .env to your workspace directory name "
            "(e.g. PRAX_USER_ID=usr_abc12345). "
            "This determines which user's workspace the sandbox mounts."
        )

    # Ensure the identity service's workspace_dir matches PRAX_USER_ID
    reconcile_workspace_dir()

    # In debug mode Werkzeug spawns a reloader process + a child process.
    # Both the parent and child would otherwise call init_scheduler() and
    # init_discord_bot(), causing duplicate jobs (one per scheduler instance)
    # — which manifests as duplicate SMS reminders. Only initialize these
    # singletons in the child process (WERKZEUG_RUN_MAIN=true), or when
    # not in debug mode at all.
    #
    # NOTE: we check `settings.debug` (the env-var-driven setting), NOT
    # `app.debug`. Flask only sets `app.debug` when `app.run(debug=True)`
    # is called, which happens AFTER create_app() returns. At this point
    # in execution, `app.debug` is always False, which would defeat the
    # guard and let the parent reloader process double-init the scheduler.
    _is_reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if not settings.debug or _is_reloader_child:
        init_scheduler()
        start_discord_bot()

    # Initialize TeamWork integration if configured.
    # In lite mode all services share a container and TeamWork may still
    # be starting when Prax reaches this point.  Retry with backoff.
    if settings.teamwork_enabled and settings.teamwork_url:
        try:
            import time as _time

            from prax.services.teamwork_service import get_teamwork_client
            tw = get_teamwork_client()
            # Wait for TeamWork to be reachable (up to ~30s)
            for _attempt in range(15):
                try:
                    import requests as _req
                    _req.get(f"{settings.teamwork_url}/health", timeout=2)
                    break
                except Exception:
                    logger.info("Waiting for TeamWork (%s)...", settings.teamwork_url)
                    _time.sleep(2)
            # Build the webhook URL — use localhost in lite mode (same container),
            # "app" hostname in multi-container mode.
            _webhook_host = "localhost" if settings.teamwork_url.startswith("http://localhost") else "app"
            webhook_url = f"http://{_webhook_host}:5001/teamwork/webhook"
            # Use the real user's workspace if a phone number is configured,
            # so the file browser and workspace tools see the same files as SMS/Discord.
            workspace_dir = (settings.teamwork_user_phone or "").lstrip("+") or None
            tw.create_project(
                name=f"{settings.agent_name}'s Workspace",
                description=f"Controlled by {settings.agent_name}",
                webhook_url=webhook_url,
                workspace_dir=workspace_dir,
            )
            tw.create_agent(name=settings.agent_name, role="orchestrator", soul="Primary AI assistant")
            # Register internal role agents so their status is visible in the UI.
            for role_name, role_type, soul in [
                ("Planner", "planner", "Breaks complex requests into structured plans"),
                ("Researcher", "researcher", "Investigates questions via web search and document analysis"),
                ("Executor", "executor", "Executes tool calls and workspace operations"),
                ("Auditor", "auditor", "Reviews claims for accuracy and audits governance logs"),
            ]:
                tw.create_agent(name=role_name, role=role_type, soul=soul)
            # Ensure #discord and #sms mirror channels exist (backfills
            # for projects created before mirroring was added).
            from prax.services.teamwork_hooks import ensure_mirror_channels, reset_all_idle, sync_conversation_history
            ensure_mirror_channels()
            sync_conversation_history()
            # Reset all agents to idle on startup — clears stuck "working"
            # status from previous runs that crashed or were interrupted.
            reset_all_idle()
            # Coding agent channels (#claude-code, #codex, #opencode) are
            # created lazily on first tool invocation — no startup setup needed.
        except Exception:
            logger.warning("TeamWork integration failed to initialize", exc_info=True)

    # --- Health probes (Kubernetes/Docker-compatible) ---

    @app.route("/healthz/live")
    def liveness():
        """Liveness probe — is the process alive and responding?
        Fast check, no external dependencies. Use for Docker HEALTHCHECK
        and Kubernetes livenessProbe.
        """
        from flask import jsonify as _jsonify
        return _jsonify({"status": "alive"})

    @app.route("/healthz/ready")
    def readiness():
        """Readiness probe — is the agent ready to accept work?
        Checks critical subsystems. Use for Kubernetes readinessProbe
        and load balancer health checks.
        """
        from flask import jsonify as _jsonify
        issues = []

        # Check LLM provider reachability via circuit breaker
        try:
            from prax.agent.circuit_breaker import get_all_breakers
            for name, state in get_all_breakers().items():
                if state["state"] == "open":
                    issues.append(f"{name}: circuit breaker open")
        except Exception:
            pass

        # Check health monitor status
        try:
            from prax.agent.health_monitor import get_last_check
            check = get_last_check()
            if check and check.overall == "unhealthy":
                issues.append(f"health: {check.overall}")
        except Exception:
            pass

        if issues:
            return _jsonify({"status": "not_ready", "issues": issues}), 503
        return _jsonify({"status": "ready"})

    return app


app = create_app()

if __name__ == '__main__':
    debug_bool = settings.debug
    app.run(
        debug=debug_bool,
        host='0.0.0.0',
        port=settings.port,
        exclude_patterns=[
            "**/.git/*",
            "**/.github/*",
            "**/workspaces/*",
            "**/__pycache__/*",
            "**/*.pyc",
            "**/*.log",
            "**/*.db",
        ],
    )
