"""LangChain tool wrappers for the Faculty (professor) system.

These are the tools the ``professor`` spoke uses to run adaptive, persona-led
courses: present the faculty roster, enroll the learner with a chosen
professor, teach one calibrated lesson at a time, record how it went, and
run opt-in pop quizzes over Discord/SMS.
"""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id
from prax.services import faculty_service


def _uid() -> str:
    return current_user_id.get() or "unknown"


@tool
def professor_list() -> str:
    """List the faculty — the roster of professor personas the learner can pick from.

    Each professor has a distinct character AND a distinct, research-grounded
    teaching method, so the learner can choose the teacher they'll click with.
    Present these to the learner and let them pick before enrolling.
    """
    roster = faculty_service.list_professors(_uid())
    lines = ["**Choose your professor** — each teaches in their own style:\n"]
    for p in roster:
        lines.append(
            f"{p.get('emoji', '🎓')} **{p['name']}** — `{p['id']}`\n"
            f"   _{p.get('tagline', '')}_\n"
            f"   Style: {p.get('pedagogy', '')}. Best for: {p.get('best_for', '')}."
        )
    lines.append(
        "\nAsk the learner which professor appeals to them (by name or id), then call "
        "`professor_enroll`. There's no wrong choice — they can switch later."
    )
    return "\n".join(lines)


@tool
def professor_enroll(subject: str, professor_id: str, title: str = "", goal: str = "") -> str:
    """Enroll the learner in a new adaptive course with a chosen professor.

    Creates the course as a Library learning space and drafts ONLY the first
    lesson (in the professor's voice). Later lessons are drafted one at a time,
    adapting to how the learner does. Pick the professor with the learner first
    via ``professor_list``.

    Args:
        subject: What to learn (e.g. "linear algebra", "Rust", "music theory").
        professor_id: The chosen professor's id (from professor_list).
        title: Optional custom course title.
        goal: Optional — the learner's specific goal or why they're learning it
            (helps the professor pitch the course right).
    """
    res = faculty_service.enroll(_uid(), subject, professor_id, title=title, goal=goal)
    if "error" in res:
        return res["error"]
    enr = res["enrollment"]
    prof = res["professor"]
    outline = res.get("outline", [])
    first = res.get("first_lesson") or {}
    outline_md = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(outline))
    return (
        f"{prof.get('emoji', '🎓')} **{prof['name']}** is now teaching **{enr['title']}** "
        f"(course id `{enr['slug']}`).\n\n"
        f"Planned lessons:\n{outline_md}\n\n"
        f"Lesson 1 — *{first.get('title', 'Lesson 1')}* — is drafted and waiting in the Library "
        f"(at the **{enr['target_level'].split('(')[0].strip()}** level). Only this first lesson is "
        f"written; the next one is drafted when the learner is ready, calibrated to how lesson 1 goes.\n\n"
        f"Present lesson 1 in {prof['name']}'s voice and point the learner to the Library space. "
        f"When they've worked through it, call `professor_record_progress` with how it went."
    )


@tool
def professor_next_lesson(course: str = "") -> str:
    """Draft and deliver the next lesson — calibrated to the learner's last result.

    Writes the lesson into the Library in the professor's voice at the current
    difficulty (raised if the last lesson was too easy, gentler/smaller if they
    struggled). Call this when the learner is ready to continue.

    Args:
        course: The course id (slug). Leave blank for the most recent active course.
    """
    res = faculty_service.next_lesson(_uid(), course)
    if "error" in res:
        return res["error"]
    if res.get("done"):
        return res.get("message", "Course complete.")
    prof = res.get("professor", {})
    return (
        f"Drafted **{res.get('lesson_title')}** at the "
        f"**{(res.get('target_level') or '').split('(')[0].strip()}** level "
        f"(saved to Library space `{res.get('space_slug')}`).\n\n"
        f"Teach it now as {prof.get('name', 'the professor')} — present it in their voice, "
        f"check understanding as you go, and point the learner to the full lesson in the Library. "
        f"When they're done, call `professor_record_progress`.\n\n"
        f"--- lesson body ---\n{res.get('body', '')}"
    )


@tool
def professor_record_progress(result: str, course: str = "", note: str = "") -> str:
    """Record how the learner did on the current lesson and recalibrate difficulty.

    This marks the current lesson complete (advancing the course) and adapts the
    NEXT lesson's difficulty: ``too_easy`` raises it, ``struggled`` makes the next
    lesson a smaller iteration with more scaffolding, ``ok`` holds steady.

    Args:
        result: One of "too_easy", "ok", or "struggled" — your honest read of how
            the learner handled the lesson (from their answers/questions/engagement).
        course: The course id (slug). Leave blank for the most recent active course.
        note: Optional private note about what clicked or confused them.
    """
    res = faculty_service.record_progress(_uid(), course, result, note=note)
    if "error" in res:
        return res["error"]
    if res.get("completed"):
        return (
            f"Recorded **{res['recorded']}**. That was the last lesson — the course is complete! "
            f"Congratulate the learner and offer a recap, a final challenge, or a new course."
        )
    return (
        f"Recorded **{res['recorded']}**. Next up: *{res.get('next_lesson_title')}* at the "
        f"**{(res.get('target_level') or '').split('(')[0].strip()}** level.\n"
        f"Calibration: {res['directive']}\n"
        f"When the learner's ready, call `professor_next_lesson` to draft it."
    )


@tool
def professor_pop_quiz(course: str = "") -> str:
    """Generate one short pop-quiz question (in the professor's voice) and arm it for grading.

    Returns the question to send to the learner. Their reply should be graded
    with ``professor_grade_quiz``. Used both on demand ("quiz me") and by the
    scheduled opt-in quiz job.

    Args:
        course: The course id (slug). Leave blank for the most recent active course.
    """
    res = faculty_service.pop_quiz(_uid(), course)
    if "error" in res:
        return res["error"]
    return res["question"]


@tool
def professor_grade_quiz(answer: str, course: str = "") -> str:
    """Grade the learner's reply to the pending pop quiz, in the professor's voice.

    Returns the professor's spoken feedback and updates the course's difficulty
    calibration. Call this when the learner answers a pop quiz.

    Args:
        answer: The learner's answer text.
        course: The course id (slug). Leave blank for the most recent active course.
    """
    res = faculty_service.grade_quiz(_uid(), course, answer)
    if "error" in res:
        return res["error"]
    return f"[{res['verdict']}] {res['feedback']}"


@tool
def professor_quiz_optin(
    course: str = "",
    enabled: bool = True,
    channel: str = "",
    frequency: str = "daily",
) -> str:
    """Opt the learner IN or OUT of proactive pop quizzes for a course (their choice).

    When enabled, the professor proactively texts one short question on a cadence
    over the chosen channel; replies are graded and feed the course's calibration.
    Only ever enable this when the learner has explicitly asked for it — it sends
    unprompted messages. Delivery only works to a channel the learner has linked.

    Args:
        course: The course id (slug). Leave blank for the most recent active course.
        enabled: True to turn quizzes on, False to turn them off.
        channel: "discord", "sms", or "" to auto-pick a linked channel.
        frequency: "daily", "weekdays", "twicedaily", or "weekly".
    """
    res = faculty_service.set_quiz_optin(
        _uid(), course, enabled=enabled, channel=channel, frequency=frequency,
    )
    if "error" in res:
        return res["error"]
    return res["message"]


@tool
def professor_status(course: str = "") -> str:
    """Show the learner's courses — professor, progress, difficulty, and quiz health.

    Use this to supervise the faculty: check that courses are progressing and
    that opt-in quiz schedules are alive (so you can tell if a professor has gone
    quiet and needs re-activating). Also good for "where was I?" recaps.

    Args:
        course: A course id (slug) for one course, or blank for all of them.
    """
    res = faculty_service.status(_uid(), course)
    if isinstance(res, dict) and res.get("error"):
        return res["error"]
    rows = [res] if isinstance(res, dict) else res
    if not rows:
        return "No courses yet. Offer `professor_list` so the learner can pick a professor."
    lines = []
    for c in rows:
        prog = c.get("progress", {})
        quiz = c.get("quiz", {})
        q = "off"
        if quiz.get("enabled"):
            health = quiz.get("schedule_health") or {}
            alive = "✓" if health.get("scheduled") else "⚠ schedule missing"
            q = f"on via {quiz.get('channel')} ({quiz.get('frequency')}) {alive}, next: {health.get('next_run', '?')}"
            if quiz.get("pending"):
                q += " — ⏳ awaiting an answer"
        lines.append(
            f"- **{c.get('title')}** (`{c.get('slug')}`) with {c.get('professor_name')} — "
            f"{c.get('status')}, {prog.get('done', 0)}/{prog.get('total', 0)} lessons, "
            f"level: {(c.get('target_level') or '').split('(')[0].strip()}, quizzes: {q}"
        )
    return "Courses:\n" + "\n".join(lines)


@tool
def professor_create(
    name: str,
    pedagogy: str = "",
    personality: str = "",
    teaching_style: str = "",
    voice: str = "",
    best_for: str = "",
    emoji: str = "🎓",
    tagline: str = "",
) -> str:
    """Create a CUSTOM professor persona the learner invented, added to their faculty.

    Use when the learner wants a teacher with specific traits ("a sarcastic
    physicist", "teach me like I'm five", "a professor who only uses sports
    analogies"). The persona then works exactly like a built-in professor.

    Args:
        name: The professor's name.
        pedagogy: Their core teaching method (e.g. "Socratic", "drill & recall").
        personality: Their character — what makes them distinctive and likable.
        teaching_style: How they actually run a lesson.
        voice: Their tone and speech patterns.
        best_for: Subjects/learners they suit.
        emoji: A face for them.
        tagline: A one-line hook.
    """
    prof = faculty_service.create_professor(
        _uid(), name, pedagogy=pedagogy, personality=personality,
        teaching_style=teaching_style, voice=voice, best_for=best_for,
        emoji=emoji, tagline=tagline,
    )
    return (
        f"{prof['emoji']} **{prof['name']}** (`{prof['id']}`) has joined the faculty. "
        f"Enroll with them via `professor_enroll(subject, '{prof['id']}')`."
    )


def build_faculty_hub_tools() -> list:
    """Tools for the FACULTY HUB agent (delegate_professor).

    The hub is administrative: it presents the roster, enrolls the learner with
    a chosen professor, supervises courses, and creates custom professors. The
    hub does NOT teach — it hands off to the in-character professor sub-agent
    (via the ``teach_as_professor`` tool defined in the professor spoke).
    """
    return [
        professor_list,
        professor_enroll,
        professor_status,
        professor_create,
    ]


def build_professor_session_tools() -> list:
    """Tools for the in-character PROFESSOR sub-agent (one teaching session).

    These are the actual teaching actions, performed by the chosen professor in
    character: deliver the next lesson, record how it went (recalibrating
    difficulty), run/grade pop quizzes, and manage opt-in quizzes. Plus a couple
    of read-only Library tools so the professor can pull the lesson content.
    """
    from prax.agent.library_tools import library_note_read, library_notes_list

    return [
        professor_next_lesson,
        professor_record_progress,
        professor_pop_quiz,
        professor_grade_quiz,
        professor_quiz_optin,
        library_note_read,
        library_notes_list,
    ]
