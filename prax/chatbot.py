import asyncio
import logging

from nltk.tokenize import sent_tokenize

from prax.convo_states import convo_states
from prax.services.conversation_service import conversation_service
from prax.settings import settings
from prax.sms import send_sms

logger = logging.getLogger(__name__)

def split_into_sentences(text):
    sentences = sent_tokenize(text)
    return sentences

async def streamaskgpt(question,
           buffer_id,
           from_num,
           phone_or_text='text',
           twiml=False,
           call_sid=None,
           ngrok_url=None,
           model_name=settings.base_model,
           streaming=False,
           latex=False,
           sms_sid=None,):


    if call_sid is None:
        user_sid = sms_sid
    else:
        user_sid = call_sid

    logger.info(f"streamaskgpt: {question}")
    convo_states[user_sid]['buffer_on'] = True
    convo_states[user_sid]['in_article'] = False
    convo_states[user_sid]['read_buffer'][buffer_id] = []

    answer = conversation_service.reply(from_num, question)
    sentences = split_into_sentences(answer)
    for sentence in sentences:
        convo_states[user_sid]['read_buffer'][buffer_id].append(sentence)
    convo_states[user_sid]['read_buffer'][buffer_id].append("#FINISHED#")

    if phone_or_text == "text":
        logger.info(f"Sending reply: {answer} text message to {from_num}")
        send_sms(answer, from_num)
    else:
        return answer, sentences


def askgpt(question,
           buffer_id,
           from_num,
           phone_or_text='text',
           twiml=False,
           call_sid=None,
           ngrok_url=None,
           model_name=settings.base_model,
           streaming=False,
           latex=False,
           sms_sid=None,):

    asyncio.run(streamaskgpt(question,
            buffer_id,
            from_num=from_num,
            phone_or_text=phone_or_text,
            twiml=twiml,
            call_sid=call_sid,
            ngrok_url=ngrok_url,
            model_name=model_name,
            streaming=streaming,
            latex=latex,
            sms_sid=sms_sid,))

