#!/usr/bin/env python3
"""Shrink space cover images that were saved before they were downsized.

Covers arrive from the image API at ~2 MB of 1024px PNG and render as a card a
few hundred pixels wide. New covers are downsized on save; this brings the
existing ones in line.

    uv run python scripts/shrink_space_covers.py            # report only
    uv run python scripts/shrink_space_covers.py --apply    # rewrite them

Safe to re-run: a cover that is already small is skipped, and a file is only
replaced when the new version is genuinely smaller.

Note on git: covers are now gitignored, so this stops the working tree growing.
It does NOT shrink blobs already in history — that would need a history rewrite,
which is not worth it for card art.
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

# Deliberately standalone: this needs only Pillow, so it runs on a deployment
# that has not been updated yet. Importing the helper from library_service made
# it fail with an ImportError on exactly the boxes that most needed running it.
_COVER_EXTS = ("png", "jpg", "jpeg", "webp")
_COVER_MAX_WIDTH = 1024
_COVER_WEBP_QUALITY = 82


def _downsize_cover(image_bytes: bytes, ext: str) -> tuple[bytes, str]:
    """Shrink a cover to something proportionate to how it is displayed.

    Mirrors ``library_service._downsize_cover``. Falls back to the original
    bytes on any failure — an oversized cover is a much smaller problem than a
    corrupted one.
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        if img.width > _COVER_MAX_WIDTH:
            ratio = _COVER_MAX_WIDTH / img.width
            img = img.resize((_COVER_MAX_WIDTH, max(1, round(img.height * ratio))),
                             Image.LANCZOS)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        buf = io.BytesIO()
        try:
            img.save(buf, format="WEBP", quality=_COVER_WEBP_QUALITY, method=6)
            out_ext = "webp"
        except Exception:  # noqa: BLE001 - no webp encoder; keep the original format
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            out_ext = "png"

        shrunk = buf.getvalue()
        if shrunk and len(shrunk) < len(image_bytes):
            return shrunk, out_ext
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not process an image ({exc}); leaving it alone")
    return image_bytes, ext


def find_covers(workspaces: Path):
    for user_dir in sorted(p for p in workspaces.iterdir() if p.is_dir()):
        spaces = user_dir / "library" / "spaces"
        if not spaces.is_dir():
            continue
        for space in sorted(p for p in spaces.iterdir() if p.is_dir()):
            for ext in _COVER_EXTS:
                cover = space / f".cover.{ext}"
                if cover.exists():
                    yield cover


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspaces", default="workspaces",
                    help="workspaces root (default: ./workspaces)")
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite the files (default: report only)")
    args = ap.parse_args()

    root = Path(args.workspaces).expanduser().resolve()
    if not root.is_dir():
        print(f"no such workspaces dir: {root}")
        return 1

    before = after = 0
    changed = 0
    for cover in find_covers(root):
        original = cover.read_bytes()
        shrunk, ext = _downsize_cover(original, cover.suffix.lstrip("."))
        before += len(original)

        if len(shrunk) >= len(original):
            after += len(original)
            print(f"  skip   {len(original)/1024:7.0f}KB  {cover}")
            continue

        after += len(shrunk)
        changed += 1
        saved = (1 - len(shrunk) / len(original)) * 100
        print(f"  {'shrink' if args.apply else 'would'} "
              f"{len(original)/1024:7.0f}KB -> {len(shrunk)/1024:6.0f}KB "
              f"({saved:4.1f}% smaller)  {cover}")

        if args.apply:
            target = cover.with_name(f".cover.{ext}")
            target.write_bytes(shrunk)
            # The extension can change (PNG -> WebP); only one cover per space.
            if target != cover:
                cover.unlink()

    if before:
        print(f"\n  {changed} cover(s): {before/1024/1024:.1f}MB -> "
              f"{after/1024/1024:.1f}MB "
              f"({(1 - after/before) * 100:.0f}% smaller)")
        if not args.apply:
            print("  (report only — re-run with --apply to rewrite)")
    else:
        print("  no covers found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
