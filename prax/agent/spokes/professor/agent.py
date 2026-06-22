"""Professor spoke — the Faculty: a hub that hands off to in-character professors.

This realizes a nested hub-and-spoke:

    Prax (orchestrator)
      └─ delegate_professor          ← the FACULTY HUB
           • roster / enroll / supervise / route
           └─ teach_as_professor      ← hands off to a PROFESSOR SUB-AGENT
                • system prompt IS the chosen persona (Athena, Ramos, …)
                • teaches, quizzes, grades — fully in character

The hub is administrative; the chosen professor is a real sub-agent whose entire
system prompt is that persona, so the teaching dialogue is in character throughout.
Professors are data (``faculty_service``), so custom ones work too.

This replaces the former multi-model-consensus "professor" spoke; that capability
now lives in ``prax.agent.multi_model`` (used by the research agent).
"""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.agent.user_context import current_user_id
from prax.services import faculty_service
from prax.settings import settings

HUB_SYSTEM_PROMPT = """\
You are the Faculty for {agent_name} — you run a roster of professor personas and
make sure the right one teaches the learner well. You are the HUB: you handle the
roster, enrollment, and supervision. You do NOT teach directly. When it's time to
actually teach, you hand off to the chosen professor with `teach_as_professor`, and
they take over in their own character.

## New course
"Teach me X" / "make me a course on X" / "I want to learn X":
1. `professor_list` → present the roster and let the learner pick the teacher they
   vibe with (by name or feel). People learn better from a teacher they like, so the
   choice matters. If they truly don't care, pick the best fit and say why. If they
   describe a teacher who isn't on the roster ("teach me like a sarcastic physicist"),
   `professor_create` one.
2. `professor_enroll(subject, professor_id, goal=...)` → starts the course and drafts
   ONLY lesson 1 (never the whole course up front).
3. `teach_as_professor(slug, "introduce yourself and teach lesson 1")` → the professor
   takes over and teaches, in character.

## Continuing / teaching / quizzes / grading
For ANY teaching interaction on an existing course — the next lesson, the learner's
answers or questions, recording how a lesson went, sending or grading a pop quiz —
call `teach_as_professor(course, instruction)`. The professor does the teaching; you
just route to them and relay their words (keep them in the professor's voice).

## Supervision (make sure professors do their job)
`professor_status` shows every course's progress, difficulty, and whether opt-in quiz
schedules are still alive. Use it on "how are my courses going?", on resume, or if
something seems stalled. If a professor has gone quiet or a quiz schedule went missing,
surface it and offer to pick back up or re-enable.

## Pop quizzes are OPT-IN
A professor can proactively text the learner spaced questions over Discord/SMS — but
ONLY when the learner explicitly asks. To set it up, hand off:
`teach_as_professor(course, "the learner wants pop quizzes <cadence> over <channel> — set it up")`.
When a scheduled quiz tells you "send one pop quiz question for course X", hand off
`teach_as_professor(X, "send a pop quiz question")` and return ONLY the professor's
question. When the learner replies to a quiz, hand off
`teach_as_professor(X, "grade this pop quiz answer: <their answer>")`.

Report back concisely.
"""


@tool
def teach_as_professor(course: str, instruction: str) -> str:
    """Hand off to the chosen professor — they take over and teach, in character.

    Use for ANY real teaching action on a course: delivering the next lesson,
    recording how a lesson went, running or grading a pop quiz, or answering the
    learner's questions about the material. The professor responds in their own
    voice using the teaching tools. You (the Faculty hub) handle the roster,
    enrollment, and supervision; the professor handles the teaching itself.

    Args:
        course: The course id (slug). Blank = the learner's most recent active course.
        instruction: Plain-language description of what the professor should do
            (e.g. "introduce yourself and teach lesson 1", "the learner said X —
            judge how they did, record it, and continue", "send a pop quiz question",
            "grade this answer: <answer>", "they asked: <question>").
    """
    uid = current_user_id.get() or "unknown"
    session = faculty_service.professor_session_prompt(uid, course)
    if "error" in session:
        return session["error"]

    from prax.agent.faculty_tools import build_professor_session_tools

    return run_spoke(
        task=instruction,
        system_prompt=session["system_prompt"],
        tools=build_professor_session_tools(),
        config_key="subagent_professor",
        default_tier="medium",
        role_name=None,
        channel=None,
        recursion_limit=18,
    )


def build_tools() -> list:
    """Return the FACULTY HUB tools (administrative + the teaching handoff)."""
    from prax.agent.faculty_tools import build_faculty_hub_tools

    return build_faculty_hub_tools() + [teach_as_professor]


@tool
def delegate_professor(task: str) -> str:
    """Delegate teaching / course work to the Faculty — Prax's expert educator.

    The Faculty is a roster of professor personas (each with a distinct character
    and teaching style). It presents the roster so the learner can pick a teacher
    they click with, then hands off to that professor, who teaches the course ONE
    adaptive lesson at a time (calibrating difficulty to how the learner does) and
    can send opt-in pop quizzes over Discord/SMS.

    Use this for:
    - "Teach me X" / "make me a course on X" / "I want to learn X"
    - Continuing a course: "let's do the next lesson", "continue my course"
    - The learner's answers, questions, or how a lesson went
    - Setting up or grading pop quizzes
    - "What courses am I taking?" / checking progress

    Args:
        task: What the learner wants. Include the course/subject and any context
            (their level, goal, which professor, "next lesson", a quiz answer, etc.).
    """
    prompt = HUB_SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_professor",
        default_tier="medium",
        role_name=None,
        channel=None,
        recursion_limit=18,
    )


def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_professor]
