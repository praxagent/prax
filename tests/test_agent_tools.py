import importlib


def test_background_search_tool(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.tools'))

    called = {}

    async def fake_search(query, to_number=None, sms_bool=False):
        called['args'] = (query, to_number, sms_bool)
        return "search-result"

    monkeypatch.setattr('prax.agent.tools.background_search', fake_search)

    result = module.background_search_tool.invoke({"query": "find news"})
    assert result == "search-result"
    assert called['args'][0] == "find news"
    assert called['args'][1] is None
    assert called['args'][2] is False


def test_run_coro_safely_when_loop_running(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.tools'))

    monkeypatch.setattr(module.asyncio, 'get_running_loop', lambda: object())
    captured = {}

    def fake_run(coro):
        captured['value'] = coro
        return "thread-result"

    monkeypatch.setattr(module.asyncio, 'run', fake_run)

    result = module._run_coro_safely(lambda: "coro")
    assert result == "thread-result"
    assert captured['value'] == "coro"


# --- Reader tools migrated to plugins — patch the source reader modules ---


def test_npr_tool(monkeypatch):
    monkeypatch.setattr(
        'prax.readers.news.npr_top_hour.get_latest_npr_podcast',
        lambda: "https://npr.test",
    )
    module = importlib.reload(
        importlib.import_module('prax.plugins.tools.npr_podcast.plugin')
    )
    result = module.npr_podcast_tool.invoke({})
    assert result == "https://npr.test"


def test_npr_tool_none(monkeypatch):
    monkeypatch.setattr(
        'prax.readers.news.npr_top_hour.get_latest_npr_podcast',
        lambda: None,
    )
    module = importlib.reload(
        importlib.import_module('prax.plugins.tools.npr_podcast.plugin')
    )
    assert "Unable" in module.npr_podcast_tool.invoke({})


def test_web_summary_tool(monkeypatch):
    def fake_convert(url, user_id):
        assert user_id == 'agent'
        return ("https://audio", "summary text")

    monkeypatch.setattr(
        'prax.readers.web.web2mp3.convert_web_to_mp3',
        fake_convert,
    )
    module = importlib.reload(
        importlib.import_module('prax.plugins.tools.web_summary.plugin')
    )
    result = module.web_summary_tool.invoke({"url": "https://example.com"})
    assert "summary text" in result
    assert "https://audio" in result


def test_pdf_summary_tool(monkeypatch):
    monkeypatch.setattr(
        'prax.services.pdf_service.process_pdf_url',
        lambda url: "# Paper\n\nContent here.",
    )
    module = importlib.reload(
        importlib.import_module('prax.plugins.tools.pdf_reader.plugin')
    )
    result = module.pdf_summary_tool.invoke({"url": "https://arxiv.org/pdf/2301.12345.pdf"})
    assert "PDF Content:" in result
    assert "Content here." in result


def test_pdf_summary_tool_error(monkeypatch):
    def explode(url):
        raise RuntimeError("Java not found")

    monkeypatch.setattr(
        'prax.services.pdf_service.process_pdf_url',
        explode,
    )
    module = importlib.reload(
        importlib.import_module('prax.plugins.tools.pdf_reader.plugin')
    )
    result = module.pdf_summary_tool.invoke({"url": "https://example.com/broken.pdf"})
    assert "Failed to extract PDF" in result


def test_youtube_transcribe_tool(monkeypatch):
    monkeypatch.setattr(
        'prax.services.youtube_service.process_youtube_url',
        lambda url: {
            "title": "Test Video",
            "channel": "TestChannel",
            "duration_seconds": 125,
            "url": url,
            "transcript": "Hello world this is a test transcript.",
        },
    )
    module = importlib.reload(
        importlib.import_module('prax.plugins.tools.youtube_reader.plugin')
    )
    result = module.youtube_transcribe.invoke({"url": "https://www.youtube.com/watch?v=abc12345678"})
    assert "Test Video" in result
    assert "TestChannel" in result
    assert "2m 5s" in result
    assert "Hello world" in result


def test_youtube_transcribe_tool_error(monkeypatch):
    def explode(url):
        raise RuntimeError("yt-dlp not installed")

    monkeypatch.setattr(
        'prax.services.youtube_service.process_youtube_url',
        explode,
    )
    module = importlib.reload(
        importlib.import_module('prax.plugins.tools.youtube_reader.plugin')
    )
    result = module.youtube_transcribe.invoke({"url": "https://youtu.be/abc12345678"})
    assert "Failed to transcribe YouTube video" in result
