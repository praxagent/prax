"""EMBEDDING_BASE_URL: the 'openai' embedding path on a local server.

Before this setting, `_embed_openai` always constructed the SDK client with
its defaults — api.openai.com unless the process environment happened to carry
OPENAI_BASE_URL (pydantic settings do NOT export to os.environ, so whether a
local deployment worked depended on how the process was launched). These tests
pin the explicit contract: unset → prior behaviour bit-for-bit; set → the
client targets the local server and works keyless.
"""

from unittest.mock import MagicMock, patch

from prax.services.memory import embedder


def _client_kwargs(base_url, openai_key):
    """Run _embed_openai with patched settings; return the OpenAI() kwargs."""
    fake_client = MagicMock()
    fake_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1, 0.2])])
    with patch("openai.OpenAI", return_value=fake_client) as ctor, \
         patch.object(embedder.settings, "embedding_base_url", base_url, create=True), \
         patch.object(embedder.settings, "openai_key", openai_key, create=True):
        vectors = embedder._embed_openai(["hello"], "text-embedding-3-small")
    assert vectors == [[0.1, 0.2]]
    return ctor.call_args.kwargs


class TestEmbeddingBaseUrl:
    def test_unset_means_prior_behaviour(self):
        kwargs = _client_kwargs(base_url=None, openai_key="sk-real")
        assert kwargs["base_url"] is None          # SDK default: api.openai.com
        assert kwargs["api_key"] == "sk-real"

    def test_empty_string_is_treated_as_unset(self):
        kwargs = _client_kwargs(base_url="", openai_key="sk-real")
        assert kwargs["base_url"] is None

    def test_set_points_the_client_at_the_local_server(self):
        kwargs = _client_kwargs(base_url="http://localhost:8080/v1",
                                openai_key="sk-real")
        assert kwargs["base_url"] == "http://localhost:8080/v1"

    def test_local_server_needs_no_openai_key(self):
        """The keyless-local path: a placeholder key is supplied so the SDK
        constructor doesn't refuse — local servers accept anything."""
        kwargs = _client_kwargs(base_url="http://localhost:8080/v1",
                                openai_key=None)
        assert kwargs["api_key"] == "sk-local-no-key"

    def test_no_key_and_no_base_url_stays_none(self):
        """Without a local server we must NOT invent a key — the SDK's own
        missing-key error is the honest failure."""
        kwargs = _client_kwargs(base_url=None, openai_key=None)
        assert kwargs["api_key"] is None
