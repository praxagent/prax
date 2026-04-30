"""Mermaid diagram validation for markdown writes and publishes.

Two tiers:

- :func:`validate_fast` — pure-Python heuristic. Sub-millisecond. Runs on
  every markdown save via ``workspace_tools._validate_syntax``. Catches the
  common breaks (missing/unknown diagram type, unbalanced brackets,
  malformed arrows).

- :func:`validate_render` — shells out to ``mmdc`` (mermaid-cli, installed
  in the sandbox). Renders each block to SVG; non-zero exit means the
  diagram won't render in the user's browser either. Slower (~1–2 s per
  block) so it runs at publish time, not save time.

When ``mmdc`` is not available (local dev without the sandbox), the render
tier returns an empty list with a debug log — the fast tier still runs.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)


_MERMAID_BLOCK_RE = re.compile(
    r"```mermaid\s*\n(.*?)```",
    re.DOTALL,
)

# Known top-level diagram-type keywords. The first non-empty, non-comment
# line of a mermaid block must start with one of these.
_VALID_DIAGRAM_TYPES = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "gantt",
    "pie",
    "journey",
    "requirementDiagram",
    "gitGraph",
    "mindmap",
    "timeline",
    "quadrantChart",
    "xychart-beta",
    "sankey-beta",
    "block-beta",
    "C4Context",
    "C4Container",
    "C4Component",
    "C4Dynamic",
    "C4Deployment",
    "info",
    "architecture-beta",
    "packet-beta",
    "kanban",
)


def extract_blocks(content: str) -> list[tuple[int, str]]:
    """Find all ```mermaid blocks. Returns ``[(line_number, code), ...]``."""
    blocks: list[tuple[int, str]] = []
    for m in _MERMAID_BLOCK_RE.finditer(content):
        line_no = content[: m.start()].count("\n") + 1
        blocks.append((line_no, m.group(1)))
    return blocks


def _heuristic_block_errors(code: str) -> list[str]:
    """Pure-Python checks against a single mermaid block body.

    Returns a list of human-readable error strings; empty list means OK.
    """
    errors: list[str] = []

    # Strip leading/trailing blank lines and comment-only lines (`%% ...`).
    lines = [ln for ln in code.splitlines() if ln.strip() and not ln.strip().startswith("%%")]
    if not lines:
        errors.append("empty diagram (no content after stripping comments)")
        return errors

    first = lines[0].strip()
    # The first token is the diagram-type keyword. Match against the known
    # list — accept any direction/orientation suffix (`flowchart TD`, etc.).
    first_token = first.split()[0] if first.split() else ""
    if not any(first_token == t or first_token.startswith(t) for t in _VALID_DIAGRAM_TYPES):
        errors.append(
            f"first line '{first[:60]}' does not start with a known mermaid diagram type "
            f"(expected one of: flowchart, graph, sequenceDiagram, classDiagram, …)"
        )

    # Bracket balance — ignore brackets inside double-quoted strings since
    # mermaid permits arbitrary chars inside quotes.
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = {v: k for k, v in pairs.items()}
    stack: list[str] = []
    in_quote = False
    for ch in code:
        if ch == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch in pairs:
            stack.append(ch)
        elif ch in closers:
            if not stack or stack[-1] != closers[ch]:
                errors.append(f"unbalanced bracket: unexpected '{ch}'")
                break
            stack.pop()
    if stack and not errors:
        errors.append(f"unbalanced bracket: '{stack[-1]}' was never closed")
    if in_quote:
        errors.append("unbalanced double quote")

    return errors


def validate_fast(content: str) -> list[str]:
    """Fast pure-Python validation for all mermaid blocks in *content*.

    Returns a list of error messages prefixed with the block's line number;
    empty list means everything looks OK.
    """
    out: list[str] = []
    for line_no, code in extract_blocks(content):
        for err in _heuristic_block_errors(code):
            out.append(f"mermaid block at line {line_no}: {err}")
    return out


def _mmdc_available() -> bool:
    """Return True if ``mmdc`` is callable via the configured shell."""
    try:
        from prax.utils.shell import which
        return which("mmdc")
    except Exception:
        return False


def _shared_tempdir() -> str:
    """Return a tempdir reachable from both app and sandbox."""
    from prax.utils.shell import shared_tempdir
    return shared_tempdir(prefix="mermaid_")


def validate_render(content: str, *, timeout: int = 30) -> list[str]:
    """Render each mermaid block via ``mmdc`` and report any failures.

    Returns a list of error messages; empty list means every block rendered
    successfully (or ``mmdc`` is unavailable, in which case we no-op).
    """
    blocks = extract_blocks(content)
    if not blocks:
        return []
    if not _mmdc_available():
        logger.debug("mmdc not available — skipping render-time mermaid validation")
        return []

    from prax.utils.shell import run_command

    out: list[str] = []
    workdir = _shared_tempdir()
    try:
        for idx, (line_no, code) in enumerate(blocks):
            in_path = os.path.join(workdir, f"block_{idx}.mmd")
            out_path = os.path.join(workdir, f"block_{idx}.svg")
            with open(in_path, "w", encoding="utf-8") as fh:
                fh.write(code)
            try:
                result = run_command(
                    ["mmdc", "-i", in_path, "-o", out_path, "-q"],
                    timeout=timeout,
                )
            except Exception as e:
                logger.debug("mmdc invocation failed for block at line %d: %s", line_no, e)
                continue
            if result.returncode != 0:
                stderr = (result.stderr or "").strip().splitlines()
                msg = stderr[-1] if stderr else f"mmdc exited {result.returncode}"
                out.append(f"mermaid block at line {line_no}: {msg[:300]}")
    finally:
        try:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass
    return out
