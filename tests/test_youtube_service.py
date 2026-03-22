import importlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def yt_service():
    return importlib.reload(importlib.import_module("prax.services.youtube_service"))


class TestIsYoutubeUrl:
    def test_standard_url(self, yt_service):
        assert yt_service.is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_short_url(self, yt_service):
        assert yt_service.is_youtube_url("https://youtu.be/dQw4w9WgXcQ")

    def test_shorts_url(self, yt_service):
        assert yt_service.is_youtube_url("https://youtube.com/shorts/dQw4w9WgXcQ")

    def test_non_youtube(self, yt_service):
        assert not yt_service.is_youtube_url("https://vimeo.com/12345")

    def test_random_string(self, yt_service):
        assert not yt_service.is_youtube_url("not a url at all")


class TestDownloadAudio:
    def test_file_too_large(self, yt_service, tmp_path, monkeypatch):
        big_file = tmp_path / "big.mp3"
        big_file.write_bytes(b"x" * (26 * 1024 * 1024))

        fake_ydl_instance = MagicMock()
        fake_ydl_instance.extract_info.return_value = {
            "title": "Big Video",
            "uploader": "Channel",
            "duration": 9999,
            "webpage_url": "https://youtube.com/watch?v=abc",
        }
        fake_ydl_instance.__enter__ = MagicMock(return_value=fake_ydl_instance)
        fake_ydl_instance.__exit__ = MagicMock(return_value=False)

        fake_ydl_class = MagicMock(return_value=fake_ydl_instance)

        monkeypatch.setattr(
            "tempfile.mkstemp",
            lambda suffix="", prefix="": (0, str(big_file)),
        )
        monkeypatch.setattr("os.close", lambda fd: None)

        with patch.dict("sys.modules", {"yt_dlp": MagicMock(YoutubeDL=fake_ydl_class)}):
            mod = importlib.reload(importlib.import_module("prax.services.youtube_service"))
            with pytest.raises(ValueError, match="exceeding"):
                mod.download_audio("https://youtube.com/watch?v=abc")


class TestProcessYoutubeUrl:
    def test_full_pipeline(self, yt_service, monkeypatch):
        monkeypatch.setattr(yt_service, "download_audio", lambda url: (
            "/tmp/fake.mp3",
            {"title": "My Video", "channel": "Me", "duration_seconds": 60, "url": url},
        ))
        monkeypatch.setattr(yt_service, "transcribe_audio", lambda path: "Hello from transcript")
        monkeypatch.setattr("os.unlink", lambda p: None)

        result = yt_service.process_youtube_url("https://youtube.com/watch?v=test123test")
        assert result["title"] == "My Video"
        assert result["transcript"] == "Hello from transcript"

    def test_cleanup_on_transcribe_failure(self, yt_service, monkeypatch):
        monkeypatch.setattr(yt_service, "download_audio", lambda url: (
            "/tmp/fake.mp3",
            {"title": "Video", "channel": "Ch", "duration_seconds": 10, "url": url},
        ))
        monkeypatch.setattr(yt_service, "transcribe_audio", MagicMock(side_effect=RuntimeError("API down")))

        unlinked = []
        monkeypatch.setattr("os.unlink", lambda p: unlinked.append(p))

        with pytest.raises(RuntimeError, match="API down"):
            yt_service.process_youtube_url("https://youtube.com/watch?v=test123test")

        assert "/tmp/fake.mp3" in unlinked
