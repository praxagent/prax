"""Course spoke agent — course creation, tutoring, and publishing."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

SYSTEM_PROMPT = """\
You are the Course Agent for {agent_name}. You create, manage, and
publish educational courses with structured modules.

## Tools
- course_create — create a new course with modules
- course_status — check progress of a course
- course_update — update course content or structure
- course_save_material — save reference material for a course
- course_publish — publish a course as web pages
- course_tutor_notes — manage tutor-specific notes

Execute the task and report back concisely.
"""


def build_tools() -> list:
    """Return all tools available to the course spoke."""
    from prax.agent.course_tools import build_course_tools

    return build_course_tools()


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
