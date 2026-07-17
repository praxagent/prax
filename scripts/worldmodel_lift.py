"""World-model lift experiment — does building an executable model of a problem
beat answering it directly? Runs both on real MATH-500 + GPQA and compares.

TJ's thesis: a general world-model capability should raise the OTHER benchmarks,
not just ARC. This measures it. Usage:
    OPENROUTER_API_KEY=... uv run python scripts/worldmodel_lift.py [n] [model]
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    model = sys.argv[2] if len(sys.argv) > 2 else "deepseek/deepseek-v4-flash"
    os.environ["PRAX_EVAL_FULL_DATASETS"] = "1"
    os.environ["PRAX_EVAL_DATASET_LIMIT"] = str(n)

    from langchain_openai import ChatOpenAI

    from prax.eval.benchmarks import get_adapter
    from prax.eval.stats import wilson_ci
    from prax.reasoning.worldmodel import world_model_solve
    from prax.settings import settings

    key = getattr(settings, "openrouter_api_key", None) or os.environ.get("OPENROUTER_API_KEY", "")
    llm = ChatOpenAI(model=model, api_key=key, base_url="https://openrouter.ai/api/v1",
                     temperature=0.2, timeout=120, max_retries=2)

    def complete(prompt: str) -> str:
        return llm.invoke(prompt).content or ""

    def problem_text(bench: str, case: dict) -> str:
        if bench == "math":
            return case["problem"]
        # gpqa: question + lettered choices
        return get_adapter("gpqa").prompt(case)

    def wm_response(answer) -> str:
        # Wrap so the adapter's answer-extractor can grab it either way.
        return f"FINAL ANSWER: {answer}\nThe answer is {answer}."

    for bench in ("math", "gpqa"):
        adapter = get_adapter(bench)
        cases = adapter.cases()
        d_pass = w_pass = 0
        for i, case in enumerate(cases):
            # Direct
            try:
                dresp = complete(adapter.prompt(case))
            except Exception as e:  # noqa: BLE001
                dresp = ""
                print(f"  [{bench} {i}] direct err: {e}", flush=True)
            d_ok = bool(adapter.score(case, dresp).get("passed"))
            # World-model
            try:
                wm = world_model_solve(problem_text(bench, case), complete,
                                       max_rounds=3, timeout=25)
                wresp = wm_response(wm.get("answer"))
                via = wm.get("via")
            except Exception as e:  # noqa: BLE001
                wresp, via = "", f"err:{e}"
            w_ok = bool(adapter.score(case, wresp).get("passed"))
            d_pass += d_ok
            w_pass += w_ok
            print(f"  [{bench} {i+1}/{len(cases)}] direct={'Y' if d_ok else 'n'} "
                  f"wm={'Y' if w_ok else 'n'} (via {via})", flush=True)

        n_ = len(cases)
        dl, dh = wilson_ci(d_pass, n_)
        wl, wh = wilson_ci(w_pass, n_)
        print(f"\n=== {bench.upper()} (n={n_}, {model}) ===")
        print(f"  DIRECT      : {d_pass}/{n_} = {100*d_pass/n_:.1f}% "
              f"(95% CI {100*dl:.1f}-{100*dh:.1f}%)")
        print(f"  WORLD-MODEL : {w_pass}/{n_} = {100*w_pass/n_:.1f}% "
              f"(95% CI {100*wl:.1f}-{100*wh:.1f}%)")
        print(f"  LIFT        : {100*(w_pass-d_pass)/n_:+.1f} points\n", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
