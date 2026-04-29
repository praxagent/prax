import logging
import re
import threading

import pytrie

from prax.conversation_memory import add_dict_to_list
from prax.convo_states import convo_states
from prax.helpers_dictionaries import (
    initial_user_prompts,
    language_prompts,
    menu_strings,
    news_menu_strings,
    transcription_mapping,
    words_to_languages,
)
from prax.helpers_functions import delete_temp_files
from prax.readers.news.deutschlandfunk_radio import dlf_process
from prax.readers.news.npr_top_hour import npr_process
from prax.services.state_paths import ensure_conversation_db

logger = logging.getLogger(__name__)


def create_trie(mapping):
    trie = pytrie.Trie()
    for correct_word, mis_transcribed_words in mapping.items():
        for mis_transcribed_word in mis_transcribed_words:
            trie[mis_transcribed_word] = correct_word
    return trie

transcription_trie = create_trie(transcription_mapping)


program_choices = transcription_mapping.keys()

def menu_correct_transcription(transcribed_text):

    logger.info("Transcribed: %s", transcribed_text)
    corrected_text_buffer = re.sub(r'[.,?!]+$', '', str(transcribed_text.lower()).lower())
    transcribed_text = corrected_text_buffer.strip()
    transcribed_text_lower = transcribed_text.lower()
    return transcription_trie.get(transcribed_text_lower, transcribed_text_lower)

def execute_state_redirect(convo_states, call_sid, resp, key_word):
    logger.info("User selection: %s", key_word)

    if key_word in ['archive', 'news']:
        logger.info("User selected archive or news")
        convo_states[call_sid]['reader_mode'] = True
        convo_states[call_sid]['chat_mode'] = False
        convo_states[call_sid]['buffer_redirect'] = "/reader"
    elif key_word in ['playlist']:
        logger.info("User selected playlist")
        convo_states[call_sid]['reader_mode'] = False
        convo_states[call_sid]['chat_mode'] = False
        convo_states[call_sid]['buffer_redirect'] = "/music"
    else:
        logger.info("User selected chat")
        convo_states[call_sid]['reader_mode'] = False
        convo_states[call_sid]['chat_mode'] = True
        convo_states[call_sid]['buffer_redirect'] = "/respond"
    return resp

def preprocess_input(user_input_raw, resp, gather, call_sid):

    logger.info("Raw input: %s", user_input_raw)
    if user_input_raw:
        logger.info("User input detected")
        cleaned_input = re.sub(r'[.,?!]+$', '', str(user_input_raw).lower())
        user_input = menu_correct_transcription(cleaned_input)
        logger.info("Cleaned input: %s", user_input)

        # If changing language
        for word in words_to_languages.keys():
            convo_states[call_sid]['buffer_on'] = False
            if user_input == word.lower():
                detected_word = word
                language_code = words_to_languages[detected_word]
                logger.info("Language change detected, updating prompts")
                logger.info(convo_states[call_sid]['from_num'][1:])
                logger.info(language_code)

                thread1 = threading.Thread(target=add_dict_to_list, kwargs={
                        'database_name': ensure_conversation_db(),
                        'id': int(convo_states[call_sid]['from_num'][1:]),
                        'new_dict': {'role': 'user', 'content': language_prompts[language_code]},
                        })
                thread1.start()
                thread2 = threading.Thread(target=add_dict_to_list, kwargs={
                        'database_name': ensure_conversation_db(),
                        'id': int(convo_states[call_sid]['from_num'][1:]),
                        'new_dict': {'role': 'user', 'content': initial_user_prompts[language_code]},
                        })
                thread2.start()

                convo_states[call_sid]['language'] = language_code
                convo_states[call_sid]['buffer_redirect'] = "/transcribe"
                resp.say(f"Language: {detected_word}", voice="Polly.Joanna-Neural", language='en-US')
                return resp, None


        # If we haven't changed languages, and program choice isn't made, pass through
        if user_input and user_input not in program_choices:
            convo_states[call_sid]['buffer_on'] = True
            convo_states[call_sid]['buffer_redirect'] = None
            logger.info("Not in program_choices: %s", user_input)
            return resp, user_input
        else:
            # If a key program choice is detected, handle case by case
            convo_states[call_sid]['buffer_on'] = False
            match user_input:
                case "astro":
                    logger.info("User selected astro")
                    convo_states[call_sid]['buffer_redirect'] = "/reader"
                    convo_states[call_sid]['arxiv_stale'] = True
                    convo_states[call_sid]['reader_source'] = 'arxiv'
                    convo_states[call_sid]['chat_mode'] = False
                    convo_states[call_sid]['reader_mode'] = True
                    convo_states[call_sid]['arxiv_subject'] = "astro"
                    convo_states[call_sid]['buffer_on'] = True
                    convo_states[call_sid]['read_buffer'] = {}
                    convo_states[call_sid]["arxiv_url"] = ""
                    resp = execute_state_redirect(convo_states, call_sid, resp, key_word="archive")
                    return resp, None
                case "archive":
                    logger.info("User selected archive")
                    convo_states[call_sid]['buffer_redirect'] = "/reader"
                    convo_states[call_sid]['arxiv_stale'] = True
                    convo_states[call_sid]['reader_source'] = 'arxiv'
                    convo_states[call_sid]['chat_mode'] = False
                    convo_states[call_sid]['reader_mode'] = True
                    convo_states[call_sid]['arxiv_subject'] = None
                    convo_states[call_sid]["arxiv_url"] = None
                    convo_states[call_sid]['buffer_on'] = True
                    convo_states[call_sid]['read_buffer'] = {}
                    resp = execute_state_redirect(convo_states, call_sid, resp, key_word="archive")

                    return resp, None
                case "radio":
                    logger.info("User selected radio")
                    convo_states[call_sid]['reader_mode'] = False
                    convo_states[call_sid]['chat_mode'] = True
                    convo_states[call_sid]['buffer_redirect'] = None
                    convo_states[call_sid]['buffer_on'] = False
                    convo_states[call_sid]['read_buffer'] = {}
                    resp = execute_state_redirect(convo_states, call_sid, resp, key_word="radio")
                    resp.say("Preparing NPR update, reducing source volume, one moment.",
                            voice="Polly.Joanna-Neural",
                            language='en-US')
                    temp_file = npr_process(call_sid)
                    resp.play(temp_file, loop=1)
                    gather.say("Returning to chat mode, welcome back to GPT.",
                            voice="Polly.Joanna-Neural",
                            language='en-US')
                    convo_states[call_sid]['buffer_redirect'] = None
                    resp.append(gather)
                    resp.redirect('/transcribe', method='POST')
                    return resp, None
                case "podcast":
                    logger.info("User selected podcast")
                    convo_states[call_sid]['reader_mode'] = False
                    convo_states[call_sid]['chat_mode'] = True
                    convo_states[call_sid]['buffer_on'] = False
                    convo_states[call_sid]['read_buffer'] = {}
                    convo_states[call_sid]['buffer_redirect'] = None
                    resp = execute_state_redirect(
                        convo_states,
                        call_sid,
                        resp,
                        key_word="podcast")
                    resp.say("Preparing the German and lowering base volume.",
                            voice="Polly.Joanna-Neural",
                            language='en-US')
                    temp_file = dlf_process(call_sid)
                    resp.play(temp_file, loop=1)
                    gather.say("Returning to chat mode, welcome back to GPT.",
                            voice="Polly.Joanna-Neural",
                            language='en-US')
                    convo_states[call_sid]['buffer_redirect'] = None
                    resp.append(gather)
                    resp.redirect('/transcribe', method='POST')
                    return resp, None
                case "music":
                    resp = execute_state_redirect(
                        convo_states,
                        call_sid,
                        resp,
                        key_word="music")
                    convo_states[call_sid]['buffer_on'] = False
                    convo_states[call_sid]['read_buffer'] = {}
                    return resp, None
                case "news":
                    convo_states[call_sid]['news_stale'] = True
                    convo_states[call_sid]['reader_source'] = 'news'
                    convo_states[call_sid]['news_subject'] = None
                    convo_states[call_sid]['chat_mode'] = False
                    convo_states[call_sid]['reader_mode'] = True
                    convo_states[call_sid]['buffer_on'] = True
                    convo_states[call_sid]['read_buffer'] = {}
                    convo_states[call_sid]['buffer_redirect'] = None
                    resp = execute_state_redirect(
                        convo_states,
                        call_sid,
                        resp,
                        key_word="news")
                    return resp, None
                case "playlist":
                    resp = execute_state_redirect(
                        convo_states,
                        call_sid,
                        resp,
                        key_word="playlist")
                    return resp, None
                case "menu":
                    convo_states[call_sid]['buffer_on'] = True
                    convo_states[call_sid]['read_buffer'] = {}
                    if convo_states[call_sid]['reader_mode']:
                        _menu = news_menu_strings
                    elif convo_states[call_sid]['chat_mode']:
                        _menu = menu_strings
                    else:
                        logger.error("Invalid state: %s", convo_states[call_sid])
                        return resp, None

                    logger.info("Reading menu")
                    for i in _menu:
                        logger.info(i)
                        gather.say(i,
                                    voice="Polly.Joanna-Neural",
                                    language='en-US')
                        gather.pause(length=1)
                        resp.append(gather)
                    return resp, None
                case "hang up":
                    logger.info("hang up")
                    delete_temp_files(call_sid)
                    resp.say(
                        "Hanging up per your request, goodbye friend!",
                        voice="Polly.Joanna-Neural"
                        )
                    resp.hangup()
                    convo_states[call_sid]['buffer_redirect'] = "/transcribe"
                    return resp, None
                case "skip":
                    logger.info("skipping")
                    if convo_states[call_sid]['reader_mode']:
                        resp.redirect('/reader', method='GET')
                    else:
                        resp.redirect('/transcribe', method='POST')
                    return resp, None
                case "exit":
                    logger.info("exiting")
                    convo_states[call_sid]['start_index'] = 0
                    convo_states[call_sid]['chat_mode'] = True
                    convo_states[call_sid]['reader_mode'] = False
                    convo_states[call_sid]['news_stale'] = True
                    convo_states[call_sid]['arxiv_stale'] = True
                    convo_states[call_sid]['reader_data'] = []
                    convo_states[call_sid]['news_subject'] = None
                    convo_states[call_sid]['arxiv_subject'] = None
                    convo_states[call_sid]['reader_source'] = None
                    convo_states[call_sid]['article_content'] = None
                    convo_states[call_sid]['source_index'] = None
                    convo_states[call_sid]['buffer_on'] = False
                    convo_states[call_sid]['read_buffer'] = {}
                    convo_states[call_sid]['buffer_redirect'] = None

                    resp.say("Returning to chat mode, welcome back to GPT.",
                        voice="Polly.Joanna-Neural",
                        language='en-US')
                    resp.redirect(url="/transcribe", method="POST")
                    return resp, None
                case _:
                    logger.info("Pass through other inputs %s", user_input)
                    convo_states[call_sid]['buffer_redirect'] = None
                    return resp, user_input

    convo_states[call_sid]['buffer_redirect'] = None
    convo_states[call_sid]['buffer_on'] = True
    return resp, None
