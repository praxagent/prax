import json
import logging
import sqlite3
from datetime import UTC, datetime
from functools import lru_cache

from openai import OpenAI

from prax.settings import settings
from prax.token_management import chat_to_string, get_encoding_for_model, num_tokens_from_string

BASE_MODEL = settings.base_model


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _openai_client():
    if not settings.openai_key:
        raise RuntimeError("OPENAI_KEY is not configured")
    return OpenAI(api_key=settings.openai_key)


def add_dict_to_list(database_name, id, new_dict):
    # Retrieve the existing list of dictionaries for the ID
    existing_list = retrieve_dict(database_name, id)

    # If there's no existing list, create a new one
    if existing_list is None:
        existing_list = []

    # Append the new dictionary to the existing list
    new_dict['date'] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    existing_list.append(new_dict)

    # Store the updated list back in the database
    store_dict(database_name, id, existing_list)

def retrieve_dict(database_name, id):
    conn = sqlite3.connect(database_name)
    cursor = conn.cursor()

    # Fetch the JSON string from the database
    cursor.execute("SELECT data FROM conversations WHERE id = ?", (id,))
    row = cursor.fetchone()

    conn.close()

    if row:
        # Convert the JSON string back to a list of dictionaries
        return json.loads(row[0])
    else:
        return None

def store_dict(database_name, id, list_of_dicts):
    # Convert the list of dictionaries to a single JSON string
    list_of_dicts = summarize_and_replace(list_of_dicts, max_size=100000)

    conn = sqlite3.connect(database_name)
    cursor = conn.cursor()
    json_data = json.dumps(list_of_dicts)

    # Insert the JSON string into the database with the specified ID
    cursor.execute("INSERT OR REPLACE INTO conversations (id, data) VALUES (?, ?)", (id, json_data))
    conn.commit()
    conn.close()


def summarize_and_replace(list_of_dicts, max_size=100000):
    if num_tokens_from_string(chat_to_string(list_of_dicts), get_encoding_for_model(BASE_MODEL)) >= max_size:
        text_to_summarize = "; ".join([f"role: {entry['role']}, content: {entry['content']}" for entry in list_of_dicts[1:4]])


        client = _openai_client()
        response = client.chat.completions.create(model=BASE_MODEL,
        messages=[{
            "role": "assistant",
            "content": (f"Please succinctly summarize the following text, including both"
                        f"what the user and system said: {text_to_summarize}")
            }],
        max_tokens=4096,
        n=1,
        stop=None,
        temperature=0.3)

        summary = response.choices[0].message.content
        try:
            summary_dict = {"date": list_of_dicts[3]["date"],
                            "role": "assistant",
                            "content": f"Summary of prior chats: {summary}" }
        except (KeyError, IndexError):
            summary_dict = {"date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
                            "role": "assistant",
                            "content": f"Summary of prior chats: {summary}" }

        list_of_dicts = [list_of_dicts[0]] + [summary_dict] + list_of_dicts[4:]

    return list_of_dicts

def chats_to_prompt(chat_log):
    messages = []
    for entry in chat_log:
        if entry["role"] == "user":
            messages.append({"role": "user", "content": entry["content"]})
        elif entry["role"] == "assistant" or entry["role"]=="system":
            messages.append({"role": "assistant", "content": entry["content"]})
    return messages

def init_database(database_name):
    conn = sqlite3.connect(database_name)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY,
        data TEXT NOT NULL
    );
    ''')
    conn.commit()
    conn.close()
