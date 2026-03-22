"""Render LaTeX snippets to Discord-friendly PNGs.

Pipeline: pdflatex → ImageMagick (trim + rasterize) → Pillow (dark background + border).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# White background, black text — clean and readable.
_BG_COLOR = "#FFFFFF"
_TEXT_COLOR = "000000"
_DENSITY = 440  # 2x oversample
_DOWNSAMPLE = 2
_BORDER_PX = 10

_TEMPLATE = r"""
\documentclass[preview,border=2pt]{{standalone}}
\usepackage[utf8]{{inputenc}}
\usepackage[T1]{{fontenc}}
\usepackage{{amsmath,amssymb,amsfonts,mathtools,bm}}
\usepackage{{xcolor}}
\definecolor{{fg}}{{HTML}}{{{text_color}}}
\color{{fg}}
\pagenumbering{{gobble}}
\begin{{document}}
{content}
\end{{document}}
"""


def _find_magick() -> str | None:
    """Find ImageMagick binary (v7 'magick' preferred, v6 'convert' fallback)."""
    for cmd in ("magick", "convert"):
        if shutil.which(cmd):
            return cmd
    return None


def render_latex_snippet(latex: str, output_path: str | None = None) -> str | None:
    """Render a LaTeX math snippet to a PNG file.

    Args:
        latex: LaTeX content (can be raw math or a full equation environment).
        output_path: Where to write the PNG. If None, writes to a temp file.

    Returns:
        Absolute path to the PNG, or None on failure.
    """
    magick = _find_magick()
    if not magick:
        logger.error("ImageMagick not found — cannot render LaTeX")
        return None

    # Wrap bare math in a displaymath environment if not already wrapped.
    stripped = latex.strip()
    if not any(stripped.startswith(p) for p in ("\\begin", "\\[", "$$", "\\(")):
        stripped = f"\\[{stripped}\\]"

    tex_source = _TEMPLATE.format(text_color=_TEXT_COLOR, content=stripped)

    tmpdir = tempfile.mkdtemp(prefix="latex-render-")
    try:
        tex_file = os.path.join(tmpdir, "snippet.tex")
        pdf_file = os.path.join(tmpdir, "snippet.pdf")
        png_raw = os.path.join(tmpdir, "snippet_raw.png")

        with open(tex_file, "w") as f:
            f.write(tex_source)

        # Step 1: pdflatex
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_file],
            capture_output=True, text=True, timeout=30,
        )
        if not os.path.isfile(pdf_file):
            logger.error("pdflatex failed:\n%s", result.stdout[-500:] if result.stdout else result.stderr[-500:])
            return None

        # Step 2: ImageMagick — trim whitespace, rasterize at high DPI
        magick_cmd = [magick, "-density", str(_DENSITY), "-quality", "100", pdf_file, "-trim", "+repage", png_raw]
        result = subprocess.run(magick_cmd, capture_output=True, text=True, timeout=30)
        if not os.path.isfile(png_raw):
            logger.error("ImageMagick failed: %s", result.stderr[:300])
            return None

        # Step 3: Pillow — add dark background + border, downsample for crisp text
        from PIL import Image

        img = Image.open(png_raw).convert("RGBA")

        # Downsample 2x for supersampled anti-aliasing
        if _DOWNSAMPLE > 1:
            new_size = (img.width // _DOWNSAMPLE, img.height // _DOWNSAMPLE)
            img = img.resize(new_size, Image.BICUBIC)

        # Create background with border
        bg_color = tuple(int(_BG_COLOR.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
        bordered = Image.new("RGBA", (img.width + _BORDER_PX * 2, img.height + _BORDER_PX * 2), bg_color)
        bordered.paste(img, (_BORDER_PX, _BORDER_PX), img)

        # Save final PNG
        if output_path is None:
            output_path = os.path.join(tmpdir, "output.png")
        bordered.save(output_path, "PNG")
        return output_path

    except subprocess.TimeoutExpired:
        logger.error("LaTeX render timed out")
        return None
    except Exception:
        logger.exception("LaTeX render failed")
        return None
    finally:
        # Clean up intermediate files but keep output if it's in tmpdir
        for f in ("snippet.tex", "snippet.pdf", "snippet.aux", "snippet.log", "snippet_raw.png"):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path):
                os.remove(path)
