import logging
import os
import re
import threading
import uuid

import pytrie
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from prax.chatbot import askgpt
from prax.convo_states import convo_states
from prax.helpers_dictionaries import (
    email_map,
    transcription_mapping,
)
from prax.readers.reader_function_mappings import reader_function_mappings
from prax.readers.reader_source_mappings import article_option_mapping, reader_source_mappings
from prax.settings import settings

ngrok_url = settings.ngrok_url or ""

logger = logging.getLogger(__name__)

# Create Trie from mapping
def create_trie(mapping):
    """Create Trie data structure from provided mapping"""
    trie = pytrie.Trie()
    for correct_word, mis_transcribed_words in mapping.items():
        for mis_transcribed_word in mis_transcribed_words:
            trie[mis_transcribed_word] = correct_word
    return trie

transcription_trie = create_trie(transcription_mapping)

def return_dict_except_key(dictionary, key_to_exclude):
    # create a copy of the dictionary
    copied_dict = dictionary.copy()
    # remove the unwanted key-value pair
    copied_dict.pop(key_to_exclude, None)
    # print the dictionary
    return copied_dict

def menu_correct_transcription(transcribed_text):
    """Perform corrections on the transcribed text using Trie"""
    logger.info("Transcribed: %s", transcribed_text)
    # Removing trailing punctuations
    corrected_text_buffer = re.sub(r'[.,?!]+$', '', str(transcribed_text.lower()).lower())
    transcribed_text = corrected_text_buffer.strip()
    transcribed_text_lower = transcribed_text.lower()
    return transcription_trie.get(transcribed_text_lower, transcribed_text_lower)

article_choice_mapping = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}

def handle_user_input(user_input, start_index, article_choice_mapping, data_length, call_sid):
    """Handle user input and provide article index and next starting index"""
    logger.info("Handling user input: %s with %s", user_input, start_index)
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    article_index = handle_article_index(user_input, start_index, article_choice_mapping)
    next_start_index = handle_next_start_index(user_input, start_index, data_length, call_sid)

    logger.info("Next start index: %s", next_start_index)

    return article_index, next_start_index


def handle_article_index(user_input, start_index, article_choice_mapping):
    """Return appropriate article index"""
    logger.info("handle_article_index")
    if user_input in article_choice_mapping:
        logger.info("User input in article_choice_mapping: %s", user_input)
        article_index = article_choice_mapping.get(user_input, 0) - 1 + start_index
        logger.info("Article index: %s", article_index)
    else:
        logger.info("User input not in article_choice_mapping: %s", user_input)
        article_index = None
    return article_index


def handle_next_start_index(user_input, start_index, data_length, call_sid):
    """Calculate the next start index"""
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("handle_next_start_index")
    if user_input in {"next", "text"}:
        next_start_index = min(start_index + 5, data_length)
    elif user_input == "back":
        next_start_index = max(start_index - 5, 0)
    else:
        next_start_index = start_index
    convo_states[call_sid]['start_index'] = next_start_index
    return next_start_index

def generate_say_articles(reader_data, start, end, call_sid):
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("generate_say_articles")

    logger.info("Generating say text for articles %s to %s", start, end)
    say_text = ""
    if convo_states[call_sid]['in_article']:
        logger.info("generate_say_articles > if1")
        logger.info("In article")
        reader_data = article_option_mapping
        convo_states[call_sid]['reader_data'] = article_option_mapping
    if reader_data is None:
        logger.info("generate_say_articles > if2")
        return "Something went wrong, say exit to go back"
    logger.info("generate_say_articles > for1")
    for i in range(start, end):

        if i < len(reader_data):
            num_headline = (i+1)%5
            if num_headline == 0:
                num_headline = 5
            say_text += "{}. {}. ".format(num_headline, reader_data[i]['title'])
    if not convo_states[call_sid]['in_article']:
        logger.info("generate_say_articles > if3")
        say_text += "To hear the next five options, say next. To go back, five items, say back. To select an article, say the number."
    return say_text


def get_reader_data(reader_source, subject, index, call_sid):
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("get_reader_data")

    if convo_states[call_sid]['reader_source'] is None:
        logger.info("No cached %s data", reader_source)
        logger.info(convo_states[call_sid])
    else:
        if not convo_states[call_sid][f'{reader_source}_stale']:
            logger.info("Using cached %s data", reader_source)
            return convo_states[call_sid]['reader_data']
        else:
            function_name = reader_source_mappings[reader_source][index]["headline_function"]
            function = reader_function_mappings.get(function_name)
            if function is None:
                logger.info("No function found for source %s and subject %s", reader_source, subject)
                return None
            try:
                _data = function(call_sid=call_sid)
                return _data
            except Exception:
                try:
                    _data = function(call_sid=call_sid)
                    return _data
                except Exception:
                    logger.error("Error getting %s data", reader_source, exc_info=True)
                    return None


def get_article_text(reader_source, subject, index, reader_data, call_sid, source_index):
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("get_article_text")

    convo_states[call_sid]['article_index'] = None
    function_name = reader_source_mappings[reader_source][source_index]["article_function"]
    convo_states[call_sid]['buffer_on'] = True
    function = reader_function_mappings.get(function_name)
    if function is None:
        logger.info("No function found for source %s and subject %s", reader_source, subject)
        return None


    return function(reader_data, call_sid=call_sid)


def reader_choose_subject(source):
    logger.info(convo_states)
    logger.info("reader_choose_subject")

    logger.info("Choosing subject for %s", source)
    return [{key: value[key] for key in ('title', 'subject')} for value in reader_source_mappings.get(source, {}).values()]


def set_convo_states_in_article(call_sid, article_full_text):
    """Sets the conversation state"""
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("set_convo_states_in_article")
    convo_states[call_sid]['old_start_index'] = convo_states[call_sid]['start_index']
    convo_states[call_sid]['start_index'] = 0
    convo_states[call_sid]['in_article'] = True
    convo_states[call_sid]['current_buffer_id'] = 0
    convo_states[call_sid]['buffer_redirect'] = "/reader"
    logger.info(convo_states[call_sid])

def handle_convo_state(call_sid):
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("handle_convo_state")
    subject = convo_states[call_sid].get(f"{convo_states[call_sid]['reader_source']}_subject")
    logger.info("Subject: %s", subject)
    reader_source = convo_states[call_sid]['reader_source']
    if convo_states[call_sid]['reader_source'] and not subject:
        logger.info("handle_convo_state > if")
        reader_data = reader_choose_subject(convo_states[call_sid]['reader_source'])
        convo_states[call_sid]['reader_data'] = reader_data
        logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
        return reader_data, subject
    elif not convo_states[call_sid]['in_article'] and convo_states[call_sid][f"{convo_states[call_sid]['reader_source']}_stale"]:
        logger.info("handle_convo_state > elif1")
        reader_data = get_reader_data(
            reader_source,
            subject,
            convo_states[call_sid]['source_index'],
            call_sid)
        convo_states[call_sid]['reader_data'] = reader_data
        logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
        return reader_data, subject
    elif not convo_states[call_sid]['in_article'] and not convo_states[call_sid][f"{convo_states[call_sid]['reader_source']}_stale"]:
        logger.info("handle_convo_state > elif2")
        reader_data = convo_states[call_sid]['reader_data']
        return reader_data, subject
    elif convo_states[call_sid]['in_article']:
        logger.info("handle_convo_state > elif3")
        reader_data = article_option_mapping
        logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
        return reader_data, subject
    else:
        logger.info("handle_convo_state > else")
        logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
        return None, subject


def handle_user_input_choice(call_sid, user_input, reader_data):
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("handle_user_input_choice")
    article_index = None
    if user_input:
        try:
            logger.info("handle_user_input_choice > try")
            article_index, next_start_index = handle_user_input(
                user_input, convo_states[call_sid]['start_index'], article_choice_mapping, len(reader_data), call_sid)
        except Exception:
            logger.info("handle_user_input_choice > except")
            article_index, next_start_index = handle_user_input(
                user_input, convo_states[call_sid]['start_index'], article_choice_mapping, 5, call_sid)

        logger.info("handle_user_input_choice > if")
        convo_states[call_sid]['next_start_index'] = next_start_index
        convo_states[call_sid]['article_index'] = article_index
        return article_index
    else:
        logger.info("handle_user_input_choice > else")
        logger.info(f"No user_input, start_index: {convo_states[call_sid]['start_index']}")
        return article_index


def handle_in_article_state(call_sid, article_index, resp):
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("handle_in_article_state")
    logger.info(f"article_index: {article_index}")
    if convo_states[call_sid]['in_article']:
        logger.info("handle_in_article_state > if")
        logger.info("In article")
        if article_index == 0:
            logger.info("handle_in_article_state > if > if")
            convo_states[call_sid]['in_article'] = False
            convo_states[call_sid]['article_index'] = None
            if convo_states[call_sid]['old_start_index']:
                convo_states[call_sid]['start_index'] = convo_states[call_sid]['old_start_index']
            else:
                convo_states[call_sid]['start_index'] = 0
            resp.redirect(url="/reader", method="GET")
            return resp
        elif article_index == 1:
            convo_states[call_sid]['buffer_redirect'] = '/transcribe'
            if convo_states[call_sid]['reader_source'] == "news":
                resp.say("Sorry, this feature is not available for news articles yet.")
                convo_states[call_sid]['in_article'] = False
                convo_states[call_sid]['article_index'] = None
                if convo_states[call_sid]['old_start_index']:
                    convo_states[call_sid]['start_index'] = convo_states[call_sid]['old_start_index']
                else:
                    convo_states[call_sid]['start_index'] = 0
                resp.redirect(url="/reader", method="GET")
                return resp
            else:
                logger.info("handle_in_article_state > if > elif")
                convo_states[call_sid]['in_article'] = False
                convo_states[call_sid]['buffer_on'] = True
                convo_states[call_sid]['start_index'] = 0
                prompt = f"""
                    I'd like to discuss the following article I just read, contents
                    enclosed between triple hash marks:

                    ###

                    {convo_states[call_sid]['article_text']}

                    ###

                    What do you think about this article?
                    """
                convo_states[call_sid].update({
                    'chat_mode': True,
                    'reader_mode': False,
                    'news_stale': True,
                    'arxiv_stale': True,
                    'reader_data': [],
                    'news_subject': None,
                    'arxiv_subject': None,
                    'article_index': None,
                    'buffer_on': True,
                    'read_buffer': {},
                })
                buffer_id = str(uuid.uuid4())
                convo_states[call_sid]['current_buffer_id'] = buffer_id
                convo_states[call_sid]['read_buffer'][buffer_id] = []
                convo_states[call_sid]['buffer_redirect'] = '/read'
                convo_states[call_sid]['buffer_on'] = True
                logger.info("Buffer id: %s", buffer_id)
                thread = threading.Thread(target=askgpt, kwargs={
                            'question': prompt,
                            'buffer_id': buffer_id,
                            'from_num': convo_states[call_sid]['from_num'],
                            'phone_or_text': 'phone',
                            'twiml': resp,
                            'ngrok_url': ngrok_url,
                            'call_sid': call_sid,
                            'model_name': convo_states[call_sid]['model_name'],
                            })
                thread.start()
                resp.redirect(url="/read", method="POST")
                resp.say("Preparing abstract for human read back.", voice="Polly.Joanna-Neural", language='en-US')
                return resp
        elif article_index == 2:
            message = Mail(
                from_email=os.environ.get('SENDGRID_FROM_EMAIL', 'noreply@example.com'),
                to_emails=email_map[convo_states[call_sid]['from_num']],
                subject=f"Requested link for article: {convo_states[call_sid]['buffer_title']}",
                html_content=f"""
                <b>Links:</b>
                <br/><br/>
                <a href="{convo_states[call_sid]['buffer_link'].replace('e-print', 'abs')}">{convo_states[call_sid]['buffer_link'].replace('e-print', 'abs')}</a>
                <br><br/>
                <b>Comments:<b>
                <br><br/>
                <p>
                {convo_states[call_sid]['buffer_comments']}
                </p>
                <br><br/>
                <b>Article contents:</b>
                <br><br/>
                <p>
                {convo_states[call_sid]['article_text']}
                </p>
                """
                )
            try:
                sg = SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))
                response = sg.send(message)
                logger.info("SendGrid response: %s", response.status_code)
                resp.say("E-mail sent, my friend.", voice="Polly.Joanna-Neural", language='en-US')

            except Exception as e:
                logger.error("SendGrid error: %s", e)
            convo_states[call_sid]['in_article'] = False
            convo_states[call_sid]['article_index'] = None
            if convo_states[call_sid]['old_start_index']:
                convo_states[call_sid]['start_index'] = convo_states[call_sid]['old_start_index']
            else:
                convo_states[call_sid]['start_index'] = 0
            resp.redirect(url="/reader", method="GET")
            return resp
        else:
            logger.info("handle_in_article_state > else")
            return resp

def handle_article_text_state(call_sid, resp, reader_data, article_index):
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("handle_article_text_state")
    if not convo_states[call_sid]['reader_source']:
        logger.info("handle_article_text_state > if1")
        resp.redirect(url="/transcribe", method="POST")
        return resp
    reader_source = convo_states[call_sid]['reader_source']
    if convo_states[call_sid][f"{convo_states[call_sid]['reader_source']}_subject"] is not None and not convo_states[call_sid]["in_article"]:
        logger.info("handle_article_text_state > if2")
        convo_states[call_sid]["in_article"] = True
        resp = handle_article_text(
                    article_index=article_index,
                    reader_data=reader_data,
                    reader_source=reader_source,
                    call_sid=call_sid,
                    subject=f"{convo_states[call_sid]['reader_source']}_subject",
                    resp=resp,
                    next_start_index=convo_states[call_sid]['next_start_index'],
                    source_index=convo_states[call_sid]['source_index'])
        return resp
    elif not convo_states[call_sid][f"{convo_states[call_sid]['reader_source']}_subject"]:
        logger.info("handle_article_text_state > elif2")
        logger.info(f"reader_source: {convo_states[call_sid]['reader_source']}")
        logger.info("attempting to set subject")

        reader_source = convo_states[call_sid]['reader_source']
        if reader_source in reader_source_mappings and article_index in reader_source_mappings[reader_source]:
            convo_states[call_sid].update({
                f"{reader_source}_url": reader_source_mappings[reader_source][article_index].get("url"),
                f"{reader_source}_subject": reader_source_mappings[reader_source][article_index].get("subject"),
                "source_index": article_index
            })
        resp.redirect(url="/reader", method="POST")
        return resp
    else:
        logger.info("handle_article_text_state > else2")
        convo_states[call_sid]["in_article"] = True
        resp.redirect(url="/reader", method="POST")
        return resp

def handle_article_text(article_index, reader_data, reader_source, call_sid, subject, resp, next_start_index, source_index):
    logger.info(return_dict_except_key(convo_states[call_sid], 'reader_data'))
    logger.info("handle_article_text")
    logger.info("in_article: %s", convo_states[call_sid]['in_article'])
    convo_states[call_sid]['old_start_index'] = convo_states[call_sid]['start_index']
    convo_states[call_sid]['buffer_link'] = reader_data[article_index]['link']
    convo_states[call_sid]['buffer_comments'] = reader_data[article_index]['comments']
    convo_states[call_sid]['buffer_title'] = reader_data[article_index]['title']
    if convo_states[call_sid]['in_article']:
        logger.info("handle_article_text > if")
        if article_index is not None and article_index < len(reader_data):
            logger.info("handle_article_text > if > if")
            logger.info("Article index: %s", article_index)
            logger.info("Getting article text for %s", reader_source)
            logger.info("Reader data for %s: %s", article_index, reader_data[article_index])
            article_full_text = get_article_text(reader_source, subject, article_index, reader_data[article_index], call_sid, source_index=source_index)
            convo_states[call_sid]['buffer_link'] = reader_data[article_index]['link']
            convo_states[call_sid]['buffer_comments'] = reader_data[article_index]['comments']
            convo_states[call_sid]['buffer_title'] = reader_data[article_index]['title']
            if article_full_text:
                logger.info("handle_article_text > if > if > if")
                logger.info("Article text: %s", article_full_text)
                convo_states[call_sid][f"{convo_states[call_sid]['reader_source']}_stale"] = True
                set_convo_states_in_article(call_sid, article_full_text)
                resp.redirect(url="/read", method="POST")
                return resp
        else:
            logger.info("handle_article_text > if >  else")
            resp.say("Unable to retrieve full text.")
            convo_states[call_sid]['in_article'] = False
            convo_states[call_sid]['read_buffer'][0] = "Unable to retrieve full text."
            resp.redirect(url=f"/reader?start_index={next_start_index}", method="GET")
            return resp
    else:
        logger.info("handle_article_text > else")
        convo_states[call_sid]['in_article'] = True
        convo_states[call_sid]['buffer_link'] = reader_data[article_index]['link']
        convo_states[call_sid]['buffer_comments'] = reader_data[article_index]['comments']
        convo_states[call_sid]['buffer_title'] = reader_data[article_index]['title']
        logger.info("handle_article_text > else")
        return resp


def pop_first_n_items(buffer, n=5):
    items = []
    for _ in range(min(n, len(buffer))):
        items.append(buffer.pop(0))
    return items
