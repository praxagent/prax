"""Settings-derived maps must be read, not captured.

`num_to_names`, `email_map` and `num_to_greetings` are built from settings when
`helpers_dictionaries` is imported. A module that does
`from prax.helpers_dictionaries import num_to_names` binds *that* dict forever:
reload the configuration and a new dict is built which the module never sees, so
it goes on answering from a map nobody can observe.

Two of these guard access — who may call, who may text — so the stale copy is
not a cosmetic problem. It also made test outcomes depend on import order, which
is how the bug surfaced: the same test passed alone and failed in a full run.

This test names the class rather than the three modules that had it, so the next
one is caught when it is written.
"""
from __future__ import annotations

import ast
import pathlib

# Built from settings at import; a reload replaces the object.
DERIVED_FROM_SETTINGS = {"num_to_names", "email_map", "num_to_greetings"}

PRAX = pathlib.Path(__file__).resolve().parent.parent / "prax"


def _import_time_bindings(path: pathlib.Path) -> set[str]:
    """Names this module binds from helpers_dictionaries at module scope.

    Function-local imports are fine and deliberately not flagged: they re-read
    the module attribute on every call, which is the behaviour we want.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound: set[str] = set()
    for node in tree.body:                       # module scope only
        if isinstance(node, ast.ImportFrom) and node.module == "prax.helpers_dictionaries":
            bound.update(a.name for a in node.names)
    return bound


def test_no_module_captures_a_settings_derived_map_at_import():
    offenders = {}
    for path in PRAX.rglob("*.py"):
        if path.name == "helpers_dictionaries.py":
            continue
        captured = _import_time_bindings(path) & DERIVED_FROM_SETTINGS
        if captured:
            offenders[str(path.relative_to(PRAX.parent))] = sorted(captured)

    assert not offenders, (
        "these modules bind a settings-derived map at import, so a config "
        f"reload will not reach them: {offenders}. Import the module "
        "(`from prax import helpers_dictionaries`) and read the attribute at "
        "call time instead.")


def test_the_guard_would_catch_a_real_offender(tmp_path):
    """A checker that cannot fail is not a check."""
    bad = tmp_path / "bad.py"
    bad.write_text("from prax.helpers_dictionaries import num_to_names\n")
    assert _import_time_bindings(bad) & DERIVED_FROM_SETTINGS == {"num_to_names"}

    ok = tmp_path / "ok.py"
    ok.write_text("def f():\n    from prax.helpers_dictionaries import num_to_names\n"
                  "    return num_to_names\n")
    assert not (_import_time_bindings(ok) & DERIVED_FROM_SETTINGS)
