"""Token → USD cost estimation for eval runs.

Real token counts come from ``prax.eval.telemetry`` (the actual usage of every LLM
call). This module turns those into a **dollar estimate** so a benchmark run can
report cost per benchmark — the point of the cheap-evals work (docs/guides/cheap-evals.md).

The estimate is only as good as the price table + the model attribution, so it is
always reported as an *estimate*:

- ``MODEL_PRICING`` holds approximate 2026 $/1M-token rates (input, output) for the
  models Prax actually runs evals on. Prices drift and vary by provider — treat
  them as ballpark.
- For any model NOT in the table (or to pin exact rates), set
  ``EVAL_COST_INPUT_PER_M`` / ``EVAL_COST_OUTPUT_PER_M`` — they override for the
  whole run, so the active eval model always has a price.
- If neither the table nor the env has a rate, ``estimate_cost`` returns ``None``
  (unknown — reported honestly, never a fabricated number).
"""
from __future__ import annotations

import os

# (input $/1M, output $/1M). Approximate, 2026 — override via env for exactness.
# Keys are matched exact first, then by suffix (so "openai/gpt-5.4-nano" matches
# "gpt-5.4-nano"), case-insensitive.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenRouter cheap-eval defaults
    "deepseek/deepseek-v4-flash": (0.09, 0.28),
    "deepseek/deepseek-chat": (0.14, 0.28),
    "deepseek/deepseek-v3": (0.14, 0.28),
    # OpenAI eval tiers (approximate)
    "gpt-5.4-nano": (0.05, 0.40),
    "gpt-5.4-mini": (0.15, 0.60),
    "gpt-5.5": (1.25, 10.0),
    # GPT-5.6 family — the 2026-07-30 reprice (Luna −80%, Terra −20%)
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    # OpenRouter stress-test floor for coding evals (live models-endpoint rate)
    "qwen/qwen3-coder-30b-a3b-instruct": (0.07, 0.27),
    # The LIVE ladder (moved to OpenRouter 2026-08-09). These three carried
    # ~100% of production traffic while every trace showed
    # cost_estimate_usd: null — the fail-closed unknown-rate semantics held
    # (unknown ≠ $0.00), but a table that lacks the models actually in
    # production reports "unknown" for everything, which is honest and useless.
    # Dated snapshots (-0731) resolve via the suffix match on the base slug.
    # Rates read from openrouter.ai/api/v1/models on 2026-08-30 — not recalled,
    # not estimated. When these drift, the fix is to re-read the endpoint.
    "deepseek/deepseek-v4-flash-0731": (0.065, 0.18),
    "z-ai/glm-5.2": (1.19, 3.74),
    "openai/gpt-5.6-sol-pro": (2.00, 10.00),
}


def _lookup_rates(model: str | None) -> tuple[float, float] | None:
    """Env override wins; then exact; then suffix match on the model slug."""
    env_in = os.environ.get("EVAL_COST_INPUT_PER_M")
    env_out = os.environ.get("EVAL_COST_OUTPUT_PER_M")
    if env_in is not None and env_out is not None:
        try:
            return (float(env_in), float(env_out))
        except ValueError:
            pass
    if not model:
        return None
    key = model.strip().lower()
    for name, rates in MODEL_PRICING.items():
        if name.lower() == key:
            return rates
    # Suffix match: "openai/gpt-5.4-nano" → "gpt-5.4-nano"; "…:free" → base.
    base = key.split("/")[-1].split(":")[0]
    for name, rates in MODEL_PRICING.items():
        if name.lower().split("/")[-1] == base:
            return rates
    return None


def estimate_cost(model: str | None, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimated USD for *prompt_tokens*/*completion_tokens* on *model*.

    Returns ``None`` when no rate is known (unknown ≠ zero). Rounded to 4 dp.
    """
    rates = _lookup_rates(model)
    if rates is None:
        return None
    in_rate, out_rate = rates
    cost = (int(prompt_tokens or 0) / 1_000_000) * in_rate + \
           (int(completion_tokens or 0) / 1_000_000) * out_rate
    return round(cost, 4)


def format_cost(cost: float | None) -> str:
    """Human string for a cost estimate ('$0.0123' or 'n/a' when unknown)."""
    return "n/a" if cost is None else f"${cost:.4f}"
