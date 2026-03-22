# config.py
from prax.settings import settings


class Config:
    SECRET_KEY = settings.flask_secret_key
    SESSION_TYPE = settings.session_type
    NGROK_URL = settings.ngrok_url
    DEBUG = settings.debug
    LOG_PATH = settings.log_path
    DATABASE_NAME = settings.database_name
    PORT = settings.port
    APP_SETTINGS = settings
