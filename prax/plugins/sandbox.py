"""Subprocess-isolated plugin testing.

Before any plugin goes live, it is imported and validated in a separate
process so that import errors, syntax bugs, or missing dependencies
cannot crash the running agent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

_SAFE_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
    "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
}


def sandbox_test_plugin(plugin_path: str, *, timeout: int = 30,
                        example: dict | None = None) -> dict:
    """Test a plugin in an isolated subprocess.

    Always checks the structure: the module imports, ``register()`` returns a
    list of tools with names and descriptions. That says nothing about what a
    tool does. With *example* (``{"tool": name, "args": {...}, "expect":
    text}``) it also calls that tool and requires the output to contain
    ``expect`` (docs/research/autoenvscaling.md: running the reference is what
    separates "it builds" from "it works").

    Returns ``{"passed": bool, "errors": [...], "tools": [...], "behaviour":
    "tested" | "untested" | "failed", "example_output": str}``.
    """
    example_json = json.dumps(example) if example else ""
    test_script = textwrap.dedent(f"""\
        import sys, json, traceback
        result = {{"passed": True, "errors": [], "tools": [], "behaviour": "untested"}}
        example = json.loads({example_json!r}) if {example_json!r} else None
        tools = []
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "plugin_under_test", {plugin_path!r}
            )
            if spec is None or spec.loader is None:
                result["errors"].append("Could not create module spec for plugin")
                result["passed"] = False
            else:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                if not hasattr(mod, "register"):
                    result["errors"].append("Missing register() function")
                    result["passed"] = False
                else:
                    tools = mod.register()
                    if not isinstance(tools, list):
                        result["errors"].append("register() must return a list")
                        result["passed"] = False
                    else:
                        for t in tools:
                            if not hasattr(t, "name") or not hasattr(t, "description"):
                                result["errors"].append(
                                    f"Tool missing name/description: {{t}}"
                                )
                                result["passed"] = False
                            else:
                                result["tools"].append(t.name)
        except Exception:
            result["errors"].append(traceback.format_exc())
            result["passed"] = False

        if result["passed"] and example:
            result["behaviour"] = "failed"
            tool = next((t for t in tools if getattr(t, "name", None) == example.get("tool")), None)
            if tool is None:
                result["errors"].append(
                    "example_call: no tool named " + repr(example.get("tool"))
                    + "; the plugin defines " + repr(result["tools"]))
            else:
                try:
                    out = tool.invoke(example.get("args") or {{}})
                except Exception:
                    result["errors"].append("example_call raised:\\n" + traceback.format_exc(limit=4))
                else:
                    text = out if isinstance(out, str) else str(out)
                    result["example_output"] = text[:500]
                    expect = str(example.get("expect") or "")
                    if expect and expect not in text:
                        result["errors"].append(
                            "example_call: output does not contain " + repr(expect)
                            + "; it returned " + repr(text[:300]))
                    else:
                        result["behaviour"] = "tested"
            if result["behaviour"] != "tested":
                result["passed"] = False

        print(json.dumps(result))
    """)

    try:
        proc = subprocess.run(
            [sys.executable, "-c", test_script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_SAFE_ENV,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "errors": ["Plugin test timed out"], "tools": []}

    if proc.returncode != 0:
        stderr_tail = proc.stderr[-500:] if proc.stderr else "unknown error"
        return {"passed": False, "errors": [stderr_tail], "tools": []}

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "passed": False,
            "errors": [f"Invalid test output: {proc.stdout[:300]}"],
            "tools": [],
        }
