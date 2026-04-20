#!/usr/bin/env python3
"""Architectural layer linter for Prax.

Mechanical enforcement of the hub-and-spoke layering. Runs as part of
`make ci`. Complements ruff (style/correctness) by catching the class
of bugs ruff cannot see: cross-layer imports that, if left unchecked,
turn a clean architecture into a tangled graph.

Rules enforced
--------------
1. **Plugin isolation.**  Code under `prax/plugins/tools/**` must not
   import `prax.services.*` or `prax.agent.*`.  Plugins access host
   functionality only via the capabilities gateway
   (`prax.plugins.capabilities`).  Rationale: the capability gateway
   is the single audited bottleneck between untrusted plugin code and
   privileged host state.  See `docs/research/plugin-sandboxing.md`.

2. **No reverse dependency from services to agent.**  Code under
   `prax/services/**` must not import `prax.agent.*`.  The agent layer
   depends on services; reversing the arrow creates cycles and makes
   the service layer untestable without the agent runtime.
   Infrastructure modules that happen to live under `prax.agent` but
   are architecturally layer-free (`llm_factory`, `user_context`) are
   carved out — these should be moved to `prax/` root in a future
   refactor, at which point the carve-outs go away.

3. **Services are HTTP-agnostic.**  Code under `prax/services/**`
   must not import `prax.blueprints.*`.  Blueprints (Flask routes)
   import services, not the reverse.

Existing violations are grandfathered in `ALLOWLIST` below — each
entry is documented technical debt.  The rule that matters is: **new
code must not add to the allowlist**.  When you fix a violation,
remove the entry.

Usage
-----
    python scripts/check_layers.py          # scan and report
    python scripts/check_layers.py --strict # fail on allowlist drift
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRAX_ROOT = REPO_ROOT / "prax"

# Modules that live under prax.agent.* but are architecturally
# infrastructure — services may import them.  Move them out of
# prax.agent someday and delete this carve-out.
SERVICES_AGENT_CARVE_OUT: set[str] = {
    "prax.agent.llm_factory",
    "prax.agent.user_context",
}

# Grandfathered violations — each entry is documented technical debt.
# Format: "<path>:<lineno> -> <imported_module>"
# NEW CODE MUST NOT ADD TO THIS LIST.  When you fix one, remove it.
ALLOWLIST: set[str] = {
    # Plugins reaching into host internals.  Pre-subprocess-isolation
    # code; should migrate to capability-gateway calls.  Tracked in
    # docs/research/plugin-sandboxing.md.
    "prax/plugins/tools/arxiv_reader/plugin.py:300 -> prax.services",
    "prax/plugins/tools/news_summary/plugin.py:18 -> prax.services.workspace_service",
    "prax/plugins/tools/news/plugin.py:24 -> prax.services.workspace_service",
    "prax/plugins/tools/news/plugin.py:371 -> prax.services.library_service",
    "prax/plugins/tools/pdf_reader/plugin.py:15 -> prax.services.pdf_service",
    "prax/plugins/tools/youtube_reader/plugin.py:18 -> prax.services.youtube_service",
    "prax/plugins/tools/rss_reader/plugin.py:12 -> prax.services.workspace_service",
    "prax/plugins/tools/arxiv_reader/plugin.py:299 -> prax.agent.user_context",
    "prax/plugins/tools/news_summary/plugin.py:17 -> prax.agent.user_context",
    "prax/plugins/tools/news/plugin.py:23 -> prax.agent.user_context",
    "prax/plugins/tools/rss_reader/plugin.py:11 -> prax.agent.user_context",
    # Services reaching back into agent.  Each is a real architectural
    # smell to pay down; none are load-bearing enough to fix right now.
    "prax/services/conversation_service.py:27 -> prax.agent",
    "prax/services/feedback_service.py:214 -> prax.agent.trace",
    "prax/services/scheduler_service.py:234 -> prax.agent.orchestrator",
    # task_runner_service spawns a synthetic orchestrator turn per
    # picked-up task — same pattern as scheduler_service.  Both
    # should long-term route through a background-work abstraction
    # that lives outside prax.agent.*.
    "prax/services/task_runner_service.py:246 -> prax.agent.orchestrator",
    # trace_search_service reads in-memory ExecutionGraph state that
    # lives in prax.agent.trace.  Same shape as feedback_service's
    # existing carve-out for prax.agent.trace — these are "trace as
    # data" reads, not "run the agent" calls.  A future refactor
    # should move ExecutionGraph persistence into a service layer.
    "prax/services/trace_search_service.py:338 -> prax.agent.trace",
    # Services reaching into blueprints.  teamwork_hooks is a bridge
    # module and could legitimately move.
    "prax/services/teamwork_hooks.py:60 -> prax.blueprints.teamwork_routes",
}


@dataclass(frozen=True)
class Violation:
    rule: str
    path: Path
    lineno: int
    imported: str

    def key(self) -> str:
        rel = self.path.relative_to(REPO_ROOT).as_posix()
        return f"{rel}:{self.lineno} -> {self.imported}"


def _iter_imports(path: Path) -> list[tuple[str, int]]:
    """Return (imported_module, lineno) for every top-level import in a file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.module, node.lineno))
    return out


def _is_plugin_tool(path: Path) -> bool:
    parts = path.relative_to(PRAX_ROOT).parts
    return len(parts) >= 2 and parts[0] == "plugins" and parts[1] == "tools"


def _is_service(path: Path) -> bool:
    parts = path.relative_to(PRAX_ROOT).parts
    return bool(parts) and parts[0] == "services"


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def scan() -> list[Violation]:
    violations: list[Violation] = []
    for path in PRAX_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for imported, lineno in _iter_imports(path):
            if _is_plugin_tool(path):
                if _matches(imported, "prax.services"):
                    violations.append(Violation(
                        "plugin_imports_services", path, lineno, imported,
                    ))
                if _matches(imported, "prax.agent"):
                    violations.append(Violation(
                        "plugin_imports_agent", path, lineno, imported,
                    ))
            if _is_service(path):
                if _matches(imported, "prax.agent") and imported not in SERVICES_AGENT_CARVE_OUT:
                    violations.append(Violation(
                        "services_imports_agent", path, lineno, imported,
                    ))
                if _matches(imported, "prax.blueprints"):
                    violations.append(Violation(
                        "services_imports_blueprints", path, lineno, imported,
                    ))
    return violations


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    violations = scan()
    new_violations = [v for v in violations if v.key() not in ALLOWLIST]
    stale_entries = ALLOWLIST - {v.key() for v in violations}

    if new_violations:
        print("Layer check FAILED — new architectural violations:", file=sys.stderr)
        for v in sorted(new_violations, key=Violation.key):
            print(f"  [{v.rule}] {v.key()}", file=sys.stderr)
        print(
            "\nEither fix the violation or, if this is genuine "
            "technical debt to pay down later, add the exact entry "
            "to ALLOWLIST in scripts/check_layers.py with a comment "
            "explaining why.",
            file=sys.stderr,
        )
        return 1

    if stale_entries:
        msg = f"Layer check found {len(stale_entries)} stale allowlist entries:"
        print(msg, file=sys.stderr)
        for entry in sorted(stale_entries):
            print(f"  {entry}", file=sys.stderr)
        print(
            "\nThese violations no longer exist — delete the "
            "corresponding entries from ALLOWLIST.",
            file=sys.stderr,
        )
        if strict:
            return 1
        print("(not strict mode — continuing)", file=sys.stderr)

    print(f"Layer check OK — {len(violations)} known violations, all allow-listed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
