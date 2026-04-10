"""Finetune spoke agent — LoRA fine-tuning pipeline management.

Prax delegates fine-tuning tasks here instead of keeping 8 finetune tools in
the main orchestrator.  The finetune agent manages the full lifecycle:
harvesting training data from corrections, running LoRA training, verifying
adapters, and promoting them to production.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the Fine-Tuning Agent for {agent_name}.  You manage the LoRA
fine-tuning pipeline that lets {agent_name} learn from user corrections.

## Available tools

### Data
- **finetune_harvest** — Scan last 24h of conversations for user corrections
  and create ChatML training pairs.

### Training
- **finetune_start** — Start a LoRA training job (background process).
  Optionally provide a data_path; otherwise auto-harvests.
- **finetune_status** — Check training progress (step N/M).

### Adapter management
- **finetune_verify** — Test an adapter with validation prompts.
- **finetune_load** — Load an adapter into vLLM for inference.
- **finetune_promote** — Set an adapter as the active default.
- **finetune_rollback** — Revert to the previous adapter.
- **finetune_list_adapters** — List all available adapters.

## Workflow
1. **Harvest** training data from recent corrections.
2. **Start** a training job.
3. **Monitor** with finetune_status until complete.
4. **Load** the new adapter into vLLM.
5. **Verify** with test prompts — check pass/fail.
6. **Promote** if verification passes, **rollback** if it doesn't.
7. **Report** the outcome to the orchestrator.

## Rules
- Always verify before promoting — never promote an untested adapter.
- If verification fails, report honestly and suggest rollback.
- Training runs in the background — check status, don't wait.
"""


# ---------------------------------------------------------------------------
# Tool assembly
# ---------------------------------------------------------------------------

def build_tools() -> list:
    """Return all tools available to the finetune spoke."""
    from prax.agent.finetune_tools import build_finetune_tools
    return build_finetune_tools()


# ---------------------------------------------------------------------------
# Delegation function
# ---------------------------------------------------------------------------

@tool
def delegate_finetune(task: str) -> str:
    """Delegate a fine-tuning task to the Fine-Tuning Agent.

    The Fine-Tuning Agent manages the LoRA training pipeline — harvesting
    corrections from conversations, training adapters, verifying them, and
    promoting to production.

    Use this for:
    - "Harvest recent corrections and start fine-tuning"
    - "Check the status of the current training job"
    - "Verify and promote the latest adapter"
    - "Roll back to the previous adapter"
    - "List all available adapters"

    Only works when FINETUNE_ENABLED=true.

    Args:
        task: Description of the fine-tuning task.
    """
    if not settings.finetune_enabled:
        return "Fine-tuning is disabled (FINETUNE_ENABLED=false)."

    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_finetune",
        role_name="Finetune Agent",
        channel=None,
        recursion_limit=30,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_finetune]
