import concurrent.futures
import logging

import openai
from openai import OpenAI

from prax.convo_states import convo_states
from prax.settings import settings

logger = logging.getLogger(__name__)

client = OpenAI(api_key=settings.openai_key)
BASE_MODEL = settings.base_model


def chunk_latex_file(filepath, max_chunk_size=2096):
    with open(filepath) as file:
        latex = file.read()

    latex_chunks = []
    while len(latex) > max_chunk_size:
        split_point = max_chunk_size
        while split_point > 0 and latex[split_point] not in ['\\', '\n']:
            split_point -= 1

        if split_point == 0:
            split_point = max_chunk_size

        latex_chunks.append(latex[:split_point])
        latex = latex[split_point:]

    latex_chunks.append(latex)

    return latex_chunks


def latex_chunk_to_english(input_text, call_sid=None):
    conversation = [
    {'role': 'system', 'content': 'You are a helpful assistant.'},
    {'role': 'user', 'content': """
        Convert the following to spoken language and leave everything the same except
        convert LaTeX code into English words, for example, $x^2$ should be read as x
        squared, and $x_1$ should be read as x sub one, C/O would be said C slash O,
        and so on. If there is a big table, summarize it. If there are lots of authors,
        just say the first author and et al. If there is a big equation, summarize it.
        Be sure to maintain as much accuracy with the original material as possible.
        Do not tell me extraneous things like
        "This is a document that is using the RevTeX 4.1 template with two columns, PRL style"
        or anything to do with the formatting. Just read the document as if you are a human
        with a degree in math and physics who understands how to say the latex out loud and you
        are reading this article to a colleague. If you are unsure how to say something, just
        say it as best as you can. If you are unsure how to say a symbol, just say the symbol.
        I'll be sending you chunks at a time, so if the chunk i send just has latex preamble
        stuff, don't say
        "I apologize, but I can't convert this document as it's merely a bunch of LaTeX package imports and custom commands."
        Just say nothing and wait for the next chunk.
        Also don't say
        "I'm ready to help. Please send the next chunk of the document."
        Things like M_A should be "M sub A".
        Just read the document as if you were human to another human.
        """},
    {'role': 'user', 'content': input_text}
    ]

    output_text = None
    try:
        response = client.chat.completions.create(model=BASE_MODEL,
        messages=conversation,
        max_tokens=4096)
        output_text = [response.choices[0].message.content]
    except openai.RateLimitError as e:
        logger.error("RateLimitError occurred: %s", e)

    return output_text


def convert_latex_file(filepath):
    latex_chunks = chunk_latex_file(filepath)

    english_chunks = []
    for chunk in latex_chunks:
        english = latex_chunk_to_english(chunk)
        english_chunks.append(english)

    return english_chunks


def concurrent_convert_latex_file(filepath):
    latex_chunks = chunk_latex_file(filepath)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_chunk = {executor.submit(latex_chunk_to_english, chunk): chunk for chunk in latex_chunks}

        english_chunks = []
        for future in concurrent.futures.as_completed(future_to_chunk):
            english = future.result()
            english_chunks.append(english)

    return english_chunks


def latex_to_english(reader_data, call_sid):
    ngrok_url = settings.ngrok_url or ""
    input_text = reader_data['abstract']
    if input_text:
        logger.info("latex_to_english: %s", input_text)
        conversation = [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': 'Convert the following to spoken language and leave everything the same except convert LaTeX code into English words, for example, $x^2$ should be read as x squared, and $x_1$ should be read as x sub one, C/O would be said C slash O, and so on.'},
        {'role': 'user', 'content': input_text}
        ]
        logger.info("calling chatGPT with: %s", conversation)

        client.calls(call_sid).update(url=f"{ngrok_url}/conference", method='POST')
        try:
            response = client.chat.completions.create(model=BASE_MODEL,
            messages=conversation,
            max_tokens=4096)
            convo_states[call_sid]['read_buffer'] = [response.choices[0].message.content]
        except openai.RateLimitError as e:
            convo_states[call_sid]['read_buffer'] = ["Sorry friend, OpenAI is overloaded at the moment. Please try again in a few moments."]
            logger.error("RateLimitError occurred: %s", e)
    else:
        convo_states[call_sid]['read_buffer'] = ["No content was found where expected."]

    convo_states[call_sid]['buffer_redirect'] = f"{ngrok_url}/reader"
    client.calls(call_sid).update(url=f"{ngrok_url}/read", method='POST')
