"""Shared text utilities."""
from __future__ import annotations

import re


def slugify(
    text: str,
    *,
    separator: str = "-",
    fallback: str = "item",
    max_length: int = 60,
) -> str:
    """Convert *text* to a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", separator, text.lower()).strip(separator)
    return slug[:max_length] or fallback
