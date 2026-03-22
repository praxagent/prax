import logging

import openai
from openai import OpenAI

from prax.clients import get_twilio_client
from prax.convo_states import convo_states
from prax.settings import settings

openai_client = OpenAI(api_key=settings.openai_key)

logger = logging.getLogger(__name__)

def latex_to_english(reader_data, call_sid, redirect=True):
    input_text = reader_data['abstract']
    if input_text:
        logger.info(f"latex_to_english: {input_text}")
        latex_prompt="""
        Convert the following to spoken language and leave everything the same
        except convert LaTeX code into English words, for example, $x^2$ should
        be read as x squared, and $x_1$ should be read as x sub one, C/O would
        be said C slash O unless it was in a math equation, Mpc is mega parsecs
        if the content is astrophysics or astronomy, and so on.
        """
        conversation = [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': latex_prompt},
        {'role': 'user', 'content': input_text}
        ]
        logger.info(f"calling chatGPT with: {conversation}")

        get_twilio_client().calls(call_sid).update(url=f"{settings.ngrok_url}/conference", method='POST')
        try:
            response = openai_client.chat.completions.create(model=settings.base_model,
            messages=conversation,
            max_tokens=4096)

            convo_states[call_sid]['read_buffer'][0] = [response.choices[0].message.content]
            convo_states[call_sid]['read_buffer'][0].append('#FINISHED#')
            logger.info(f"latex article content {convo_states[call_sid]['article_content']}")
        except openai.RateLimitError as e:
            convo_states[call_sid]['read_buffer'][0] = [ "Sorry friend, OpenAI is overloaded at the moment. Please try again in a few moments."]
            convo_states[call_sid]['read_buffer'][0].append('#FINISHED#')
            logger.error("RateLimitError occurred:", e)
    else:
        convo_states[call_sid]['read_buffer'][0] = ["No content was found where expected."]
        convo_states[call_sid]['read_buffer'][0].append('#FINISHED#')
        convo_states[call_sid]['current_buffer_id'] = 0
    if redirect:
        get_twilio_client().calls(call_sid).update(url=f"{settings.ngrok_url}/read", method='POST')

    convo_states[call_sid]['buffer_redirect'] = "/reader"
    convo_states[call_sid]['current_buffer_id'] = 0
    convo_states[call_sid]['article_text'] = convo_states[call_sid]['read_buffer'][0][0]
    return convo_states[call_sid]['read_buffer'][0][0]
