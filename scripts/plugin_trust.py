#!/usr/bin/env python3
"""Review and approve workspace plugins for WORKSPACE_PLUGIN_INTEGRITY_ENABLED.

With the flag on, Prax runs a workspace or imported plugin only when its files
match a digest recorded by one of its own plugin tools. Plugins that predate
the ledger, or that changed outside those tools (the sandbox, TeamWork's file
API, a ``git pull`` of the workspace), are blocked until an operator reads
them and approves them here.

    uv run python scripts/plugin_trust.py list               # every workspace plugin
    uv run python scripts/plugin_trust.py approve PATH [...]  # after reviewing PATH

Run it on the Prax host as the account that owns the install (in production:
``sudo -u praxsvc env HOME=/home/praxsvc uv run python scripts/plugin_trust.py``).
Approval is deliberately not an agent tool: the point is that a human looked.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _workspace_plugin_dirs(workspace_root: Path) -> list[Path]:
    seen: set[Path] = set()
    dirs: list[Path] = []
    if not workspace_root.is_dir():
        return dirs
    for user_dir in sorted(workspace_root.iterdir()):
        plugins = user_dir / "plugins"
        if not plugins.is_dir():
            continue
        real = plugins.resolve()  # usr_* entries are often symlinks to one workspace
        if real not in seen:
            seen.add(real)
            dirs.append(real)
    return dirs


def _units(plugins_dir: Path):
    from prax.plugins.integrity import plugin_unit
    from prax.plugins.loader import PluginLoader

    loader = PluginLoader()  # discovery only — nothing is imported or loaded
    for plugin_file, rel_key in loader._discover_plugins(plugins_dir):
        yield rel_key, plugin_unit(plugin_file)


def cmd_list(workspace_root: Path) -> int:
    from prax.plugins.integrity import get_ledger

    ledger = get_ledger()
    any_found = False
    for plugins_dir in _workspace_plugin_dirs(workspace_root):
        for _rel_key, unit in _units(plugins_dir):
            any_found = True
            trusted, reason = ledger.check(unit)
            state = "trusted  " if trusted else "UNTRUSTED"
            print(f"{state} {unit}" + ("" if trusted else f"   ({reason})"))
    if not any_found:
        print(f"No workspace plugins under {workspace_root}.")
    return 0


def cmd_approve(paths: list[str]) -> int:
    from prax.plugins.integrity import get_ledger

    ledger = get_ledger()
    status = 0
    for raw in paths:
        unit = Path(raw).resolve()
        if unit.name == "plugin.py":
            unit = unit.parent
        if not unit.exists():
            print(f"not found: {raw}", file=sys.stderr)
            status = 1
            continue
        value = ledger.record(unit, "operator")
        print(f"approved {unit}  sha256:{value[:16]}…")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="show every workspace plugin and whether it is trusted")
    p_list.add_argument("--workspaces", help="workspace root (default: WORKSPACE_DIR)")
    p_approve = sub.add_parser("approve", help="record the current contents as trusted")
    p_approve.add_argument("paths", nargs="+", help="plugin folder, its plugin.py, or a flat plugin file")
    args = parser.parse_args(argv)

    if args.cmd == "approve":
        return cmd_approve(args.paths)
    if args.workspaces:
        root = Path(args.workspaces)
    else:
        from prax.settings import settings
        root = Path(settings.workspace_dir)
    return cmd_list(root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
