"""LangChain tool wrappers for the self-improving fine-tuning pipeline."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.services import finetune_service


@tool
def finetune_harvest() -> str:
    """Harvest training data from recent conversations.

    Scans the last 24 hours of conversation history for user corrections
    (e.g. "no, that's wrong", "try again") and creates ChatML-formatted
    training pairs.  Returns a count of examples found.
    """
    examples = finetune_service.harvest_corrections(since_hours=24)
    if not examples:
        return "No corrections found in recent conversations."
    path = finetune_service.save_training_data(examples)
    return f"Harvested {len(examples)} training example(s). Saved to {path}"


@tool
def finetune_start(data_path: str | None = None) -> str:
    """Start a LoRA fine-tuning job.

    If no data_path is provided, automatically harvests corrections from
    recent conversations.  The training runs as a background process.
    """
    result = finetune_service.start_training(data_path)
    if "error" in result:
        return f"Fine-tune error: {result['error']}"
    return (
        f"Fine-tuning started (adapter: {result['adapter_name']}, pid: {result['pid']}).\n"
        f"Training data: {result['data_path']}\n"
        f"Use finetune_status to check progress."
    )


@tool
def finetune_status() -> str:
    """Check the status of the current fine-tuning job."""
    status = finetune_service.get_training_status()
    state = status.get("state", "unknown")
    if state == "disabled":
        return "Fine-tuning is disabled (FINETUNE_ENABLED=false)."
    if state == "idle":
        return "No fine-tuning job running."
    if state == "running":
        step = status.get("step", "?")
        max_steps = status.get("max_steps", "?")
        return f"Training in progress: step {step}/{max_steps} ({status.get('adapter_name', '?')})"
    if state == "completed":
        return f"Training completed: {status.get('adapter_name', '?')} (rc={status.get('return_code', 0)})"
    return f"Training state: {state}. Details: {status}"


@tool
def finetune_verify(adapter_name: str) -> str:
    """Verify a fine-tuned adapter by running test prompts through it.

    The adapter must be loaded into vLLM first (via finetune_load).
    """
    result = finetune_service.verify_adapter(adapter_name)
    if "error" in result:
        return f"Verification error: {result['error']}"
    return (
        f"Verification {result['verdict'].upper()}: "
        f"{result['passed']} passed, {result['failed']} failed\n"
        + "\n".join(
            f"  {'PASS' if r['passed'] else 'FAIL'}: {r['prompt']}"
            for r in result["results"]
        )
    )


@tool
def finetune_load(adapter_name: str) -> str:
    """Load a LoRA adapter into vLLM for inference."""
    result = finetune_service.load_adapter(adapter_name)
    if "error" in result:
        return f"Load error: {result['error']}"
    return f"Adapter '{adapter_name}' loaded into vLLM. Use it as a model name in requests."


@tool
def finetune_promote(adapter_name: str) -> str:
    """Promote a verified adapter as the active default."""
    result = finetune_service.promote_adapter(adapter_name)
    if "error" in result:
        return f"Promote error: {result['error']}"
    return f"Adapter '{adapter_name}' is now the active default."


@tool
def finetune_rollback() -> str:
    """Roll back to the previous adapter if the current one is problematic."""
    result = finetune_service.rollback_adapter()
    if "error" in result:
        return f"Rollback error: {result['error']}"
    return f"Rolled back to '{result['active_adapter']}' (was: {result.get('previous_adapter', '?')})"


@tool
def finetune_list_adapters() -> str:
    """List all available LoRA adapters."""
    adapters = finetune_service.list_adapters()
    if not adapters:
        return "No adapters available."
    active = finetune_service.get_active_adapter()
    lines = []
    for a in adapters:
        marker = " [ACTIVE]" if a["name"] == active else ""
        lines.append(f"  {a['name']}{marker} — created {a.get('created_at', '?')}")
    return "Available adapters:\n" + "\n".join(lines)


def build_finetune_tools() -> list:
    """Return finetune tools only if FINETUNE_ENABLED is true."""
    from prax.settings import settings
    if not settings.finetune_enabled:
        return []
    return [
        finetune_harvest, finetune_start, finetune_status,
        finetune_verify, finetune_load, finetune_promote,
        finetune_rollback, finetune_list_adapters,
    ]
