#!/usr/bin/env python
"""Ask Prax a single question through the FULL harness, from the command line.

This is the programmatic entry point for probing Prax outside of any channel
(Discord/SMS/TeamWork): it runs one prompt through the orchestrator — all tools,
spokes, memory, and sandbox — in an isolated eval workspace, and prints the
answer plus which tools/spokes it used and how many tokens it burned.

It's the maintained, general version of the ad-hoc probe scripts (e.g. the
praxbench Jacobian probe). Everything about the run — model, provider, which
tools are on, timeouts, rate-limiting — is controlled by environment variables,
so the same command reproduces a run exactly.

Usage:
    # prompt as an argument
    uv run python scripts/ask_prax.py "What is 2^10?"

    # prompt from a file or stdin (good for long / multi-line prompts)
    uv run python scripts/ask_prax.py --file question.txt
    echo "explain X" | uv run python scripts/ask_prax.py -

    # pick a model tier (low|medium|high|pro), show the tool/spoke trace
    uv run python scripts/ask_prax.py --tier medium --verbose "..."

See docs/guides/programmatic-usage.md for the full env-var reference and worked
examples (including running on a cheap OpenRouter model with the sandbox on).
"""
from __future__ import annotations

import argparse
import sys


def _read_prompt(args: argparse.Namespace) -> str:
    if args.file:
        return open(args.file, encoding="utf-8").read()
    if args.prompt in (None, "-"):
        return sys.stdin.read()
    return args.prompt


def main() -> int:
    p = argparse.ArgumentParser(description="Ask Prax one question through the full harness.")
    p.add_argument("prompt", nargs="?", help="The question. Omit or '-' to read stdin.")
    p.add_argument("--file", help="Read the prompt from this file instead.")
    p.add_argument("--tier", default="low", choices=["low", "medium", "high", "pro"],
                   help="Model tier (default low). The concrete model is set by "
                        "the *_MODEL env vars — see the guide.")
    p.add_argument("--case-id", default="ask", help="Label for the isolated run workspace.")
    p.add_argument("--verbose", action="store_true",
                   help="Also print the tools/spokes used and token count.")
    args = p.parse_args()

    prompt = _read_prompt(args).strip()
    if not prompt:
        p.error("no prompt (pass an argument, --file, or pipe stdin)")

    # Imported lazily so --help works without loading the whole app.
    from prax.eval.capability import orchestrator_executor

    run = orchestrator_executor(
        prompt, tier=args.tier, case_id=args.case_id, fold_artifacts=False)

    print(run.answer if getattr(run, "answer", None) else "(no answer)")
    if args.verbose:
        print("\n--- run ---", file=sys.stderr)
        print(f"tier={args.tier}  tokens={getattr(run, 'tokens', 0)}", file=sys.stderr)
        if getattr(run, "tools", None):
            print(f"tools: {', '.join(run.tools)}", file=sys.stderr)
        if getattr(run, "spokes", None):
            print(f"spokes: {', '.join(run.spokes)}", file=sys.stderr)
        if getattr(run, "error", ""):
            print(f"error: {run.error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
