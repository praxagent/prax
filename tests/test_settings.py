import json
import os

os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret')
os.environ.setdefault('TWILIO_ACCOUNT_SID', 'ACXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX')
os.environ.setdefault('TWILIO_AUTH_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_NAME', 'conversations.db')
os.environ.setdefault('OPENAI_KEY', 'sk-test')
os.environ.setdefault('LLM_PROVIDER', 'openai')
os.environ.setdefault('AGENT_TEMPERATURE', '0.7')

def _merge_env_map(env_var_name, additions):
    """Ensure test-specific phone data exists without clobbering user config."""
    existing = {}
    raw_value = os.getenv(env_var_name)
    if raw_value:
        try:
            existing = json.loads(raw_value)
        except json.JSONDecodeError:
            existing = {}
    existing.update(additions)
    os.environ[env_var_name] = json.dumps(existing)


test_phone_to_name = {
    '+01234567890': 'Tester',
    '+1234567890': 'Test User'
}

test_phone_to_email = {
    '+01234567890': 'tester@example.com',
    '+1234567890': 'test.user@example.com'
}

test_phone_to_greeting = {
    '+01234567890': 'greeting_test.mp3',
    '+1234567890': 'greeting_test_user.mp3'
}

_merge_env_map('PHONE_TO_NAME_MAP', test_phone_to_name)
_merge_env_map('PHONE_TO_EMAIL_MAP', test_phone_to_email)
_merge_env_map('PHONE_TO_GREETING_MAP', test_phone_to_greeting)
