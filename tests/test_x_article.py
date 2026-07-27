"""A post that links an X Article should say what the Article is.

Fetching such a post produced "here is a t.co link" and nothing else. The API
was returning the Article's title all along, in an `article` object we neither
requested nor rendered — so the single piece of human-meaningful content in the
response was dropped, and the agent was left with a URL it then could not open.

The Article BODY is genuinely not available: `/i/article/<id>` is not a post id
(the API answers "Could not find post with id"), and v2 exposes no Article
endpoint. Verified against the live API through the credential proxy. That makes
the title the whole of what this route can give, which is exactly why throwing
it away mattered.
"""
from __future__ import annotations

from prax.services import url_reader as ur


def _post_linking_an_article() -> dict:
    # Shape taken from a real response.
    return {
        "id": "2081762065392541951",
        "text": "https://t.co/aivc7B4A7Z",
        "author_id": "u1",
        "created_at": "2026-07-27T15:22:08.000Z",
        "article": {"title": "22580: From GPT2 to Kimi3, Explained"},
    }


def _includes() -> dict:
    return {"users": [{"id": "u1", "name": "ali", "username": "waterloo_intern"}]}


def test_the_article_title_survives_into_the_rendered_post():
    md = ur._format_tweet_markdown(_post_linking_an_article(), _includes())
    assert "22580: From GPT2 to Kimi3, Explained" in md


def test_it_says_the_body_is_not_available_rather_than_implying_completeness():
    """An agent that thinks it has the article will summarise a title as if it
    were the piece. Saying what is missing is what stops that."""
    md = ur._format_tweet_markdown(_post_linking_an_article(), _includes())
    assert "title only" in md
    assert "does not expose Article body text" in md


def test_an_ordinary_post_is_unchanged():
    plain = {"id": "1", "text": "just a normal post", "author_id": "u1"}
    md = ur._format_tweet_markdown(plain, _includes())
    assert "Article" not in md
    assert "just a normal post" in md


def test_the_article_field_is_actually_requested():
    """Rendering it is useless if the API was never asked for it."""
    assert "article" in ur._TWEET_FIELDS
