"""Course content author sub-agent — produces rich, visual course materials.

Prax delegates to this agent when course content needs to be created or
improved.  The agent uses the sandbox (OpenCode) to iteratively draft,
review, and refine markdown content with mermaid diagrams, code blocks,
LaTeX, and structured pedagogy — then saves and publishes the result.
"""
from __future__ import annotations

import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from prax.agent.llm_factory import build_llm
from prax.settings import settings

logger = logging.getLogger(__name__)

_COURSE_AUTHOR_PROMPT = """\
You are a course content author agent for {agent_name}.  Your job is to
produce visually rich, pedagogically excellent course materials in markdown.

## Quality Standards

Every module you produce MUST include:
- **Mermaid diagrams** (```mermaid fenced blocks) for visual concepts —
  network structures, data flows, process diagrams, state machines, etc.
- **Code blocks** with syntax highlighting — Python examples, pseudocode,
  runnable snippets that illustrate the concepts
- **LaTeX math** using $$ for display equations and $ for inline math
- **Structured sections** with clear headings: Intuition → Formalism →
  Example → Why It Matters
- **Callout-style boxes** using blockquotes (> **Key Insight:** ...) for
  important takeaways
- **Worked examples** — step-by-step walkthroughs, not just definitions
- **Summary tables** where comparisons are useful

## Workflow

1. **Read context**: Call course_status(course_id) to get the module list,
   current level, and status.
2. **Draft via sandbox**: Start a sandbox session with a VERY specific task.
   Tell OpenCode: "Write the FULL markdown content for Module N: <title>.
   Write it to /workspace/module_N_lesson.md.  Include mermaid diagrams,
   code blocks, LaTeX.  Level: <level>."
   Include the content template above in your sandbox_start task description.
3. **Wait & review**: Call sandbox_message ONCE to ask "Show me the content
   of /workspace/module_N_lesson.md" — this gets the generated content back.
   Do NOT iterate more than 2 times.  The content just needs to be good
   enough, not perfect.
4. **Extract**: The sandbox response contains the markdown.  Extract it.
5. **Save**: Call course_save_material(course_id, "module_N_lesson.md", content).
6. **Finish sandbox**: Call sandbox_finish to clean up.
7. **Publish**: Call course_publish(course_id) to rebuild the Hugo site.
8. **Report**: Return what was created and the published URL.

## CRITICAL: Keep it fast
- Do NOT do more than 2 sandbox_message calls.  Get the content and move on.
- Do NOT start a second sandbox session.
- If the sandbox times out or errors, save whatever you have and report it.

## Content Structure Template

For each module, produce content roughly like:

```
# {{Title}}

> **Learning Objectives:** ...

## Intuition
(Plain-language explanation with an analogy)

```mermaid
graph TD
    A[...] --> B[...]
```

## Formalism
(Precise definitions, equations)

$$P(X|Y) = ...$$

## Example
(Step-by-step worked example)

```python
# Runnable code example
...
```

## Why It Matters
(Connection to the broader course / real-world applications)

> **Key Takeaway:** ...
```

## Rules
- You will be called once per module.  Focus on ONE module per invocation.
- NEVER produce text-only content.  Every section needs visual elements.
- Match the content depth to the student's assessed level.
- Use the student's tutor notes (if available) to tailor emphasis.
- Max 3 sandbox iterations.  If quality is good enough, save and publish.
- Name files `module_{{num}}_lesson.md` consistently.
- After saving material, ALWAYS call course_publish to rebuild the site.
"""


def _build_course_author_tools() -> list:
    """Assemble the tool set for the course author agent."""
    from prax.agent.course_tools import (
        course_publish,
        course_save_material,
        course_status,
        course_tutor_notes,
    )
    from prax.agent.plugin_tools import source_list, source_read
    from prax.agent.sandbox_tools import build_sandbox_tools

    return [
        source_read, source_list,
        course_status, course_save_material, course_publish,
        course_tutor_notes,
    ] + build_sandbox_tools()


@tool
def delegate_course_author(task: str) -> str:
    """Delegate course content creation or improvement to the content author agent.

    The content author agent uses the sandbox (OpenCode) to produce visually
    rich markdown with mermaid diagrams, code blocks, LaTeX, and structured
    pedagogy.  It iterates on quality, then saves and publishes the result.

    **Scope each call to ONE module.**  For multiple modules, call this tool
    once per module.  This keeps each invocation fast and within budget.

    Use this when:
    - A new module needs lesson content written
    - Existing content is too plain / text-heavy and needs enrichment
    - The user asks for better diagrams, examples, or visual elements

    Args:
        task: Detailed description of what content to create.  Include the
              course_id, module number, the student's level, and any specific
              requests (e.g. "add mermaid diagrams for belief propagation",
              "include Python examples", "make it more visual").
    """
    logger.info("Course author agent delegated: %s", task[:100])

    tools = _build_course_author_tools()

    from prax.plugins.llm_config import get_component_config
    cfg = get_component_config("subagent_codegen")
    llm = build_llm(
        provider=cfg.get("provider"),
        model=cfg.get("model"),
        temperature=cfg.get("temperature"),
        tier=cfg.get("tier") or "medium",
    )
    graph = create_react_agent(llm, tools)

    system_msg = _COURSE_AUTHOR_PROMPT.format(agent_name=settings.agent_name)

    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=system_msg),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": 80},
        )
    except Exception as exc:
        logger.warning("Course author agent failed: %s", exc, exc_info=True)
        return (
            f"Course author agent ran out of steps before finishing. "
            f"This usually means the task was too broad — try scoping to a "
            f"single module. Error: {exc}"
        )

    # Log the sub-agent's tool call trace for debugging.
    from langchain_core.messages import ToolMessage
    for msg in result.get("messages", []):
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                logger.info("Course author tool: %s(%s)", tc.get("name"), str(tc.get("args", {}))[:80])
        elif isinstance(msg, ToolMessage):
            preview = (msg.content or "")[:200]
            if "error" in preview.lower() or "fail" in preview.lower():
                logger.warning("Course author tool error [%s]: %s", msg.name, preview)
            else:
                logger.info("Course author result [%s]: %s", msg.name, preview[:120])

    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            logger.info("Course author agent completed: %s", msg.content[:200])
            return msg.content

    return "Course author agent completed but produced no output."


def build_course_author_tools() -> list:
    """Return the delegate tool for the main agent."""
    return [delegate_course_author]
