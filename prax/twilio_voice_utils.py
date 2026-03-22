import logging
import urllib.parse

from twilio.twiml.voice_response import Gather

from prax.clients import get_twilio_client
from prax.convo_states import convo_states

logger = logging.getLogger(__name__)


def twilio_sms(chat_num, user_num, answer):

    max_length = 1600
    parts = []
    for i in range(0, len(answer), max_length):
        parts.append(answer[i:i+max_length])
    for part in parts:
        get_twilio_client().messages.create(
                    from_=chat_num,
                    body=part,
                    to=user_num
                    )

def remove_from_conference_to_say(call_sid, ngrok_url, message, response_file=None):

    if response_file:
        say_url = f'{ngrok_url}/play?{urllib.parse.urlencode({"response_file": response_file})}'
    else:
        convo_states[call_sid]['message'] = message
        say_url = f'{ngrok_url}/say'

    get_twilio_client().calls(call_sid).update(url=say_url, method='POST')

def create_gather_instance(language, redirect_url, redirect_method, hints=None):
    language_code = "en-US" if language == 'en' else f'{language}-{language.upper()}'
    return Gather(
        speech_timeout='auto',
        speech_model='experimental_conversations',
        input='speech',
        action=redirect_url,
        method=redirect_method,
        language=language_code,
        hints=hints
    )

def create_say_text_and_append_gather(resp, gather, say_text, voice, language):
    language_code = "en-US" if language == 'en' else f'{language}-{language.upper()}'
    gather.say(
        f"{say_text}",
        voice=voice,
        language=language_code)
    resp.append(gather)
    return resp
