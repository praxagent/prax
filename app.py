import logging
import os

from flask import Flask

#from flask_session import Session
from prax.blueprints.conference_routes import conference_routes
from prax.blueprints.main_routes import main_routes
from prax.blueprints.reader_routes import reader_routes
from prax.blueprints.teamwork_routes import teamwork_routes
from prax.blueprints.textchat_routes import textchat_routes
from prax.conversation_memory import init_database
from prax.services.discord_service import start_bot as start_discord_bot
from prax.services.scheduler_service import init_scheduler
from prax.settings import settings
from prax.token_management import get_encoding_for_model


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    init_database(app.config['DATABASE_NAME'])

    app.register_blueprint(main_routes)
    app.register_blueprint(conference_routes)
    app.register_blueprint(reader_routes)
    app.register_blueprint(teamwork_routes)
    app.register_blueprint(textchat_routes)


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

    init_scheduler()

    # In debug mode Werkzeug spawns a reloader process + a child process.
    # Only start the Discord bot once — in the child (WERKZEUG_RUN_MAIN=true)
    # or when not in debug mode at all.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_discord_bot()

    # Initialize TeamWork integration if configured
    if settings.teamwork_url:
        try:
            from prax.services.teamwork_service import get_teamwork_client
            tw = get_teamwork_client()
            # Build the webhook URL from our own address
            webhook_url = "http://app:5001/teamwork/webhook"
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
                ("Skeptic", "skeptic", "Challenges assumptions and audits claims for accuracy"),
                ("Auditor", "auditor", "Reviews governance logs and tool risk classifications"),
            ]:
                tw.create_agent(name=role_name, role=role_type, soul=soul)
        except Exception:
            logger.warning("TeamWork integration failed to initialize", exc_info=True)

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
