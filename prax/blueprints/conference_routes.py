import logging

from flask import Blueprint, Response, current_app, request
from twilio.twiml.voice_response import VoiceResponse

from prax.blueprints.twilio_auth import validate_twilio_request
from prax.convo_states import convo_states
from prax.helpers_dictionaries import (
    voices,
)
from prax.twilio_voice_utils import (
    create_gather_instance,
)

logger = logging.getLogger(__name__)

conference_routes = Blueprint('conference_routes', __name__)


@conference_routes.route('/conference', methods=['POST'])
@validate_twilio_request
def conference():
    current_app.config['NGROK_URL']
    call_sid = request.values['CallSid']
    convo_states[call_sid]['language']

    response = VoiceResponse()
    response.play('/static/mp3/silence.mp3', loop=2)

    return Response(str(response), mimetype='text/xml')


@conference_routes.route('/say', methods=['POST'])
@validate_twilio_request
def say_message():
    response = VoiceResponse()

    call_sid = request.values['CallSid']

    try:
        language = convo_states[call_sid]['language']
    except KeyError:
        language = 'en'
        convo_states[call_sid] = {}
        convo_states[call_sid]['language'] = language

    gather = create_gather_instance(language, "/respond", "POST")

    try:
        message = convo_states[call_sid]['message']
    except KeyError:
        message = request.values['TranscriptionText']
        convo_states[call_sid]['message'] = message

    voice = voices[language]
    gather.say(
        message,
        voice=voice,
        language='en-US' if language == 'en' else f'{language}-{language.upper()}',
    )
    response.append(gather)
    response.redirect(method="POST", url="/transcribe")

    return Response(str(response), mimetype='application/xml')


# Allowed static paths for the /play endpoint (prevent open redirect).
_ALLOWED_PLAY_PREFIX = "/static/mp3/"


@conference_routes.route('/play', methods=['POST'])
@validate_twilio_request
def play_message():
    response = VoiceResponse()
    response_file = request.args.get('response_file', '')

    # Validate that response_file is a local static asset, not an arbitrary URL.
    if not response_file or not response_file.startswith(_ALLOWED_PLAY_PREFIX):
        logger.warning("Rejected play request with invalid path: %s", response_file)
        response.say("Audio file not available.")
    else:
        response.play(response_file, loop=1)

    response.redirect(method="POST", url="/transcribe")
    return Response(str(response), mimetype='application/xml')
