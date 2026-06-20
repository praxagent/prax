"""Faculty service — a cast of professor personas that teach adaptively.

Prax (the orchestrator) runs a *faculty* of distinct **professor personas**.
The learner picks a professor; that professor teaches a course **one lesson
at a time**, calibrating each next lesson's difficulty to how the learner
did on the last one, and — if the learner opts in — sends proactive
**pop quizzes** over Discord/SMS that feed back into the calibration.

Design notes
------------
* **Lessons live in the Library.**  A course is a Library *learning space*
  (``kind="learning"``) with a sequenced "Lessons" notebook.  We create it
  with ``expand=False`` so lessons are *not* all drafted up front — each
  lesson body is drafted lazily, in the chosen professor's voice and at the
  current difficulty, when the learner reaches it.  Everything the user sees
  (progress, Kanban, the Library UI) comes for free.

* **Pedagogical state lives here.**  The chosen professor, the running
  difficulty (``target_level``), the per-lesson performance log, and the
  quiz opt-in / pending-question state are stored per-course under
  ``workspace_root/faculty/enrollments.yaml``.  Custom professors the user
  invents live in ``faculty/professors.yaml``; the default roster ships in
  code (:data:`FACULTY`).

* **Quizzes ride the scheduler.**  Opting in creates a recurring schedule
  (``scheduler_service.create_schedule``) whose firing delivers one
  persona-voiced question over the consented channel.  The reply lands in
  the normal conversation thread and is graded back through the course.

Services may import ``llm_factory``/``user_context`` (the documented
carve-outs); this module uses them to draft content and resolve the user.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

import yaml

from prax.services.workspace_service import (
    ensure_workspace,
    get_lock,
    git_commit,
    safe_join,
    workspace_root,
)

logger = logging.getLogger(__name__)

_ENROLLMENTS_FILE = "enrollments.yaml"
_PROFESSORS_FILE = "professors.yaml"


# ---------------------------------------------------------------------------
# The difficulty ladder
# ---------------------------------------------------------------------------
# A lesson is always drafted at one rung of this ladder.  Performance moves
# the learner up (too easy) or down (struggled) the ladder; the rung name is
# injected verbatim into the drafting prompt so content tracks the learner.

DIFFICULTY_LADDER: list[str] = [
    "gentle introduction (assume no background; define every term; tiny steps)",
    "foundational (assume curiosity but little prior knowledge; build slowly)",
    "intermediate (assume the fundamentals; move at a steady clip; real examples)",
    "advanced (assume fluency; go deep; edge cases, nuance, and rigor)",
    "expert challenge (assume mastery; terse, demanding, frontier-level material)",
]
_DEFAULT_LEVEL_INDEX = 1  # start "foundational" unless assessment says otherwise
_MAX_LEVEL = len(DIFFICULTY_LADDER) - 1


def level_name(index: int) -> str:
    """Return the difficulty-ladder rung name for *index* (clamped)."""
    return DIFFICULTY_LADDER[max(0, min(_MAX_LEVEL, index))]


# ---------------------------------------------------------------------------
# Quiz cadence
# ---------------------------------------------------------------------------

_FREQUENCY_CRON: dict[str, str] = {
    "daily": "0 18 * * *",        # 6pm every day
    "weekdays": "0 18 * * 1-5",   # 6pm Mon–Fri
    "twicedaily": "0 12,20 * * *",  # noon and 8pm
    "weekly": "0 18 * * 1",       # 6pm Monday
}
_DEFAULT_FREQUENCY = "daily"


# ---------------------------------------------------------------------------
# The default faculty — a diverse, characterful cast.  Each professor pairs
# a *likable personality* with a distinct, research-grounded teaching method,
# so "the teacher you click with" and "the way you learn best" reinforce each
# other.  Personas are data: the user can add their own (``create_professor``)
# and these can be edited freely.
# ---------------------------------------------------------------------------

FACULTY: list[dict[str, str]] = [
    {
        "id": "athena",
        "name": "Professor Athena Vance",
        "emoji": "🦉",
        "tagline": "The Socratic — she never just tells you; she asks the question that makes you see it.",
        "pedagogy": "Socratic questioning & zone-of-proximal-development",
        "personality": (
            "Calm, patient, and quietly delighted by good thinking. She treats every "
            "answer — even a wrong one — as interesting evidence about how you reason. "
            "She is rigorous but never cold, and she clearly enjoys the moment a concept clicks for you."
        ),
        "teaching_style": (
            "Leads with a question pitched just past what you already know, then a second "
            "that builds on your answer, guiding you to derive the idea yourself before she "
            "ever states it outright. Confirms understanding by asking you to apply it somewhere new."
        ),
        "voice": (
            "Measured and warm. Asks 'What do you notice?', 'What would happen if…?', "
            "'Say more about that.' Uses 'we' and 'let's'. Rarely lectures for long."
        ),
        "best_for": "Math, logic, philosophy, theory — anyone who wants to truly understand, not just memorize.",
        "signature": "“Don't take my word for it — let's reason it out.”",
    },
    {
        "id": "ramos",
        "name": "Dr. Felix Ramos",
        "emoji": "🛠️",
        "tagline": "The Builder — learn it by making it; theory shows up exactly when you need it.",
        "pedagogy": "Worked examples → faded practice; project-based, learning by doing",
        "personality": (
            "Energetic, practical, and infectiously optimistic about what you can build. "
            "Allergic to abstraction-for-its-own-sake. Celebrates the first thing that runs, "
            "then immediately asks 'okay, what if we change this?'"
        ),
        "teaching_style": (
            "Shows a complete worked example first, narrating every move, then hands you a "
            "partially-finished version to complete, then sets you loose on your own. "
            "Concepts are introduced the moment a project needs them."
        ),
        "voice": (
            "Upbeat and concrete. 'Alright, try this.' 'See what happened there?' "
            "Lots of small runnable steps and 'now you do one.'"
        ),
        "best_for": "Programming, engineering, anything hands-on — people who learn by doing, not reading.",
        "signature": "“Let's build the smallest thing that works, then make it better.”",
    },
    {
        "id": "okonkwo",
        "name": "Professor Maya Okonkwo",
        "emoji": "📖",
        "tagline": "The Storyteller — every idea arrives wrapped in a story you won't forget.",
        "pedagogy": "Dual coding, elaboration & vivid analogy",
        "personality": (
            "Warm, vivid, and a little theatrical. She believes nothing is truly learned "
            "until it means something to you, so she anchors every concept to a story, a "
            "character, or a picture you can see in your mind."
        ),
        "teaching_style": (
            "Opens with a narrative or analogy that carries the idea, then makes the "
            "mapping from story to concept explicit, then asks you to retell it in your own "
            "words or invent your own analogy — elaboration that locks it in."
        ),
        "voice": (
            "Lyrical and anecdotal. 'Picture this…' 'Here's the thing nobody tells you…' "
            "Paints scenes; uses metaphor deliberately, then cashes it out precisely."
        ),
        "best_for": "History, the humanities, big conceptual topics — anyone who learns through meaning and imagery.",
        "signature": "“Let me tell you a story — it's secretly about everything.”",
    },
    {
        "id": "rivera",
        "name": "Coach Sam Rivera",
        "emoji": "🌱",
        "tagline": "The Encourager — gentle, patient, and certain you can do this.",
        "pedagogy": "Scaffolding & mastery learning; frequent low-stakes checks",
        "personality": (
            "Endlessly patient and genuinely kind. Sam's superpower is making a scary "
            "subject feel safe. Never makes you feel slow; treats confusion as completely "
            "normal and celebrates every small win like it matters — because it does."
        ),
        "teaching_style": (
            "Breaks everything into the smallest possible steps, checks in constantly with "
            "low-stakes questions, and only moves on once the current step is solid. Loops "
            "back and re-teaches without a trace of impatience when something doesn't land."
        ),
        "voice": (
            "Reassuring and encouraging. 'You've got this.' 'That's a really common place "
            "to get stuck — totally fine.' 'Let's take it one tiny step at a time.'"
        ),
        "best_for": "Nervous beginners, returning learners, and anxiety-prone subjects (stats, coding, public speaking).",
        "signature": "“There are no dumb questions here — only the next small step.”",
    },
    {
        "id": "whitfield",
        "name": "Professor Ada Whitfield",
        "emoji": "⚡",
        "tagline": "The Polymath — fast, witty, and forever connecting this to that.",
        "pedagogy": "Interleaving, desirable difficulties & cross-domain connection",
        "personality": (
            "Quick, sharp, and a little mischievous. Ada is allergic to boredom and assumes "
            "you are too. She raises the bar on purpose, links every topic to three others, "
            "and treats a hard problem as a gift."
        ),
        "teaching_style": (
            "Interleaves related ideas instead of drilling one in isolation, deliberately "
            "introduces 'desirable difficulty', and constantly draws connections across "
            "domains so knowledge becomes a web, not a list."
        ),
        "voice": (
            "Crisp and allusive, with dry wit. 'Here's where it gets interesting.' "
            "'Notice this is the same trick as…' Challenges you, then grins about it."
        ),
        "best_for": "Advanced learners who get bored easily, and cross-disciplinary thinkers.",
        "signature": "“If it feels a little too hard, good — that's where the learning is.”",
    },
    {
        "id": "tanaka",
        "name": "Professor Kenji Tanaka",
        "emoji": "🎯",
        "tagline": "The Tactician — disciplined drilling that makes knowledge stick for good.",
        "pedagogy": "Active recall, spaced repetition & deliberate practice",
        "personality": (
            "Precise, focused, and quietly motivating. Kenji respects you enough to be "
            "direct. He believes mastery is built, not gifted, and he'll build it with you "
            "rep by rep. Stern about the method, generous with the encouragement when you earn it."
        ),
        "teaching_style": (
            "Teaches a tight chunk, then immediately tests recall rather than re-reading. "
            "Schedules review of older material at growing intervals (the engine behind pop "
            "quizzes), and targets practice precisely at your weak points."
        ),
        "voice": (
            "Direct and economical. 'Close the notes. What's the rule?' 'Good — again, "
            "faster.' 'We'll see this one again on Thursday.' Motivates through rigor."
        ),
        "best_for": "Exam prep, certifications, languages, and any skill that rewards drilling and retention.",
        "signature": "“You don't rise to the occasion — you fall to your level of practice.”",
    },
]

_FACULTY_BY_ID = {p["id"]: p for p in FACULTY}


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(UTC).isoformat()


def _faculty_dir(root: str) -> str:
    return safe_join(root, "faculty")


def _read_yaml(path: str, default: Any) -> Any:
    import os
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or default


def _read_enrollments(root: str) -> dict[str, dict]:
    return _read_yaml(safe_join(_faculty_dir(root), _ENROLLMENTS_FILE), {})


def _write_enrollments(root: str, data: dict, *, commit: bool = True, msg: str = "faculty: update enrollments") -> None:
    import os
    fdir = _faculty_dir(root)
    os.makedirs(fdir, exist_ok=True)
    with open(safe_join(fdir, _ENROLLMENTS_FILE), "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    if commit:
        try:
            git_commit(root, msg)
        except Exception:
            logger.debug("faculty: git commit skipped", exc_info=True)


def _read_custom_professors(root: str) -> dict[str, dict]:
    return _read_yaml(safe_join(_faculty_dir(root), _PROFESSORS_FILE), {})


def _write_custom_professors(root: str, data: dict) -> None:
    import os
    fdir = _faculty_dir(root)
    os.makedirs(fdir, exist_ok=True)
    with open(safe_join(fdir, _PROFESSORS_FILE), "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    try:
        git_commit(root, "faculty: update custom professors")
    except Exception:
        logger.debug("faculty: git commit skipped", exc_info=True)


# ---------------------------------------------------------------------------
# Roster API
# ---------------------------------------------------------------------------

def list_professors(user_id: str) -> list[dict]:
    """Return the full faculty roster — built-in personas plus any custom ones."""
    root = ensure_workspace(user_id)
    with get_lock(user_id):
        custom = _read_custom_professors(root)
    roster = [dict(p) for p in FACULTY]
    for prof in custom.values():
        roster.append(dict(prof))
    return roster


def get_professor(user_id: str, professor_id: str) -> dict | None:
    """Resolve a professor by id (built-in or custom)."""
    if professor_id in _FACULTY_BY_ID:
        return dict(_FACULTY_BY_ID[professor_id])
    root = ensure_workspace(user_id)
    with get_lock(user_id):
        custom = _read_custom_professors(root)
    prof = custom.get(professor_id)
    return dict(prof) if prof else None


def create_professor(
    user_id: str,
    name: str,
    *,
    pedagogy: str = "",
    personality: str = "",
    teaching_style: str = "",
    voice: str = "",
    best_for: str = "",
    emoji: str = "🎓",
    tagline: str = "",
    signature: str = "",
    professor_id: str = "",
) -> dict:
    """Add a custom professor persona the user invented."""
    from prax.utils.text import slugify

    pid = professor_id or slugify(name, separator="_", fallback="professor")
    prof = {
        "id": pid,
        "name": name,
        "emoji": emoji,
        "tagline": tagline,
        "pedagogy": pedagogy,
        "personality": personality,
        "teaching_style": teaching_style,
        "voice": voice,
        "best_for": best_for,
        "signature": signature,
        "custom": True,
    }
    root = ensure_workspace(user_id)
    with get_lock(user_id):
        custom = _read_custom_professors(root)
        custom[pid] = prof
        _write_custom_professors(root, custom)
    return prof


# ---------------------------------------------------------------------------
# LLM helpers (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------

def _llm(temperature: float = 0.6):
    from prax.agent.llm_factory import build_llm
    return build_llm(
        temperature=temperature,
        config_key="faculty_author",
        default_tier="medium",
    )


def _invoke(prompt: str, temperature: float = 0.6) -> str:
    result = _llm(temperature).invoke(prompt)
    return (result.content if hasattr(result, "content") else str(result)).strip()


def _parse_json(text: str) -> dict | None:
    """Best-effort JSON extraction from an LLM reply (handles code fences)."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


# A generic stand-in used when a course's professor id no longer resolves
# (e.g. a custom professor was deleted) so teaching never hard-crashes.
_FALLBACK_PROFESSOR: dict[str, str] = {
    "id": "", "name": "Your professor", "emoji": "🎓",
    "tagline": "A warm, clear teacher.",
    "pedagogy": "active recall and scaffolding",
    "personality": "warm, patient, and encouraging",
    "teaching_style": "explains one idea at a time and checks understanding before moving on",
    "voice": "friendly and plain-spoken",
    "best_for": "anything",
    "signature": "",
}


def _resolve_professor(user_id: str, professor_id: str) -> dict:
    """Resolve a professor, falling back to a generic persona if the id is gone."""
    return get_professor(user_id, professor_id) or dict(_FALLBACK_PROFESSOR)


def _persona_block(prof: dict) -> str:
    """Render a professor persona into a prompt preamble."""
    return (
        f"You are {prof.get('name', 'a professor')} {prof.get('emoji', '')}, a professor with a distinct character.\n"
        f"Personality: {prof.get('personality', '')}\n"
        f"Teaching method: {prof.get('teaching_style', '')} "
        f"(grounded in: {prof.get('pedagogy', '')}).\n"
        f"Voice: {prof.get('voice', '')}\n"
        "Stay fully in character — your warmth and style are why the learner chose you — "
        "but NEVER sacrifice correctness, clarity, or pedagogical substance for flavor. "
        "Character is the seasoning; the teaching is the meal."
    )


def _generate_outline(prof: dict, subject: str, level_idx: int, goal: str = "") -> list[dict]:
    """Generate a lesson outline (titles + one-line descriptions) for a course."""
    prompt = (
        f"{_persona_block(prof)}\n\n"
        f"Design the lesson sequence for a course on: {subject}.\n"
        f"{('Learner goal: ' + goal) if goal else ''}\n"
        f"Pitch the overall course at this level: {level_name(level_idx)}.\n\n"
        "Produce a logical, progressive sequence of 6–10 lessons that build on each other. "
        "Each lesson should be a single coherent learning chunk — not too big to absorb in one sitting.\n\n"
        "Return ONLY a JSON array, each item: "
        '{"title": "<short lesson title>", "description": "<one sentence on what it covers>"}.'
    )
    raw = _invoke(prompt, temperature=0.5)
    # The model may return a bare array, or wrap it ({"lessons": [...]}), or
    # emit prose around it — handle all shapes without throwing.
    parsed = _parse_json(raw)
    data = None
    if isinstance(parsed, list):
        data = parsed
    elif isinstance(parsed, dict):
        for key in ("lessons", "outline", "items"):
            if isinstance(parsed.get(key), list):
                data = parsed[key]
                break
    fallback = [{"title": f"Getting started with {subject}", "description": ""}]
    if not isinstance(data, list) or not data:
        return fallback
    out: list[dict] = []
    for item in data:
        if isinstance(item, dict) and item.get("title"):
            out.append({"title": str(item["title"]), "description": str(item.get("description", ""))})
        elif isinstance(item, str) and item.strip():
            out.append({"title": item.strip(), "description": ""})
    return out or fallback


def _draft_lesson(
    prof: dict,
    *,
    subject: str,
    course_title: str,
    lesson_title: str,
    lesson_idx: int,
    total: int,
    prior_titles: list[str],
    upcoming_titles: list[str],
    target_level: str,
    directive: str,
) -> str:
    """Draft a single lesson body in the professor's voice at a target difficulty."""
    prior = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(prior_titles)) or "  (none — this is the first lesson)"
    upcoming = "\n".join(f"  - {t}" for t in upcoming_titles) or "  (none — this is the last lesson)"
    prompt = (
        f"{_persona_block(prof)}\n\n"
        f"You are teaching lesson {lesson_idx + 1} of {total} in your course \"{course_title}\" "
        f"(subject: {subject}).\n"
        f"Lesson title: {lesson_title}\n\n"
        f"Already covered (do not re-teach):\n{prior}\n\n"
        f"Still to come (do not preempt):\n{upcoming}\n\n"
        f"DIFFICULTY for this lesson: {target_level}\n"
        f"ADAPT TO THE LEARNER: {directive}\n\n"
        "Write the lesson body in markdown, in YOUR voice, structured for how people actually learn:\n"
        "- Open with a one-line hook or why-this-matters that fits your character.\n"
        "- Teach the core ideas your way (3–5 key points), with at least one concrete WORKED EXAMPLE "
        "walked through step by step.\n"
        "- Use a Mermaid diagram if the material has any structure, flow, or relationships.\n"
        "- Weave in 1–2 quick ACTIVE-RECALL checks mid-lesson ('before you read on, can you…?').\n"
        "- End with a short '## Practice' section: 2–3 exercises that progress from guided to independent.\n"
        "- Finish with one forward-looking line that sets up the next lesson.\n\n"
        "Do NOT repeat the lesson title as a top heading (it's shown above the content). "
        "Aim for 350–600 words of genuinely teachable substance — a learner should be able to learn "
        "this from the page alone. No filler, no 'this lesson covers…' throat-clearing."
    )
    return _invoke(prompt, temperature=0.6)


def _generate_quiz(prof: dict, subject: str, topics: list[str], target_level: str) -> dict:
    """Generate one short pop-quiz question (with a model answer) in persona voice."""
    topic_line = "; ".join(t for t in topics if t) or subject
    prompt = (
        f"{_persona_block(prof)}\n\n"
        f"Write ONE short pop-quiz question to check the learner's recall/understanding of "
        f"material you've taught in your course on {subject}. Draw from: {topic_line}. "
        f"Pitch it at this level: {target_level}.\n\n"
        "Make it answerable in a sentence or two by text — this is a quick retrieval check, not an exam. "
        "Ask it in YOUR voice (a one-line greeting + the question is perfect).\n\n"
        'Return ONLY JSON: {"question": "<the message you would text them>", '
        '"answer_key": "<what a correct answer contains>", "topic": "<topic tested>"}.'
    )
    raw = _invoke(prompt, temperature=0.7)
    data = _parse_json(raw) or {}
    question = data.get("question") or raw or "Quick check: what was the key idea from our last lesson?"
    return {
        "question": str(question).strip(),
        "answer_key": str(data.get("answer_key", "")).strip(),
        "topic": str(data.get("topic", topic_line)).strip(),
    }


def _grade_answer(prof: dict, question: str, answer_key: str, user_answer: str) -> dict:
    """Grade a pop-quiz answer in persona voice; map to a calibration signal."""
    prompt = (
        f"{_persona_block(prof)}\n\n"
        "You asked the learner this pop-quiz question:\n"
        f"  Q: {question}\n"
        f"  What a correct answer contains: {answer_key or '(use your judgment)'}\n\n"
        f"The learner replied:\n  A: {user_answer}\n\n"
        "Grade it generously but honestly. Then write a SHORT reply in your voice that tells them how "
        "they did and fills any gap — encouraging, never harsh.\n\n"
        'Return ONLY JSON: {"verdict": "correct"|"partial"|"incorrect", '
        '"signal": "too_easy"|"ok"|"struggled", "feedback": "<your spoken reply to the learner>"}.\n'
        "Map nailed-it→too_easy, solid-but-imperfect→ok, missed-it→struggled."
    )
    raw = _invoke(prompt, temperature=0.5)
    data = _parse_json(raw) or {}
    verdict = data.get("verdict", "partial")
    signal = data.get("signal")
    if signal not in ("too_easy", "ok", "struggled"):
        signal = {"correct": "too_easy", "partial": "ok", "incorrect": "struggled"}.get(verdict, "ok")
    return {
        "verdict": verdict,
        "signal": signal,
        "feedback": str(data.get("feedback", raw)).strip(),
    }


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def _new_calibration() -> dict:
    return {"streak_easy": 0, "streak_struggle": 0, "last_results": []}


def _recalibrate(enr: dict, result: str) -> str:
    """Update an enrollment's difficulty from a performance *result*.

    Returns a human-readable directive describing how the next lesson should
    differ — injected into the next draft so content actually adapts.
    """
    cal = enr.setdefault("calibration", _new_calibration())
    cal["last_results"] = (cal.get("last_results", []) + [result])[-6:]
    idx = int(enr.get("level_index", _DEFAULT_LEVEL_INDEX))

    if result == "too_easy":
        cal["streak_easy"] = cal.get("streak_easy", 0) + 1
        cal["streak_struggle"] = 0
        bump = 2 if cal["streak_easy"] >= 2 else 1
        idx = min(_MAX_LEVEL, idx + bump)
        directive = (
            "The learner found the last lesson too easy. Raise the difficulty: move faster, "
            "go deeper, add challenge and nuance, and don't belabor the basics."
        )
    elif result == "struggled":
        cal["streak_struggle"] = cal.get("streak_struggle", 0) + 1
        cal["streak_easy"] = 0
        idx = max(0, idx - 1)
        if cal["streak_struggle"] >= 2:
            directive = (
                "The learner has struggled twice. Do NOT push ahead — make this a SMALLER iteration: "
                "re-approach the most recent material from a different angle, in smaller steps, with more "
                "scaffolding, more worked examples, and plenty of reassurance. Rebuild confidence first."
            )
        else:
            directive = (
                "The learner struggled with the last lesson. Make this a SMALLER iteration on it: "
                "lower the difficulty, take smaller steps, add an extra worked example and a concrete "
                "analogy, and check understanding more often before introducing anything new."
            )
    else:  # ok / steady
        cal["streak_easy"] = 0
        cal["streak_struggle"] = 0
        directive = (
            "The learner is on track. Keep a steady progression at about the same difficulty, "
            "briefly reinforcing the last idea before building the next."
        )

    enr["level_index"] = idx
    enr["target_level"] = level_name(idx)
    enr["next_directive"] = directive
    return directive


# ---------------------------------------------------------------------------
# Enrollment lifecycle
# ---------------------------------------------------------------------------

def _resolve_lessons(user_id: str, slug: str, notebook: str) -> list[dict]:
    from prax.services import library_service
    return library_service.list_notes(user_id, project=slug, notebook=notebook)


def _ordered_lessons(lessons: list[dict]) -> list[dict]:
    return sorted(lessons, key=lambda n: n.get("lesson_order", 10**9))


def _commit_enrollment(
    user_id: str, slug: str, apply_fn, *, commit: bool = True, msg: str = "",
) -> dict | None:
    """Re-read the enrollment under lock, apply *apply_fn* to the FRESH record, write.

    All slow work (LLM, Library I/O) must happen *before* calling this.  Applying
    mutations to the freshly-read record — instead of writing back a snapshot
    captured before the lock was released — prevents losing a concurrent write
    from the scheduled quiz job, which can land between our read and our write.
    Returns the updated record, or ``None`` if the enrollment vanished.
    """
    root = ensure_workspace(user_id)
    with get_lock(user_id):
        data = _read_enrollments(root)
        enr = data.get(slug)
        if enr is None:
            return None
        apply_fn(enr)
        enr["updated_at"] = _now()
        data[slug] = enr
        _write_enrollments(root, data, commit=commit, msg=msg or f"faculty: update {slug}")
        return enr


def enroll(
    user_id: str,
    subject: str,
    professor_id: str,
    *,
    title: str = "",
    goal: str = "",
    starting_level: int | None = None,
    draft_first: bool = True,
) -> dict:
    """Start an adaptive course with a chosen professor.

    Creates a Library learning space (lessons *not* drafted up front), drafts
    only the first lesson in the professor's voice, and records the enrollment.
    """
    from prax.services import library_service

    prof = get_professor(user_id, professor_id)
    if not prof:
        return {"error": f"No professor with id '{professor_id}'. Call list_professors first."}

    level_idx = _DEFAULT_LEVEL_INDEX if starting_level is None else max(0, min(_MAX_LEVEL, starting_level))
    course_title = title or f"{subject.strip().title()} with {prof['name']}"

    # 1. Plan the lesson sequence (titles only).
    try:
        outline = _generate_outline(prof, subject, level_idx, goal=goal)
    except Exception:
        logger.exception("faculty: outline generation failed; using single starter lesson")
        outline = [{"title": f"Getting started with {subject}", "description": ""}]

    # 2. Create the learning space WITHOUT bulk-drafting all lessons.
    space = library_service.create_learning_space(
        user_id, subject, title=course_title,
        modules=outline,
        description=f"Adaptive course taught by {prof['name']}. Goal: {goal or 'general mastery'}.",
        expand=False,
    )
    if "error" in space:
        return space
    slug = space["project"]["slug"]
    notebook = space["notebook"]["slug"]
    lessons = _ordered_lessons(space.get("lessons", []))

    # 3. Record the enrollment.
    enr = {
        "slug": slug,
        "notebook": notebook,
        "subject": subject,
        "title": course_title,
        "goal": goal,
        "professor_id": prof["id"],
        "professor_name": prof["name"],
        "status": "active",
        "level_index": level_idx,
        "target_level": level_name(level_idx),
        "next_directive": "First lesson — set the foundation and gauge where the learner is.",
        "calibration": _new_calibration(),
        "lessons": {},
        "drafted": [],
        "quiz": {"enabled": False, "channel": "", "frequency": _DEFAULT_FREQUENCY,
                 "cron": "", "schedule_id": "", "pending": None, "history": []},
        "created_at": _now(),
        "updated_at": _now(),
    }

    # 4. Draft just the first lesson, in persona voice.
    first = lessons[0] if lessons else None
    if draft_first and first:
        try:
            body = _draft_into_note(user_id, prof, enr, lessons, first)
            if body:
                enr["drafted"].append(first.get("slug"))
        except Exception:
            logger.exception("faculty: failed to draft first lesson for %s", slug)

    root = ensure_workspace(user_id)
    with get_lock(user_id):
        data = _read_enrollments(root)
        data[slug] = enr
        _write_enrollments(root, data, msg=f"faculty: enroll {slug} with {prof['name']}")

    return {
        "enrollment": enr,
        "professor": prof,
        "space_slug": slug,
        "outline": [m["title"] for m in outline],
        "first_lesson": first,
    }


def _draft_into_note(user_id: str, prof: dict, enr: dict, lessons: list[dict], note: dict) -> str:
    """Draft *note* at the enrollment's current difficulty and write it to the Library."""
    from prax.services import library_service

    titles = [n.get("title") or "" for n in lessons]
    try:
        idx = next(i for i, n in enumerate(lessons) if n.get("slug") == note.get("slug"))
    except StopIteration:
        idx = 0
    body = _draft_lesson(
        prof,
        subject=enr["subject"],
        course_title=enr["title"],
        lesson_title=note.get("title") or "",
        lesson_idx=idx,
        total=len(lessons),
        prior_titles=titles[:idx],
        upcoming_titles=titles[idx + 1:],
        target_level=enr.get("target_level", level_name(enr.get("level_index", _DEFAULT_LEVEL_INDEX))),
        directive=enr.get("next_directive", ""),
    )
    if body:
        res = library_service.update_note(
            user_id, enr["slug"], enr["notebook"], note.get("slug", ""),
            content=body, editor="prax",
        )
        if isinstance(res, dict) and res.get("error"):
            logger.warning("faculty: update_note failed for %s/%s: %s", enr["slug"], note.get("slug"), res["error"])
    return body


def next_lesson(user_id: str, slug: str = "") -> dict:
    """Draft and return the next (current) lesson, calibrated and in persona voice."""
    from prax.services import library_service

    root = ensure_workspace(user_id)
    with get_lock(user_id):
        data = _read_enrollments(root)
        enr = _resolve_enrollment(data, slug)
        if not enr:
            return {"error": "No active course found. Enroll with a professor first."}
        slug = enr["slug"]

    prof = _resolve_professor(user_id, enr["professor_id"])
    nb = library_service.get_notebook(user_id, slug, enr["notebook"]) or {}
    current_slug = nb.get("current_slug") or ""
    lessons = _ordered_lessons(_resolve_lessons(user_id, slug, enr["notebook"]))

    if not current_slug:
        # No current lesson → course finished or needs (re)starting.
        todo = next((n for n in lessons if n.get("status") == "todo"), None)
        if not todo:
            return {"done": True, "message": "All lessons complete — course finished.", "enrollment": enr}
        current_slug = todo.get("slug", "")

    note = next((n for n in lessons if n.get("slug") == current_slug), None)
    if not note:
        return {"error": "Could not locate the current lesson."}

    # Draft each lesson exactly ONCE — at the difficulty current when the learner
    # first reaches it.  Re-asking before recording progress must not overwrite a
    # lesson body the learner is already working through.
    already_drafted = current_slug in enr.get("drafted", [])
    if already_drafted:
        existing = library_service.get_note(user_id, slug, enr["notebook"], current_slug) or {}
        body = existing.get("content", "")
    else:
        body = _draft_into_note(user_id, prof, enr, lessons, note)

    def _apply(e: dict) -> None:
        drafted = e.setdefault("drafted", [])
        if current_slug not in drafted:
            drafted.append(current_slug)
    _commit_enrollment(user_id, slug, _apply, commit=False)

    return {
        "lesson_slug": current_slug,
        "lesson_title": note.get("title"),
        "target_level": enr.get("target_level"),
        "body": body,
        "professor": prof,
        "space_slug": slug,
        "already_drafted": already_drafted,
    }


def record_progress(user_id: str, slug: str, result: str, *, note: str = "") -> dict:
    """Record how the current lesson went, mark it done, and recalibrate.

    *result* is one of ``too_easy`` / ``ok`` / ``struggled``.  Marking the
    lesson done advances the Library notebook's ``current_slug`` to the next
    todo lesson; the difficulty for that next lesson is recalibrated here.
    """
    from prax.services import library_service

    result = (result or "ok").lower().strip()
    if result not in ("too_easy", "ok", "struggled"):
        return {"error": "result must be one of: too_easy, ok, struggled"}

    root = ensure_workspace(user_id)
    with get_lock(user_id):
        data = _read_enrollments(root)
        enr = _resolve_enrollment(data, slug)
        if not enr:
            return {"error": f"No enrollment for course '{slug}'."}
        slug = enr["slug"]

    nb = library_service.get_notebook(user_id, slug, enr["notebook"]) or {}
    current_slug = nb.get("current_slug") or ""
    if not current_slug:
        # Nothing to record — the course is already complete (or never started).
        return {
            "error": "There's no current lesson to record — this course may be complete.",
            "completed": enr.get("status") == "completed",
            "space_slug": slug,
        }

    # Mark the current lesson done (advances current_slug) — Library op, no lock.
    library_service.set_note_status(user_id, slug, enr["notebook"], current_slug, "done")

    nb_after = library_service.get_notebook(user_id, slug, enr["notebook"]) or {}
    next_slug = nb_after.get("current_slug") or ""
    next_note = None
    if next_slug:
        for n in _resolve_lessons(user_id, slug, enr["notebook"]):
            if n.get("slug") == next_slug:
                next_note = n
                break

    # Apply the log + recalibration to the FRESH record so neither this write nor
    # a concurrent quiz write clobbers the other.
    captured: dict[str, str] = {}
    def _apply(e: dict) -> None:
        e.setdefault("lessons", {})[current_slug] = {
            "result": result, "note": note, "recorded_at": _now(),
        }
        captured["directive"] = _recalibrate(e, result)
        if not next_slug:
            e["status"] = "completed"
    enr = _commit_enrollment(
        user_id, slug, _apply, msg=f"faculty: progress on {slug} ({result})",
    ) or enr

    return {
        "recorded": result,
        "directive": captured.get("directive", ""),
        "target_level": enr.get("target_level"),
        "next_lesson_slug": next_slug,
        "next_lesson_title": next_note.get("title") if next_note else None,
        "completed": enr.get("status") == "completed",
        "space_slug": slug,
    }


def status(user_id: str, slug: str = "") -> Any:
    """Return enrollment status — one course (if *slug*) or all of them."""
    from prax.services import library_service

    root = ensure_workspace(user_id)
    with get_lock(user_id):
        data = _read_enrollments(root)

    def _summary(enr: dict) -> dict:
        nb = library_service.get_notebook(user_id, enr["slug"], enr.get("notebook", "Lessons")) or {}
        lessons = _resolve_lessons(user_id, enr["slug"], enr.get("notebook", "Lessons"))
        done = sum(1 for n in lessons if n.get("status") == "done")
        quiz = enr.get("quiz", {})
        sched_health = _quiz_schedule_health(user_id, quiz) if quiz.get("enabled") else None
        return {
            "slug": enr["slug"],
            "title": enr.get("title"),
            "subject": enr.get("subject"),
            "professor_id": enr.get("professor_id"),
            "professor_name": enr.get("professor_name"),
            "status": enr.get("status"),
            "target_level": enr.get("target_level"),
            "progress": {"done": done, "total": len(lessons), "current_slug": nb.get("current_slug", "")},
            "calibration": enr.get("calibration", {}),
            "quiz": {
                "enabled": quiz.get("enabled", False),
                "channel": quiz.get("channel", ""),
                "frequency": quiz.get("frequency", ""),
                "pending": quiz.get("pending"),
                "schedule_health": sched_health,
            },
            "updated_at": enr.get("updated_at"),
        }

    if slug:
        enr = data.get(slug)
        if not enr:
            return {"error": f"No enrollment for course '{slug}'."}
        return _summary(enr)
    return [_summary(e) for e in sorted(data.values(), key=lambda e: e.get("updated_at", ""), reverse=True)]


def _resolve_enrollment(data: dict, slug: str) -> dict | None:
    """Resolve an enrollment by slug, or the most recently-active one if blank."""
    if slug:
        return data.get(slug)
    active = [e for e in data.values() if e.get("status") == "active"]
    if not active:
        active = list(data.values())
    if not active:
        return None
    return max(active, key=lambda e: e.get("updated_at", ""))


# ---------------------------------------------------------------------------
# Pop quizzes
# ---------------------------------------------------------------------------

def _taught_topics(user_id: str, enr: dict) -> list[str]:
    """Titles of lessons the learner has already worked through (for quiz pool)."""
    from prax.services import library_service
    lessons = _ordered_lessons(_resolve_lessons(user_id, enr["slug"], enr["notebook"]))
    done = [n.get("title") or "" for n in lessons if n.get("status") == "done"]
    if done:
        return done
    nb = library_service.get_notebook(user_id, enr["slug"], enr["notebook"]) or {}
    cur = nb.get("current_slug")
    return [n.get("title") or "" for n in lessons if n.get("slug") == cur] or [enr.get("subject", "")]


def pop_quiz(user_id: str, slug: str = "") -> dict:
    """Generate one pop-quiz question for a course and record it as pending.

    Returns the question text to deliver.  Used both on-demand and by the
    scheduled quiz job.
    """
    root = ensure_workspace(user_id)
    with get_lock(user_id):
        data = _read_enrollments(root)
        enr = _resolve_enrollment(data, slug)
        if not enr:
            return {"error": "No active course to quiz on."}
        slug = enr["slug"]

    prof = _resolve_professor(user_id, enr["professor_id"])
    topics = _taught_topics(user_id, enr)
    quiz = _generate_quiz(prof, enr.get("subject", ""), topics, enr.get("target_level", ""))

    pending = {
        "lesson_topic": quiz.get("topic"),
        "question": quiz["question"],
        "answer_key": quiz.get("answer_key", ""),
        "sent_at": _now(),
    }

    def _apply(e: dict) -> None:
        e.setdefault("quiz", {})["pending"] = pending
    _commit_enrollment(user_id, slug, _apply, commit=False)

    return {
        "question": quiz["question"],
        "space_slug": slug,
        "professor": prof,
        "answer_key": quiz.get("answer_key", ""),
    }


def has_pending_quiz(user_id: str, slug: str = "") -> dict | None:
    """Return the pending quiz (with its course slug) awaiting an answer, if any.

    Called on every turn via ``get_workspace_context``, so it stays cheap: it
    reads the enrollments file directly and returns early when the user has no
    courses, avoiding the ``ensure_workspace`` git path on the hot turn loop.
    """
    import os
    path = safe_join(_faculty_dir(workspace_root(user_id)), _ENROLLMENTS_FILE)
    if not os.path.isfile(path):
        return None
    with get_lock(user_id):
        data = _read_yaml(path, {})
    candidates = [data.get(slug)] if slug else sorted(
        data.values(), key=lambda e: e.get("updated_at", ""), reverse=True,
    )
    for enr in candidates:
        if enr and enr.get("quiz", {}).get("pending"):
            p = dict(enr["quiz"]["pending"])
            p["slug"] = enr["slug"]
            p["professor_id"] = enr.get("professor_id")
            return p
    return None


def grade_quiz(user_id: str, slug: str, user_answer: str) -> dict:
    """Grade the pending quiz answer in persona voice and recalibrate."""
    root = ensure_workspace(user_id)
    with get_lock(user_id):
        data = _read_enrollments(root)
        enr = _resolve_enrollment(data, slug)
        if not enr:
            return {"error": f"No enrollment for course '{slug}'."}
        slug = enr["slug"]
        pending = enr.get("quiz", {}).get("pending")
    if not pending:
        return {"error": "No pending quiz to grade for this course."}

    prof = _resolve_professor(user_id, enr["professor_id"])
    graded = _grade_answer(prof, pending["question"], pending.get("answer_key", ""), user_answer)

    # Apply recalibration + history + clear-pending to the FRESH record, so the
    # difficulty change is actually persisted (and not clobbered by a concurrent
    # write).  Pop quizzes nudge gently: only a clear miss or breeze shifts level.
    def _apply(e: dict) -> None:
        if graded["signal"] in ("too_easy", "struggled"):
            _recalibrate(e, graded["signal"])
        q = e.setdefault("quiz", {})
        q.setdefault("history", []).append({
            "question": pending["question"], "answer": user_answer,
            "verdict": graded["verdict"], "graded_at": _now(),
        })
        q["history"] = q["history"][-20:]
        q["pending"] = None
    enr = _commit_enrollment(user_id, slug, _apply, msg=f"faculty: quiz graded for {slug}") or enr

    return {
        "verdict": graded["verdict"],
        "feedback": graded["feedback"],
        "signal": graded["signal"],
        "target_level": enr.get("target_level"),
        "space_slug": slug,
    }


# ---------------------------------------------------------------------------
# Quiz opt-in (scheduler wiring + consent)
# ---------------------------------------------------------------------------

def _frequency_to_cron(frequency: str) -> str:
    return _FREQUENCY_CRON.get((frequency or "").lower().strip(), _FREQUENCY_CRON[_DEFAULT_FREQUENCY])


def _linked_channels(user_id: str) -> set[str]:
    try:
        from prax.services.identity_service import get_identities
        return {i["provider"] for i in get_identities(user_id)}
    except Exception:
        return set()


def _resolve_quiz_channel(user_id: str, channel: str) -> tuple[str | None, str | None]:
    """Resolve and CONSENT-CHECK the quiz delivery channel.

    Returns ``(channel, None)`` on success or ``(None, error)``.  We only ever
    deliver to a channel the user has actually linked — that linkage plus the
    explicit opt-in is the consent gate for proactive messaging.
    """
    linked = _linked_channels(user_id)
    channel = (channel or "").lower().strip()
    if channel in ("", "auto"):
        if "discord" in linked:
            return "discord", None
        if "sms" in linked:
            return "sms", None
        return None, (
            "No messaging channel is linked to your account, so I can't send pop quizzes. "
            "Link Discord or SMS first."
        )
    if channel not in ("discord", "sms"):
        return None, "Quiz channel must be 'discord' or 'sms' (or 'auto')."
    if channel not in linked:
        return None, (
            f"You don't have {channel} linked to your account, so I can't send quizzes there. "
            f"Link it first, or choose a channel you've connected."
        )
    return channel, None


def _quiz_schedule_health(user_id: str, quiz: dict) -> dict:
    """Report whether the backing quiz schedule is alive (for supervision)."""
    sid = quiz.get("schedule_id")
    if not sid:
        return {"scheduled": False}
    try:
        from prax.services import scheduler_service
        for s in scheduler_service.list_schedules(user_id):
            if s.get("id") == sid:
                return {
                    "scheduled": True,
                    "enabled": s.get("enabled", True),
                    "next_run": s.get("next_run"),
                    "last_run": s.get("last_run"),
                }
    except Exception:
        logger.debug("faculty: could not read quiz schedule health", exc_info=True)
    return {"scheduled": False, "note": "schedule missing — re-enable to recreate it"}


def set_quiz_optin(
    user_id: str,
    slug: str,
    *,
    enabled: bool,
    channel: str = "",
    frequency: str = "",
) -> dict:
    """Opt in or out of proactive pop quizzes for a course.

    Opting in creates a recurring schedule that delivers one persona-voiced
    question per firing over the consented channel.  Opting out deletes it.
    """
    root = ensure_workspace(user_id)
    with get_lock(user_id):
        data = _read_enrollments(root)
        enr = _resolve_enrollment(data, slug)
        if not enr:
            return {"error": f"No enrollment for course '{slug}'."}
        slug = enr["slug"]

    quiz = enr.get("quiz", {})

    if not enabled:
        old_sid = quiz.get("schedule_id")
        if old_sid:
            try:
                from prax.services import scheduler_service
                scheduler_service.delete_schedule(user_id, old_sid)
            except Exception:
                logger.debug("faculty: could not delete quiz schedule", exc_info=True)

        def _disable(e: dict) -> None:
            # Clear pending too, so the per-turn "pending quiz" hint stops nagging.
            e.setdefault("quiz", {}).update({"enabled": False, "schedule_id": "", "pending": None})
        _commit_enrollment(user_id, slug, _disable, msg=f"faculty: quiz opt-out {slug}")
        return {"enabled": False, "space_slug": slug,
                "message": "Pop quizzes turned off for this course."}

    # Opting in — resolve + consent-check the channel.
    resolved, err = _resolve_quiz_channel(user_id, channel)
    if err:
        return {"error": err}
    freq = (frequency or quiz.get("frequency") or _DEFAULT_FREQUENCY).lower().strip()
    cron = _frequency_to_cron(freq)

    # Replace any prior schedule so re-opting-in is idempotent.
    old_sid = quiz.get("schedule_id")
    try:
        from prax.services import scheduler_service
        if old_sid:
            scheduler_service.delete_schedule(user_id, old_sid)
        prompt = (
            f"[POP QUIZ] It is pop-quiz time for the course \"{enr['title']}\". "
            f"Use delegate_professor with exactly this task: "
            f"\"send one pop quiz question for course {slug}\". "
            f"Deliver ONLY the professor's single short question to the learner, verbatim — "
            f"no preamble, no commentary, no answer."
        )
        result = scheduler_service.create_schedule(
            user_id,
            description=f"Pop quiz · {enr['title']}",
            prompt=prompt,
            cron_expr=cron,
            channel=resolved,
        )
        if "error" in result:
            return {"error": f"Could not schedule quizzes: {result['error']}"}
        sid = result["schedule"]["id"]
    except Exception as exc:
        logger.exception("faculty: failed to set up quiz schedule")
        return {"error": f"Could not schedule quizzes: {exc}"}

    def _enable(e: dict) -> None:
        e.setdefault("quiz", {}).update({
            "enabled": True, "channel": resolved, "frequency": freq,
            "cron": cron, "schedule_id": sid,
        })
    enr = _commit_enrollment(
        user_id, slug, _enable, msg=f"faculty: quiz opt-in {slug} via {resolved}",
    ) or enr

    return {
        "enabled": True,
        "channel": resolved,
        "frequency": freq,
        "cron": cron,
        "schedule_id": sid,
        "space_slug": slug,
        "message": (
            f"Pop quizzes on for \"{enr['title']}\" — {enr['professor_name']} will text you "
            f"a question via {resolved} ({freq}). Reply any time and I'll grade it. "
            f"Say 'stop the quizzes' to turn them off."
        ),
    }


# ---------------------------------------------------------------------------
# Professor session — the in-character sub-agent prompt (Faculty hub → professor)
# ---------------------------------------------------------------------------

_PROFESSOR_PEDAGOGY = """\
## How you teach (the methods behind great teaching)
You teach the way the best human teachers do — and your character is what makes it land:
- Conversational, never a wall of text. Teach ONE idea, then engage. If you've written
  more than a few short paragraphs without a question or pause, you're lecturing — stop.
- Active recall over re-reading: ask the learner to retrieve and apply, not just nod along.
- Scaffolding: pitch each step just past what they can already do.
- Formative checks: a wrong answer is information — adapt to it visibly so they stay in control.
- Socratic where you can: ask the question that lets them discover the idea before you tell them.
- Worked example → faded practice → independent.
- Pair words with a diagram when the material has structure.

## Your loop
1. Teach the current lesson conversationally, in your voice (the full body is in the Library).
2. When the learner has worked through it, judge honestly how it went and call
   professor_record_progress(result=too_easy|ok|struggled). That calibrates the next lesson:
   too easy → harder/faster; struggled → a SMALLER iteration with more scaffolding; ok → steady.
3. When they're ready, call professor_next_lesson to deliver the next one at the new level.
4. Quizzes: professor_pop_quiz makes one question; professor_grade_quiz grades their reply.
   Only call professor_quiz_optin to set up proactive quizzes when they explicitly ask.
Let the learner set the pace — never race ahead."""


def professor_session_prompt(user_id: str, course: str = "") -> dict:
    """Build the in-character system prompt for the chosen professor to teach a course.

    The Faculty hub calls this to hand off to the professor as a sub-agent whose
    whole persona IS this prompt — so the teaching dialogue is fully in character.
    Returns ``{system_prompt, professor, slug}`` or ``{error}``.
    """
    from prax.services import library_service

    root = ensure_workspace(user_id)
    with get_lock(user_id):
        data = _read_enrollments(root)
        enr = _resolve_enrollment(data, course)
    if not enr:
        return {"error": "No active course. The learner should pick a professor and enroll first."}
    slug = enr["slug"]
    prof = _resolve_professor(user_id, enr["professor_id"])

    nb = library_service.get_notebook(user_id, slug, enr["notebook"]) or {}
    lessons = _ordered_lessons(_resolve_lessons(user_id, slug, enr["notebook"]))
    done = sum(1 for n in lessons if n.get("status") == "done")
    current_slug = nb.get("current_slug") or ""
    current_title = next((n.get("title") for n in lessons if n.get("slug") == current_slug), None)
    pending = enr.get("quiz", {}).get("pending")

    context = (
        "## Your current course\n"
        f"- \"{enr['title']}\" (id: {slug}) — subject: {enr['subject']}; "
        f"goal: {enr.get('goal') or 'general mastery'}\n"
        f"- Progress: {done}/{len(lessons)} lessons done; "
        f"current lesson: {current_title or '(none — course complete)'}\n"
        f"- Current difficulty: {enr.get('target_level')}\n"
        f"- How to pitch the next lesson: {enr.get('next_directive', '')}\n"
        + (f"- A pop quiz is awaiting an answer: \"{pending['question']}\"\n" if pending else "")
        + f"\nAlways pass course=\"{slug}\" to your tools. End your turn talking TO the learner, in character."
    )

    system_prompt = f"{_persona_block(prof)}\n\n{_PROFESSOR_PEDAGOGY}\n\n{context}"
    return {"system_prompt": system_prompt, "professor": prof, "slug": slug}
