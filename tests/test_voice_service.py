import importlib

import pytest


def test_handle_transcribe_greets_new_call(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.services.voice_service'))

    states = {}
    monkeypatch.setitem(module.num_to_names, '+10000000000', 'Tester')
    captured = {}

    def fake_gather(resp, language_code):
        captured['language'] = language_code

    monkeypatch.setattr(module, 'gather_speech', fake_gather)

    service = module.VoiceService(states=states, base_model='gpt-test')
    service.states = states

    resp = service.handle_transcribe('CS1', '+10000000000', {})
    xml = resp.to_xml()
    assert "Hello" in xml
    assert captured['language'] == 'en'
    assert 'CS1' in states


def test_handle_transcribe_unauthorized(monkeypatch):
    module = importlib.import_module('prax.services.voice_service')
    service = module.VoiceService(states={}, base_model='gpt-test')
    with pytest.raises(module.VoiceAccessError):
        service.handle_transcribe('CSX', '+19999999999', {})


def test_handle_response_redirect(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.services.voice_service'))
    monkeypatch.setitem(module.num_to_names, '+10000000000', 'Tester')
    states = {'CS1': {'language': 'en', 'read_buffer': {}, 'buffer_redirect': None}}

    def fake_preprocess(voice_input, resp, gather, call_sid):
        return resp, 'hello'

    captured_args = {}

    class ImmediateThread:
        def __init__(self, target=None, args=None, kwargs=None):
            self.target = target
            self.args = args or ()
            self.kwargs = kwargs or {}
        def start(self):
            captured_args['args'] = self.args

    monkeypatch.setattr(module, 'preprocess_input', fake_preprocess)
    monkeypatch.setattr(module, 'conversation_service', type('FakeCS', (), {'reply': staticmethod(lambda *a: 'ok')})())
    monkeypatch.setattr(module.threading, 'Thread', ImmediateThread)
    service = module.VoiceService(states=states, base_model='gpt-test')

    resp = service.handle_response('CS1', '+10000000000', 'hi', 'https://ngrok.test')
    assert '/read' in resp.to_xml()
    # _stream_to_buffer is called with (call_sid, buffer_id, from_num, question)
    assert captured_args['args'][0] == 'CS1'  # call_sid
    assert captured_args['args'][2] == '+10000000000'  # from_num
    assert captured_args['args'][3] == 'hi'  # question
