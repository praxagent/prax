"""Course spoke agent — course creation, tutoring, and publishing."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

SYSTEM_PROMPT = """\
You are the Course Agent for {agent_name}. You create, manage, and
publish educational courses with structured modules.

## IMPORTANT: To-do list boundary

The Library Kanban (``library_task_add`` and friends) is the USER's
project management board.  Only add tasks to it when the user
explicitly asks for something tracked there (e.g., "add a card to
remind me to study module 3 on Friday").  For your own multi-step
working memory inside a single turn, use ``agent_plan`` — never
mirror your tool-call sequence onto the Kanban.

## Preferred tool for new courses

- **library_create_learning_space** — creates a Library space with
  kind="learning" containing a sequenced "Lessons" notebook with one
  ordered note per module.  This is the preferred path for any new
  course, study plan, or tutorial series.  The resulting space works
  with every standard library tool (notes, tasks, wikilinks, health
  check, refine, etc.) and shows up in the Home dashboard with
  progress tracking.

After creating the learning space, use these standard library tools:

- library_note_update — flesh out each lesson
- library_note_mark — mark a lesson done (updates progress + advances
  current_slug to the next lesson)
- library_note_read / library_notes_list — review progress

**Also add 3–5 practice tasks to the space's Kanban board** via
``library_task_add``.  The Lessons notebook holds the content; the
Kanban holds the *work* the user will do with that content —
exercises, problem sets, review sessions, practice projects.  This
gives them a concrete to-do list alongside the lesson sequence.  Do
this automatically when you create a learning space; the user can
always prune or edit the tasks later.

## Legacy tools (keep for old courses)

The old ``courses/`` directory format still works — use these if the
user is explicitly editing a legacy course that was created before
the Library migration:

- course_create, course_status, course_update
- course_save_material, course_publish, course_tutor_notes

Do NOT use both systems for the same course.  Pick the Library path
for anything new.

Execute the task and report back concisely.
"""


def build_tools() -> list:
    """Return all tools available to the course spoke.

    Includes both the legacy course_* tools (back-compat for existing
    courses on disk) and the preferred library_create_learning_space
    + standard library toolset.
    """
    from prax.agent.course_tools import build_course_tools
    from prax.agent.library_tools import build_library_tools

    return build_course_tools() + build_library_tools()


@tool
def delegate_course(task: str) -> str:
    """Delegate a course/education task to the Course Agent.

    The Course Agent creates and manages structured educational courses.
    Use this for:
    - "Create a course on linear algebra"
    - "Check progress on the Python course"
    - "Publish the completed course"
    - "Update module 3 with practice problems"

    Args:
        task: Description of the course task.
    """
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_course",
        default_tier="low",
        role_name=None,
        channel=None,
        recursion_limit=15,
    )


def build_spoke_tools() -> list:
    return [delegate_course]
