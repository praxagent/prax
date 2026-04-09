import importlib
import threading

import pytest


def test_sms_service_handles_help(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    sent = {}

    def fake_send(message, number):
        sent['payload'] = (message, number)

    monkeypatch.setattr(module, 'send_sms', fake_send)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'help',
        'NumMedia': '0',
    }

    body, status = module.sms_service.process(payload, 'https://ngrok.test')
    assert status == 200
    assert 'How we can interact' in sent['payload'][0]
    assert 'PDF' in sent['payload'][0]


def test_sms_service_rejects_unknown():
    module = importlib.import_module('prax.services.sms_service')
    payload = {'From': '+19999999999', 'MessageSid': 'SMX', 'Body': 'hi', 'NumMedia': '0'}
    with pytest.raises(module.SmsAccessError):
        module.sms_service.process(payload, 'https://ngrok.test')


def test_sms_service_image(monkeypatch):
    """Image attachments should route through the agent with the image URL."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    sent = {}
    agent_calls = []

    def fake_reply(from_number, text):
        agent_calls.append(text)
        return "I see a cat in the image!"

    monkeypatch.setattr(module.conversation_service, 'reply', fake_reply)
    monkeypatch.setattr(module, 'send_sms', lambda message, to: sent.setdefault('text', message))

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': '',
        'NumMedia': '1',
        'MediaContentType0': 'image/jpeg',
        'MediaUrl0': 'https://img.test',
    }

    module.sms_service.process(payload, 'https://ngrok.test')
    assert sent['text'] == 'I see a cat in the image!'
    assert 'https://img.test' in agent_calls[0]
    assert '[Image attachment' in agent_calls[0]


def test_sms_image_with_caption(monkeypatch):
    """Image with text body should include the caption in the agent message."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    agent_calls = []

    def fake_reply(from_number, text):
        agent_calls.append(text)
        return "That's a chart showing revenue growth."

    monkeypatch.setattr(module.conversation_service, 'reply', fake_reply)
    monkeypatch.setattr(module, 'send_sms', lambda msg, to: None)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'What does this chart show?',
        'NumMedia': '1',
        'MediaContentType0': 'image/png',
        'MediaUrl0': 'https://img.test/chart.png',
    }

    module.sms_service.process(payload, 'https://ngrok.test')
    assert 'What does this chart show?' in agent_calls[0]
    assert 'https://img.test/chart.png' in agent_calls[0]


def test_sms_text_routes_through_agent(monkeypatch):
    """Regular text messages should go through conversation_service and SMS back."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    sent = {}

    def fake_reply(from_number, text):
        return f"echo: {text}"

    def fake_send(message, number):
        sent['msg'] = message

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module.conversation_service, 'reply', fake_reply)
    monkeypatch.setattr(module, 'send_sms', fake_send)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'What is the meaning of life?',
        'NumMedia': '0',
    }

    body, status = module.sms_service.process(payload, 'https://ngrok.test')
    assert status == 200
    assert sent['msg'] == 'echo: What is the meaning of life?'


def test_sms_agent_error_sends_apology(monkeypatch):
    """If the agent throws, the user should get an apology SMS, not silence."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    sent = {}

    def exploding_reply(from_number, text):
        raise RuntimeError("LLM is down")

    def fake_send(message, number):
        sent['msg'] = message

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module.conversation_service, 'reply', exploding_reply)
    monkeypatch.setattr(module, 'send_sms', fake_send)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'hello',
        'NumMedia': '0',
    }

    body, status = module.sms_service.process(payload, 'https://ngrok.test')
    assert status == 200
    assert 'sorry' in sent['msg'].lower()


def test_sms_search_goes_through_agent(monkeypatch):
    """'search X' should go through the agent, not be handled specially."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    agent_calls = []

    def fake_reply(from_number, text):
        agent_calls.append(text)
        return "search results"

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module.conversation_service, 'reply', fake_reply)
    monkeypatch.setattr(module, 'send_sms', lambda msg, to: None)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'search best pizza near me',
        'NumMedia': '0',
    }

    module.sms_service.process(payload, 'https://ngrok.test')
    assert agent_calls == ['search best pizza near me']


def test_sms_npr_goes_through_agent(monkeypatch):
    """'npr' should go through the agent, not be handled as a special command."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    agent_calls = []

    def fake_reply(from_number, text):
        agent_calls.append(text)
        return "https://npr.org/latest.mp3"

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module.conversation_service, 'reply', fake_reply)
    monkeypatch.setattr(module, 'send_sms', lambda msg, to: None)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'npr',
        'NumMedia': '0',
    }

    module.sms_service.process(payload, 'https://ngrok.test')
    assert agent_calls == ['npr']


class TestMaybeAutoCaptureRaw:
    """Tests for _maybe_auto_capture_raw — the SMS-side auto-capture of
    shared URLs into library/raw/. Verifies fixes from the ROP-note
    investigation: the raw entry should now include the fetched page
    content, not just the user's message."""

    def _ws(self, tmp_path, monkeypatch):
        from prax.services import library_service, workspace_service
        monkeypatch.setattr(
            workspace_service.settings, "workspace_dir", str(tmp_path),
        )
        monkeypatch.setattr(
            library_service, "workspace_root", lambda uid: str(tmp_path / uid),
        )
        return tmp_path

    def test_captures_with_fetched_content(self, tmp_path, monkeypatch):
        """When Jina returns clean markdown, the raw entry embeds it."""
        self._ws(tmp_path, monkeypatch)
        module = importlib.reload(importlib.import_module('prax.services.sms_service'))

        fake_md = "# ROP Chaining\n\nReal article content goes here."
        monkeypatch.setattr(
            module, '_fetch_url_as_markdown',
            lambda url, timeout=20: fake_md,
        )

        slug = module._maybe_auto_capture_raw(
            "test_user_1",
            "Please save this https://example.com/rop",
        )
        assert slug is not None

        from prax.services.library_service import get_raw
        raw = get_raw("test_user_1", slug)
        assert raw is not None
        assert "Fetched page content" in raw["content"]
        assert "Real article content" in raw["content"]
        assert "User message" in raw["content"]

    def test_captures_with_fetch_failure(self, tmp_path, monkeypatch):
        """When Jina fails, the raw entry still stores the user message."""
        self._ws(tmp_path, monkeypatch)
        module = importlib.reload(importlib.import_module('prax.services.sms_service'))

        monkeypatch.setattr(
            module, '_fetch_url_as_markdown',
            lambda url, timeout=20: None,
        )

        slug = module._maybe_auto_capture_raw(
            "test_user_2",
            "check this https://example.com/broken",
        )
        assert slug is not None

        from prax.services.library_service import get_raw
        raw = get_raw("test_user_2", slug)
        assert raw is not None
        assert "User message" in raw["content"]
        assert "Could not fetch" in raw["content"]
        # Points at the recovery path.
        assert "note_from_url" in raw["content"]

    def test_skips_no_url(self, tmp_path, monkeypatch):
        self._ws(tmp_path, monkeypatch)
        module = importlib.reload(importlib.import_module('prax.services.sms_service'))
        assert module._maybe_auto_capture_raw("test_user_3", "hello world") is None

    def test_skips_pdf_url(self, tmp_path, monkeypatch):
        """PDF URLs are handled by the dedicated PDF flow, not raw-captured."""
        self._ws(tmp_path, monkeypatch)
        module = importlib.reload(importlib.import_module('prax.services.sms_service'))
        assert module._maybe_auto_capture_raw(
            "test_user_4", "https://example.com/paper.pdf",
        ) is None


def test_sms_url_goes_through_agent(monkeypatch):
    """Non-PDF URLs should go through the agent.

    The URL is also auto-captured into library/raw/ and the agent
    receives a SYSTEM-prefixed hint so it knows the content is saved.
    The Jina fetch inside auto-capture is stubbed to None here so the
    test stays offline and deterministic.
    """
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    agent_calls = []

    def fake_reply(from_number, text):
        agent_calls.append(text)
        return "Here's a summary of that page..."

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module.conversation_service, 'reply', fake_reply)
    monkeypatch.setattr(module, 'send_sms', lambda msg, to: None)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)
    # Keep the auto-capture fetch offline and deterministic.
    monkeypatch.setattr(module, '_fetch_url_as_markdown', lambda url, timeout=20: None)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'https://example.com/article',
        'NumMedia': '0',
    }

    module.sms_service.process(payload, 'https://ngrok.test')
    assert len(agent_calls) == 1
    sent_to_agent = agent_calls[0]
    assert sent_to_agent.startswith('https://example.com/article')
    assert '[SYSTEM: captured to library/raw/' in sent_to_agent


# --- PDF + workspace tests ---


def _setup_pdf_mocks(module, monkeypatch, fake_markdown="# Paper\n\nContent."):
    """Common mock setup for PDF workspace tests."""
    import os
    import tempfile

    # Create a real temp PDF so save_binary has something to copy
    fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
    os.write(fd, b"%PDF-fake")
    os.close(fd)

    monkeypatch.setattr(module, 'process_pdf_url_with_paths', lambda url: (fake_markdown, pdf_path))
    monkeypatch.setattr(module, 'save_file', lambda uid, fn, content: None)
    monkeypatch.setattr(module, 'save_binary', lambda uid, fn, src: None)

    return pdf_path


def test_sms_pdf_attachment_detected(monkeypatch):
    """A Twilio PDF media attachment should trigger _handle_pdf with workspace save."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    sent = []
    agent_prompts = []

    _setup_pdf_mocks(module, monkeypatch)

    def fake_reply(from_number, text):
        agent_prompts.append(text)
        return "This paper is about X."

    def fake_send(message, number):
        sent.append(message)

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module.conversation_service, 'reply', fake_reply)
    monkeypatch.setattr(module, 'send_sms', fake_send)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': '',
        'NumMedia': '1',
        'MediaContentType0': 'application/pdf',
        'MediaUrl0': 'https://api.twilio.com/media/pdf123',
    }

    body, status = module.sms_service.process(payload, 'https://ngrok.test')
    assert status == 200
    assert 'Processing' in sent[0]
    assert sent[1] == 'This paper is about X.'
    assert 'workspace' in agent_prompts[0].lower()


def test_sms_arxiv_link_saves_to_workspace(monkeypatch):
    """An arxiv link should save markdown + PDF to workspace."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    sent = []
    workspace_saves = []

    import os
    import tempfile

    fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
    os.write(fd, b"%PDF-fake")
    os.close(fd)

    monkeypatch.setattr(module, 'process_pdf_url_with_paths', lambda url: ("# Arxiv Paper", pdf_path))
    monkeypatch.setattr(module, 'save_file', lambda uid, fn, content: workspace_saves.append(('md', fn)))
    monkeypatch.setattr(module, 'save_binary', lambda uid, fn, src: workspace_saves.append(('pdf', fn)))
    monkeypatch.setattr(module.conversation_service, 'reply', lambda fn, t: "Summary.")
    monkeypatch.setattr(module, 'send_sms', lambda msg, to: sent.append(msg))

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'https://arxiv.org/abs/2301.12345',
        'NumMedia': '0',
    }

    module.sms_service.process(payload, 'https://ngrok.test')

    # Should save both markdown and PDF with arxiv ID as filename
    assert ('md', '2301.12345.md') in workspace_saves
    assert ('pdf', '2301.12345.pdf') in workspace_saves


def test_sms_pdf_url_detected(monkeypatch):
    """A direct .pdf URL should trigger PDF extraction with workspace."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    workspace_saves = []
    _setup_pdf_mocks(module, monkeypatch)
    monkeypatch.setattr(module, 'save_file', lambda uid, fn, content: workspace_saves.append(fn))

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module.conversation_service, 'reply', lambda fn, t: "ok")
    monkeypatch.setattr(module, 'send_sms', lambda msg, to: None)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'https://example.com/paper.pdf',
        'NumMedia': '0',
    }

    module.sms_service.process(payload, 'https://ngrok.test')
    assert 'paper.md' in workspace_saves


def test_sms_pdf_error_sends_apology(monkeypatch):
    """If PDF extraction fails, user should get an apology SMS."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    sent = []

    def exploding_pdf(url):
        raise RuntimeError("Java not found")

    def fake_send(message, number):
        sent.append(message)

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module, 'process_pdf_url_with_paths', exploding_pdf)
    monkeypatch.setattr(module, 'send_sms', fake_send)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'https://arxiv.org/abs/2301.99999',
        'NumMedia': '0',
    }

    body, status = module.sms_service.process(payload, 'https://ngrok.test')
    assert status == 200
    assert 'Processing' in sent[0]
    assert 'sorry' in sent[1].lower()


def test_sms_pdf_markdown_has_frontmatter(monkeypatch):
    """The markdown saved to workspace should include source frontmatter."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    saved_content = {}
    _setup_pdf_mocks(module, monkeypatch)
    monkeypatch.setattr(module, 'save_file', lambda uid, fn, content: saved_content.update({fn: content}))

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module.conversation_service, 'reply', lambda fn, t: "ok")
    monkeypatch.setattr(module, 'send_sms', lambda msg, to: None)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'https://arxiv.org/abs/2301.12345',
        'NumMedia': '0',
    }

    module.sms_service.process(payload, 'https://ngrok.test')

    md_content = saved_content['2301.12345.md']
    assert 'source: https://arxiv.org/pdf/2301.12345.pdf' in md_content
    assert 'original_pdf: archive/2301.12345.pdf' in md_content


def test_sms_pdf_with_extra_instructions(monkeypatch):
    """Extra text alongside a PDF link should be included in the agent prompt."""
    module = importlib.reload(importlib.import_module('prax.services.sms_service'))

    agent_prompts = []
    _setup_pdf_mocks(module, monkeypatch)

    def fake_reply(from_number, text):
        agent_prompts.append(text)
        return "resumen"

    def sync_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(module.conversation_service, 'reply', fake_reply)
    monkeypatch.setattr(module, 'send_sms', lambda msg, to: None)
    monkeypatch.setattr(threading.Thread, 'start', sync_thread_start)

    payload = {
        'From': '+10000000000',
        'MessageSid': 'SM1',
        'Body': 'summarize https://arxiv.org/abs/2301.12345 in Spanish',
        'NumMedia': '0',
    }

    module.sms_service.process(payload, 'https://ngrok.test')
    assert 'user also said' in agent_prompts[0].lower()


def test_derive_filename_arxiv():
    from prax.services.sms_service import _derive_filename
    assert _derive_filename("https://arxiv.org/pdf/2301.12345.pdf") == "2301.12345"
    assert _derive_filename("https://arxiv.org/pdf/2301.12345v2.pdf") == "2301.12345v2"


def test_derive_filename_generic():
    from prax.services.sms_service import _derive_filename
    assert _derive_filename("https://example.com/my-paper.pdf") == "my-paper"
    assert _derive_filename("https://example.com/") == "document"
