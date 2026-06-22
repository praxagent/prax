"""Selective system-prompt assembly.

The orchestrator's static system prompt is large (~30 sections).  Most turns
don't need the topic-specific capability sections (teaching, math, document
pipelines, coding collaboration…).  When ``settings.prompt_selectivity_enabled``
is on, :func:`select_sections` drops those optional sections when the request
shows no signal of needing them — shrinking the base prompt on simple turns
without touching the core behavioural sections (Soul, Truthfulness, How You
Work, Security, Memory, …).

This is intentionally conservative: only an allow-list of clearly
topic-specific sections is ever droppable, and a section is kept whenever ANY
of its trigger keywords appears in the request.  With the flag off, the prompt
is returned byte-for-byte unchanged.
"""
from __future__ import annotations

# Header-substring (lowercased) → trigger keywords that KEEP the section.
# Only clearly topic-specific capability sections appear here; everything else
# is always retained.
OPTIONAL_SECTIONS: dict[str, tuple[str, ...]] = {
    "plugins & system administration": (
        "plugin", "install", "package", "sysadmin", "system admin",
        "capability", "import a tool", "apt", "pip ",
    ),
    "document pipelines": (
        "pdf", "slide", "presentation", "deck", "document", "report",
        "publish", "brochure", "docx", "ebook", "whitepaper",
    ),
    "teaching — the faculty": (
        "teach", "lesson", "course", "curriculum", "student", "quiz",
        "flashcard", "study plan", "professor", "tutor", "syllabus",
    ),
    "math & latex": (
        "math", "equation", "latex", "integral", "derivative", "formula",
        "calculus", "algebra", "theorem", "proof", "matrix",
    ),
    "claude code collaboration": (
        "code", "repo", "git", "refactor", "bug", "implement", "function",
        "compile", "deploy", "program", "script", "sandbox", "codebase",
    ),
    "reading your own source code": (
        "your source", "your code", "your implementation", "how are you built",
        "your own code", "source code",
    ),
}


def _optional_key_for_header(header_line: str) -> str | None:
    """Return the OPTIONAL_SECTIONS key whose substring matches *header_line*."""
    h = header_line.lower()
    for key in OPTIONAL_SECTIONS:
        if key in h:
            return key
    return None


def select_sections(prompt: str, user_input: str) -> str:
    """Drop optional, topic-irrelevant ``## `` sections from *prompt*.

    A section is dropped only if its header is in :data:`OPTIONAL_SECTIONS`
    and NONE of its trigger keywords appear in *user_input*.  All other
    sections (and any preamble before the first ``## `` header) are kept.
    """
    lines = prompt.splitlines(keepends=True)
    query = user_input.lower()

    out: list[str] = []
    # Current block buffer + whether we're currently dropping it.
    block: list[str] = []
    dropping = False

    def _flush():
        if not dropping:
            out.extend(block)

    for line in lines:
        if line.startswith("## "):
            # Close the previous block.
            _flush()
            block = [line]
            key = _optional_key_for_header(line)
            if key is not None and not any(t in query for t in OPTIONAL_SECTIONS[key]):
                dropping = True
            else:
                dropping = False
        else:
            block.append(line)
    _flush()

    return "".join(out)
