import asyncio
import glob
import logging
import os

from langchain_community.tools import DuckDuckGoSearchRun

from prax.settings import settings
from prax.sms import send_sms  # re-export for backwards compat

search_tool = DuckDuckGoSearchRun()

logger = logging.getLogger(__name__)


def delete_temp_files(call_sid):
    file_pattern = f"./static/temp/{call_sid}_*"
    files_to_delete = glob.glob(file_pattern)
    if files_to_delete:
        for file_path in files_to_delete:
            try:
                os.remove(file_path)
                logger.info("File %s has been deleted.", file_path)
            except FileNotFoundError:
                logger.warning("File %s not found.", file_path)
            except PermissionError:
                logger.warning("Permission denied to delete %s.", file_path)
            except OSError as e:
                logger.error("Error deleting %s: %s", file_path, e)
    else:
        logger.debug("No files found with prefix '%s'.", file_pattern)
    return None

def create_convo_state():
    return {
        'convo_started': True,
        'model_name': settings.base_model,
        'music': True,
        'language': 'en',
        'news_stale': True,
        'arxiv_stale': True,
        'chat_mode': True,
        'reader_mode': False,
        'read_buffer': {},
        'reader_data': [],
        'buffer_redirect': None,
        'reader_source': None,
        'start_index': 0,
        'in_article': False,
        'article_index': None,
        'article_content': None,
        'old_start_index': None,
        'buffer_link': None,
        'buffer_comments': None,
        'buffer_title': None,
        'None_stale': None,
        'current_buffer_id': None,
    }

def gather_speech(response, language_code):
    response.play('/static/mp3/beep.mp3', loop=1)
    response.gather(
        speech_timeout='auto',
        speech_model='experimental_conversations',
        input='speech',
        action='/respond',
        language='en-US' if language_code == 'en' else f'{language_code}-{language_code.upper()}',
        timeout=30,
    )


async def background_search(text_input, to_number, sms_bool=True):
    """Perform a DuckDuckGo search and optionally SMS the result."""
    result = await asyncio.to_thread(search_tool.run, text_input)

    if sms_bool and to_number:
        send_sms(result, to_number)
    return result
