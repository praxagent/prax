"""LangChain tool wrappers for the course/tutor system."""
from __future__ import annotations

import json

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id
from prax.services import course_service


def _get_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        return "unknown"
    return uid


@tool
def course_create(subject: str, title: str = "") -> str:
    """Create a new course on a subject.  Starts in 'assessing' status.

    After creating the course, ask the user 3 diagnostic questions to gauge
    their level (beginner / intermediate / advanced), then update the course
    with the determined level and build the module plan.

    Args:
        subject: The subject area (e.g. "linear algebra", "Python", "music theory").
        title: Optional custom title.  Defaults to the subject.
    """
    try:
        data = course_service.create_course(_get_user_id(), subject, title)
        return (
            f"Course created: **{data['title']}** (id: `{data['id']}`)\n"
            f"Status: {data['status']}\n\n"
            f"Next step: ask the user 3 diagnostic questions to determine their level, "
            f"then call course_update to set the level and build the module plan."
        )
    except Exception as e:
        return f"Failed to create course: {e}"


@tool
def course_status(course_id: str = "") -> str:
    """Show course status.  If course_id is empty, lists all courses.

    Use this to review progress before a tutoring session, or to find
    a course_id when the user says "let's continue my course".

    Args:
        course_id: The course ID to inspect.  Leave empty to list all.
    """
    uid = _get_user_id()
    try:
        if not course_id:
            courses = course_service.list_courses(uid)
            if not courses:
                return "No courses yet.  Use course_create to start one."
            lines = []
            for c in courses:
                prog = c.get("progress", {})
                done = prog.get("modules_completed", 0)
                total = prog.get("total_modules", 0)
                pace = prog.get("pace", "normal")
                lines.append(
                    f"- **{c['title']}** (`{c['id']}`) — "
                    f"{c['status']}, level: {c.get('level') or 'TBD'}, "
                    f"progress: {done}/{total} modules, pace: {pace}"
                )
            return "Courses:\n" + "\n".join(lines)

        data = course_service.get_course(uid, course_id)
        plan = data.get("plan", {})
        modules = plan.get("modules", [])
        current = plan.get("current_module", 0)
        progress = data.get("progress", {})

        lines = [
            f"**{data['title']}** (`{data['id']}`)",
            f"Subject: {data['subject']}",
            f"Status: {data['status']}",
            f"Level: {data.get('level') or 'not assessed yet'}",
            f"Pace: {progress.get('pace', 'normal')}",
            f"Progress: {progress.get('modules_completed', 0)}/{progress.get('total_modules', 0)} modules",
            "",
        ]
        if modules:
            lines.append("**Modules:**")
            for m in modules:
                marker = ">>>" if m["number"] == current else "   "
                status_icon = {"completed": "[done]", "active": "[active]", "pending": "[pending]"}.get(
                    m.get("status", "pending"), ""
                )
                lines.append(f"{marker} {m['number']}. {m['title']} {status_icon}")
        return "\n".join(lines)
    except FileNotFoundError:
        return f"Course `{course_id}` not found."
    except Exception as e:
        return f"Error: {e}"


@tool
def course_update(course_id: str, updates_json: str) -> str:
    """Update course metadata — level, plan, progress, status, pace, assessment, etc.

    Pass a JSON string that will be deep-merged into the course data
    (stored as course.yaml on disk for readability).

    Common updates:
    - Set level after assessment:  {"level": "intermediate", "status": "active", "assessment": {"determined_level": "intermediate"}}
    - Set module plan:  {"plan": {"modules": [{"number": 1, "title": "...", "topics": [...], "status": "active"}, ...], "current_module": 1}, "progress": {"total_modules": 8}}
    - Advance to next module:  {"plan": {"current_module": 3, "modules": [...]}, "progress": {"modules_completed": 2}}
    - Adjust pace:  {"progress": {"pace": "fast"}}
    - Complete course:  {"status": "completed"}

    Args:
        course_id: The course to update.
        updates_json: JSON string of fields to merge into course.yaml.
    """
    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    try:
        data = course_service.update_course(_get_user_id(), course_id, updates)
        prog = data.get("progress", {})
        return (
            f"Course `{course_id}` updated.\n"
            f"Status: {data['status']}, Level: {data.get('level') or 'TBD'}, "
            f"Progress: {prog.get('modules_completed', 0)}/{prog.get('total_modules', 0)}, "
            f"Pace: {prog.get('pace', 'normal')}"
        )
    except FileNotFoundError:
        return f"Course `{course_id}` not found."
    except Exception as e:
        return f"Error updating course: {e}"


@tool
def course_tutor_notes(course_id: str, notes: str = "") -> str:
    """Read or update your private tutor notes for a course.

    These notes are for YOUR reference — observations about the student's
    strengths, weaknesses, misconceptions, what clicked, what confused them,
    and what to focus on next.  The user doesn't see these unless they ask.

    If notes is empty, reads the current notes.  If notes is provided, overwrites
    the notes with the new content (write the FULL notes, not a diff).

    Args:
        course_id: The course.
        notes: Full tutor notes content to save.  Leave empty to read.
    """
    uid = _get_user_id()
    try:
        if not notes:
            content = course_service.read_tutor_notes(uid, course_id)
            return content or "(No tutor notes yet for this course.)"
        course_service.save_tutor_notes(uid, course_id, notes)
        return f"Tutor notes updated for `{course_id}`."
    except FileNotFoundError:
        return f"Course `{course_id}` not found."
    except Exception as e:
        return f"Error: {e}"


@tool
def course_save_material(course_id: str, filename: str, content: str) -> str:
    """Save a course material file — quiz, lesson summary, cheat sheet, etc.

    Materials are stored in the course's materials/ folder and persist across
    sessions.  Use descriptive filenames like "module_3_quiz.md" or
    "week_1_summary.md".

    Args:
        course_id: The course this material belongs to.
        filename: Filename to save (e.g. "quiz_1.md").
        content: The file content (markdown recommended).
    """
    try:
        course_service.save_material(_get_user_id(), course_id, filename, content)
        return f"Saved `{filename}` to `{course_id}/materials/`."
    except FileNotFoundError:
        return f"Course `{course_id}` not found."
    except Exception as e:
        return f"Error: {e}"


@tool
def course_publish(course_id: str, public: bool = False) -> str:
    """Build a course as a Hugo static site, served by TeamWork.

    Generates Hugo markdown from ALL courses and builds a single static site.
    Each course is a section with its own index and module pages.

    Args:
        course_id: The course to publish (triggers a full site rebuild).
        public: When True, also register the course in the user's share
            registry so it's reachable over the public ngrok URL.  Default
            False — only the local TeamWork URL is returned, which the user
            reaches via localhost / Tailscale / SSH tunnel.  Only set True
            when the user has explicitly asked to share the course
            publicly.
    """
    from prax.services import share_registry
    from prax.settings import settings

    uid = _get_user_id()
    teamwork_url = settings.teamwork_base_url.rstrip("/")

    try:
        result = course_service.build_course_site(uid, course_id, teamwork_url)
        if "error" in result:
            return f"Hugo build failed: {result['error']}"
    except FileNotFoundError:
        return f"Course `{course_id}` not found."
    except Exception as e:
        return f"Error publishing course: {e}"

    private_url = f"{teamwork_url}/courses/{course_id}/"
    if not public:
        return (
            f"Course built locally.\n"
            f"Local URL: {private_url}\n"
            f"This is reachable on your network (or via Tailscale / SSH tunnel) "
            f"but not publicly. To share it on the open internet, ask me to "
            f"publish it publicly."
        )

    entry = share_registry.register_course(uid, course_id)
    public_url = share_registry.public_url_for(entry)
    if not public_url:
        return (
            f"Course built and registered as public (token `{entry['token']}`),\n"
            f"but NGROK_URL is not configured so the public link isn't reachable.\n"
            f"Local URL: {private_url}"
        )
    return (
        f"Course published publicly.\n"
        f"Public URL: {public_url}\n"
        f"Local URL:  {private_url}\n"
        f"Revoke with workspace_unshare_file using token `{entry['token']}`."
    )


def build_course_tools() -> list:
    """Return the list of course/tutor tools to register with the main agent."""
    return [
        course_create,
        course_status,
        course_update,
        course_tutor_notes,
        course_save_material,
        course_publish,
    ]
