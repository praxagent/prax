# Fetching social posts (X, Bluesky, Threads) via API

[← Guides](README.md)

Several social platforms block unauthenticated scraping, so the normal web reader
(Jina) and headless browser fail on their post URLs. Prax detects post links and
routes them through each platform's API where possible. All of this is a
**transparent upgrade to the single URL→markdown choke point**
(`prax/services/url_reader.py::fetch_markdown`) — so `fetch_url_content`,
note-from-URL, and SMS/Discord auto-capture all get it, with **no new tool** to
enable. Each path is **fail-safe**: on any miss it returns `None` and falls through
to the web reader, so nothing ever breaks.

## X / Twitter — `TWITTER_API` (required)

X locked scraping down hard, so the API is the only reliable route.

```dotenv
TWITTER_API=<X API v2 bearer token>
```

`x.com`/`twitter.com` `…/status/<id>` links → `GET api.twitter.com/2/tweets/{id}`
(`Authorization: Bearer $TWITTER_API`), returning author, date, full text
(including long "note" bodies), and like/repost/reply counts.

## Bluesky — no token needed ✅

Bluesky's AT-Protocol AppView is fully open, so this **works out of the box** with
no key. `bsky.app/profile/<handle-or-did>/post/<rkey>` links →

1. resolve handle → DID via `com.atproto.identity.resolveHandle` (skipped if the
   URL already contains a `did:`),
2. build the `at://<did>/app.bsky.feed.post/<rkey>` URI,
3. `GET public.api.bsky.app/xrpc/app.bsky.feed.getPosts?uris=<at-uri>`,

returning author, text, date, and like/repost/reply counts.

## Threads — `THREADS_API` (limited by Meta) ⚠️

```dotenv
THREADS_API=<Threads Graph API access token>
```

Honest limitation, straight from Meta's docs: the Threads API has **no oEmbed and no
URL→content endpoint**, and reading **third-party** public posts requires an app
granted **Advanced Access for `threads_basic`** — without it, only official Meta
accounts (`@meta`, `@threads`, …) and your own tester posts are retrievable. Prax
does its best anyway: it decodes the URL shortcode to a media id (Threads/Instagram
shortcodes are base64 of the numeric id) and calls
`GET graph.threads.net/v1.0/{media-id}?fields=text,username,permalink,timestamp`.
If Meta denies access (the common case for arbitrary posts), it **falls back to the
web reader** — which, since Threads is less locked-down than X, often still works.

## Code

- `prax/services/url_reader.py` — `fetch_tweet_via_api`, `fetch_bsky_via_api`,
  `fetch_threads_via_api` (+ the routing loop in `fetch_markdown`)
- `prax/settings.py` — `twitter_api` / `threads_api` (Bluesky needs none)
- Tests: `tests/test_url_reader_twitter.py`, `tests/test_url_reader_social.py` (keyless — APIs mocked)

After setting any token in `.env`, restart Prax so it picks up the value.
