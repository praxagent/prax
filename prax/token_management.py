import logging

import tiktoken

logger = logging.getLogger(__name__)

# Default encoding for models tiktoken doesn't recognize yet.
DEFAULT_ENCODING = "o200k_base"


def get_encoding_for_model(model: str) -> str:
    """Return the tiktoken encoding name for a model, with a safe fallback."""
    try:
        return tiktoken.encoding_for_model(model).name
    except KeyError:
        logger.debug("Unknown model %r for tiktoken, falling back to %s", model, DEFAULT_ENCODING)
        return DEFAULT_ENCODING


def chat_to_string(chat_list: list) -> str:
    """Converts chat log list into string."""
    result = "\n\n".join(
        "\n".join(
            f"{key.capitalize()}: {value}"
            for key, value in item.items() if value
        ) for item in chat_list
    )
    return result


def num_tokens_from_string(string: str, encoding_name: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens
