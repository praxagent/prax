"""Encapsulates Twilio voice workflow logic for easier testing and reuse."""
from __future__ import annotations

import logging
import threading
import uuid

from nltk.tokenize import sent_tokenize
from twilio.twiml.voice_response import VoiceResponse

from prax.convo_states import convo_states
from prax.filter_input import preprocess_input
from prax.helpers_dictionaries import num_to_names, voices
from prax.helpers_functions import create_convo_state, gather_speech
from prax.services.conversation_service import conversation_service
from prax.settings import settings
from prax.twilio_voice_utils import create_gather_instance

logger = logging.getLogger(__name__)


class VoiceAccessError(Exception):
    """Raised when a caller is not authorized."""


class VoiceService:
    def __init__(
        self,
        states: dict | None = None,
        base_model: str | None = None,
    ) -> None:
        self.states = states if states is not None else convo_states
        self.base_model = base_model

    def _ensure_state(self, call_sid: str, from_num: str) -> tuple[str, bool]:
        if from_num not in num_to_names:
            raise VoiceAccessError()

        if hasattr(self.states, 'ensure'):
            return self.states.ensure(call_sid, from_num)

        if call_sid not in self.states:
            self.states[call_sid] = create_convo_state()
            self.states[call_sid]['from_num'] = from_num
            self.states[call_sid]['language'] = 'en'
            return 'en', True
        return self.states[call_sid].get('language', 'en'), False

    def handle_transcribe(self, call_sid: str, from_num: str, session_store) -> VoiceResponse:
        language_code, is_new = self._ensure_state(call_sid, from_num)
        session_store['language'] = language_code
        self.states[call_sid]['read_buffer'] = {}

        response = VoiceResponse()
        if is_new:
            response.say(
                voice=voices.get(language_code, voices['en']),
                message=(
                    f"Hello {num_to_names[from_num]}. Please wait for the high pitch beep "
                    "before speaking, including after I answer your questions."
                ),
            )
        gather_speech(response, language_code)
        return response

    def _stream_to_buffer(self, call_sid: str, buffer_id: str, from_num: str, question: str) -> None:
        """Run the agent and push sentences into the read buffer for TTS playback."""
        state = self.states.get(call_sid, {})
        state['buffer_on'] = True
        state['in_article'] = False
        state.setdefault('read_buffer', {})[buffer_id] = []

        answer = conversation_service.reply(from_num, question)
        for sentence in sent_tokenize(answer):
            state['read_buffer'][buffer_id].append(sentence)
        state['read_buffer'][buffer_id].append("#FINISHED#")

    def handle_response(self, call_sid: str, from_number: str, voice_input: str, ngrok_url: str) -> VoiceResponse:
        resp = VoiceResponse()
        state = self.states.setdefault(call_sid, {'language': 'en', 'read_buffer': {}})
        language = state.get('language', 'en')

        gather = create_gather_instance(language, "/respond", "POST")
        resp, user_input = preprocess_input(voice_input, resp, gather, call_sid)

        if user_input:
            if not state.get('buffer_redirect'):
                buffer_id = str(uuid.uuid4())
                state['current_buffer_id'] = buffer_id
                state.setdefault('read_buffer', {})[buffer_id] = []
                state['buffer_redirect'] = None
                state['buffer_on'] = True

                thread = threading.Thread(
                    target=self._stream_to_buffer,
                    args=(call_sid, buffer_id, from_number, voice_input),
                )
                thread.start()

                resp.redirect(method="POST", url="/read")
                return resp

            redirect = state.get('buffer_redirect')
            if redirect:
                resp.redirect(method="POST", url=redirect)
                return resp

        if not state.get('buffer_redirect'):
            state['buffer_redirect'] = '/transcribe'

        resp.redirect(method="POST", url=state['buffer_redirect'])
        return resp


voice_service = VoiceService(base_model=settings.base_model)
