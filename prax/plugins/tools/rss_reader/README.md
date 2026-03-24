# RSS Feed Reader Plugin

Subscribe to RSS/Atom feeds, list subscriptions, and check for new items.

## Tools

### `rss_subscribe(url, name="")`
Subscribe to an RSS or Atom feed. Stores the subscription in the user's
workspace at `feeds.yaml`. If no name is provided, one is derived from the URL.

### `rss_unsubscribe(name)`
Remove a feed subscription by name.

### `rss_list()`
List all subscribed feeds with their last-checked timestamps.

### `rss_check(name="")`
Check one or all feeds for new items since the last check. Returns new items
with title, link, published date, and summary (truncated to 300 chars).
Previously seen items are deduplicated via stored URLs (capped at 200 per feed).

Requires: `feedparser` (note: must be added to pyproject.toml)
