import logging
import os

from flask import (
    Blueprint,
    Response,
    request,
)
from twilio.twiml.voice_response import VoiceResponse

from prax.blueprints.twilio_auth import validate_twilio_request
from prax.convo_states import convo_states
from prax.filter_input import preprocess_input
from prax.helpers_dictionaries import transcription_mapping, voices
from prax.readers.reader_functions import (
    generate_say_articles,
    handle_article_text_state,
    handle_convo_state,
    handle_in_article_state,
    handle_user_input_choice,
    pop_first_n_items,
)
from prax.settings import settings
from prax.twilio_voice_utils import create_gather_instance, create_say_text_and_append_gather

nytimes_username = settings.nyt_username
nytimes_password = settings.nyt_password
cookies_file = os.environ.get("NYT_COOKIES_FILE", "./news/nytimes/nyt_cookies.json")
ngrok_url = settings.ngrok_url

logger = logging.getLogger(__name__)

reader_routes = Blueprint('reader_routes', __name__)

@reader_routes.route("/reader", methods=["GET", "POST"])
@validate_twilio_request
def reader():
    resp = VoiceResponse()
    call_sid = request.values['CallSid']

    voice = "Polly.Kendra-Neural"
    language = "en"

    logger.info(convo_states[call_sid])
    gather = create_gather_instance(
        language,
        "/reader",
        redirect_method="POST",
        hints=', '.join(transcription_mapping.keys())
    )

    user_input_raw = request.form.get('SpeechResult')
    resp, user_input = preprocess_input(user_input_raw, resp, gather, call_sid)


    if convo_states[call_sid]['in_article'] and user_input:
        reader_data = convo_states[call_sid]['reader_data']
        article_index = handle_user_input_choice(call_sid, user_input, reader_data)
        resp = handle_in_article_state(call_sid, article_index, resp)
        if not convo_states[call_sid]['in_article']:
            convo_states[call_sid]['article_index'] = None
            resp.redirect(url="/reader", method="POST")
            return str(resp)

    reader_data, subject = handle_convo_state(call_sid)

    article_index = handle_user_input_choice(call_sid, user_input, reader_data)


    reader_data = convo_states[call_sid]['reader_data']
    if article_index is not None and article_index < len(reader_data):
        resp = handle_article_text_state(call_sid, resp, reader_data, article_index)

    say_text = generate_say_articles(
        reader_data,
        convo_states[call_sid]['start_index'],
        convo_states[call_sid]['start_index'] + 5,
        call_sid
    )

    resp = create_say_text_and_append_gather(
        resp,
        gather,
        say_text,
        voice,
        language
    )

    logger.info(f"start_index: {convo_states[call_sid]['start_index']}")
    resp.redirect(url="/reader", method="POST")

    logger.info("End of the line")
    return str(resp)

@reader_routes.route('/read', methods=['GET', 'POST'])
@validate_twilio_request
def reader_buff():

    call_sid = request.values['CallSid']
    buffer_id = convo_states[call_sid]['current_buffer_id']
    language = convo_states[call_sid]['language']

    response = VoiceResponse()

    #gather = create_gather_instance(language, "/respond", "POST")

    if not convo_states[call_sid]['buffer_on']:
        logger.info("Buffer is off, redirecting to transcribe")
        response.redirect(url="/transcribe", method="POST")
        #response.add_child(response)
        #response.append(gather)
        return Response(str(response), mimetype='application/xml')
    else:
        if len(convo_states[call_sid]['read_buffer'][buffer_id]) == 0:
            logger.info("A")
            # Wait longer
            logger.info("Buffer contents: %s", convo_states[call_sid]['read_buffer'][buffer_id])
            logger.info("Buffer is empty, waiting longer...")
            response.play('/static/mp3/beep_wait.mp3', loop=1)
            response.pause(length=3)
            #response.append(gather)
        else:
            # Get the first block of items to read
            logger.info("B")
            first_items = pop_first_n_items(convo_states[call_sid]['read_buffer'][buffer_id], 5)
            logger.info("First items: %s", first_items)

            for i in first_items:
                if i == '#FINISHED#':
                    convo_states[call_sid]['buffer_on'] = False
                    #response.play('/static/mp3/fin.mp3', loop=1)
                    convo_states[call_sid]['current_buffer_id'] = None
                    if convo_states[call_sid]['buffer_redirect'] != "/reader":
                        convo_states[call_sid]['buffer_redirect'] = None
                    pass
                else:
                    response.say(
                        i,
                        voice=voices[language],
                        language='en-US' if language == 'en' else f'{language}-{language.upper()}',
                    )
            if convo_states[call_sid]['buffer_on']:
                response.play('/static/mp3/beep_wait.mp3', loop=1)


            #response.play('/static/mp3/beep_quiet.mp3', loop=1)
            #response.append(gather)
        if not convo_states[call_sid]['buffer_redirect']:
            response.redirect(url="/read", method="POST")
        else:
            response.redirect(url=convo_states[call_sid]['buffer_redirect'], method="POST")
        return Response(str(response), mimetype='application/xml')
