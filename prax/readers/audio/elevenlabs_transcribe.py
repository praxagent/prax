import os

import requests

from prax.settings import settings

ELEVENLABS_API_KEY = settings.elevenlabs_api_key
NGROCK_URL = settings.ngrok_url

def text_to_speech_elevenlabs(input_text, id, user_id, voice_id="EXAVITQu4vr4xnSDxMaL"):

    CHUNK_SIZE = 1024
    #Bella
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    headers = {
    "Accept": "audio/mpeg",
    "Content-Type": "application/json",
    "xi-api-key": ELEVENLABS_API_KEY
    }

    data = {
    "text": input_text,
    "model_id": "eleven_monolingual_v1",
    "voice_settings": {
        "stability": 0.5,
        "similarity_boost": 0.5
    }
    }

    response = requests.post(url, json=data, headers=headers)

    if not os.path.exists(f"./static/temp/{user_id}/"):
        os.makedirs(f"./static/temp/{user_id}/")

    with open(f"./static/temp/{user_id}/{id}.mp3", 'wb') as f:
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                f.write(chunk)

    return f"{NGROCK_URL}/static/temp/{user_id}/{id}.mp3"
