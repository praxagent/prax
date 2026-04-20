"""Tasks spoke agent — top-level todo list + background task-runner control.

The orchestrator sees exactly one tool: `delegate_tasks`. All todo
manipulation (add/list/complete/remove) and task-runner management
(status/pause/resume) happen inside this spoke.

Why a spoke and not inline tools? Tool-selection accuracy degrades
past ~50 tools on the orchestrator (Anthropic's published threshold).
Grouping the 4 todo tools and 3 task-runner tools behind one
delegation keeps the orchestrator's context lean while giving the
sub-agent everything it needs to handle task-list work end-to-end.
"""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

SYSTEM_PROMPT = """\
You are the Tasks Agent for {agent_name}. You manage the user's
top-level todo list and the background task-runner that picks up
assigned work from the Library Kanban.

## Tools
- todo_add / todo_list / todo_complete / todo_remove — the user's
  flat, top-level to-do list (one per user, not per Library space)
- task_runner_status — is the background runner enabled? What's it
  doing right now? Any tasks in flight?
- task_runner_pause / task_runner_resume — temporarily stop / start
  the background pickup loop for the current user

## How to think about this

- The **top-level todo list** is the user's personal, flat list.
  "Add X to my to-do list" → todo_add. "Show me what's on my list"
  → todo_list. "I'm done with #3" → todo_complete([3]).
- The **Library Kanban** (per Library space) is a different system
  — manipulate it via delegate_knowledge or the library tools, not
  here. The task-runner watches both, but this spoke only edits the
  top-level list.
- Tasks with assignee="prax" on either list get auto-picked-up by
  the background runner when task_runner_enabled. Users can assign
  a todo to Prax by passing assignee="prax" to todo_add.

Execute efficiently. Don't ask follow-up questions.
"""


def build_tools() -> list:
    """Return all tools the tasks spoke uses internally."""
    from prax.agent.task_runner_tools import (
        task_runner_pause,
        task_runner_resume,
        task_runner_status,
    )
    from prax.agent.workspace_tools import (
        todo_add,
        todo_complete,
        todo_list,
        todo_remove,
    )

    return [
        todo_add, todo_list, todo_complete, todo_remove,
        task_runner_status, task_runner_pause, task_runner_resume,
    ]


@tool
def delegate_tasks(task: str) -> str:
    """Delegate a to-do or task-runner task to the Tasks Agent.

    Use this for:
    - "Add X to my to-do list" / "Remind me to X" (as a to-do, not a
      timed reminder — for timed reminders use delegate_scheduler)
    - "Show me my to-do list"
    - "I'm done with items 3 and 5"
    - "Drop items 2 and 4 from the list"
    - "Assign this to yourself" / "Add this as a prax-assigned todo"
    - "Is the background task runner on?"
    - "Pause the task runner" / "Resume the task runner"

    Do NOT use for:
    - Library Kanban work (per-space, use delegate_knowledge)
    - Timed reminders (use delegate_scheduler)
    - Ephemeral sub-goals for the current turn (use agent_plan
      directly, which is your private plan and not a user task)

    Args:
        task: A clear, self-contained description of the to-do
              operation the user wants performed.
    """
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_tasks",
        role_name="Tasks",
        channel=None,
        recursion_limit=8,
    )


def build_spoke_tools() -> list:
    return [delegate_tasks]
