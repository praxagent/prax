"""data_query — deterministic SQL / number-crunching over data in the sandbox.

Prax already has arbitrary code execution (``sandbox_shell``) and a full coding
spoke, but ad-hoc numeric/tabular work through a free-form shell is error-prone.
``data_query`` is the purpose-built, deterministic primitive: it runs a **DuckDB
SQL** query in the sandbox container and returns a clean table. DuckDB reads
CSV / Parquet / JSON files *directly* (``SELECT * FROM '/workspace/active/x.csv'``)
and does aggregation, joins, window functions, and statistics — so most "process
these numbers" tasks are one query, not a coding session.

Design (mirrors ``lean_tools`` deliberately):
- Runs in the **sandbox container** (never the harness host — the sandbox-only
  execution stance), via ``run_shell`` on the shared client.
- Invokes ``/opt/prax-venv/bin/python`` explicitly (where duckdb + pandas are
  installed) so the deps resolve even under a ``docker exec`` shell that didn't
  inherit the venv on PATH — same robustness trick as lean_check's ELAN_HOME.
- Flag-gated (``DATA_TOOLS_ENABLED``, default off) and sandbox-gated; degrades
  with a clear, actionable message when either the flag or the libs are absent.
- Pure formatting/parsing helpers are separated out so the report logic is
  unit-tested with **zero sandbox and zero keys**.
"""
from __future__ import annotations

import base64
import logging

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.services.sandbox_bridge import configured_client as get_client

logger = logging.getLogger(__name__)

# How many result rows to render before truncating (kept bounded so a
# `SELECT *` over a big file can't flood the model's context).
MAX_ROWS = 100

_EXIT_MARKER = "___DATA_EXIT___"
_MISSING_DEPS_MARKER = "___DATA_NO_DEPS___"

# The in-sandbox runner. Reads SQL from stdin, executes it with DuckDB, prints a
# bounded pandas table. Kept as a module constant so the exact program is
# reviewable (and so the shell command below stays readable).
_RUNNER = f"""
import sys
try:
    import duckdb, pandas as pd
except Exception:
    print("{_MISSING_DEPS_MARKER}")
    sys.exit(0)
pd.set_option("display.max_rows", {MAX_ROWS})
pd.set_option("display.max_columns", 60)
pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 200)
sql = sys.stdin.read()
try:
    rel = duckdb.sql(sql)
    if rel is None:
        print("(statement executed; no result set)")
    else:
        df = rel.df()
        total = len(df)
        shown = df.head({MAX_ROWS})
        print(shown.to_string(index=False) if total else "(0 rows)")
        if total > {MAX_ROWS}:
            print(f"\\n[showing first {MAX_ROWS} of {{total}} rows]")
        else:
            print(f"\\n[{{total}} row(s)]")
except Exception as e:
    print("QUERY ERROR:", type(e).__name__, str(e))
"""


def _format_result(*, exit_code: int, stdout: str, stderr: str) -> str:
    """Turn a raw sandbox run into the tool's agent-facing report.

    Pure — no I/O — so the verdict logic is unit-tested without a sandbox.
    """
    out = (stdout or "").strip()
    err = (stderr or "").strip()

    if _MISSING_DEPS_MARKER in out:
        return ("Data libraries (duckdb/pandas) are not installed in the sandbox. "
                "Rebuild the sandbox image (its Dockerfile installs them into "
                "/opt/prax-venv), or the tool cannot run.")

    if out:
        # The query itself may have failed logically (bad SQL) — that's a normal,
        # useful result the agent should see and fix, not a tool error.
        return out
    if err:
        return f"data_query produced no output. STDERR:\n{err[:1500]}"
    return f"data_query produced no output (exit code {exit_code})."


@risk_tool(risk=RiskLevel.LOW)
def data_query(sql: str, timeout: int = 60) -> str:
    """Run a DuckDB SQL query in the sandbox for data / number crunching.

    The deterministic way to compute over tabular or numeric data — prefer it
    over eyeballing numbers or a free-form coding session. DuckDB reads files
    directly, so you can query a CSV/Parquet/JSON without loading it first:

        SELECT category, count(*) n, round(avg(amount), 2) avg_amount
        FROM '/workspace/active/sales.csv'
        GROUP BY category ORDER BY n DESC

    It also does pure computation and stats (``SELECT 2^10``, ``median(x)``,
    ``regr_slope(y, x)``, window functions, ``PIVOT``…). Results come back as a
    bounded text table (first 100 rows). Runs entirely in the isolated sandbox
    container.

    Note: files the user should receive go under /workspace/active/ (deliver
    with workspace_send_file); the container's /tmp is internal.

    Args:
        sql: A DuckDB SQL query. Reference data files by absolute path in
            single quotes (DuckDB infers the format from the extension).
        timeout: Max seconds for the query (default 60).

    Returns the result table, or a clear ``QUERY ERROR: …`` line if the SQL is
    invalid (fix it and retry).
    """
    from prax.settings import settings
    if not settings.data_tools_enabled:
        return ("Data tools are disabled (DATA_TOOLS_ENABLED=false). Enable the flag "
                "and ensure the sandbox image carries duckdb + pandas.")
    if not settings.sandbox_available:
        return "Sandbox is disabled (SANDBOX_ENABLED=false); data_query needs the sandbox container."

    # base64 both the SQL and the runner through the shell so arbitrary quotes,
    # newlines, and unicode survive intact. The venv python is addressed by
    # absolute path so duckdb/pandas resolve regardless of the exec shell's PATH.
    sql_b64 = base64.b64encode((sql or "").encode()).decode()
    runner_b64 = base64.b64encode(_RUNNER.encode()).decode()
    cmd = (
        f'printf %s {runner_b64} | base64 -d > /tmp/_dq_runner.py && '
        f'printf %s {sql_b64} | base64 -d | '
        '/opt/prax-venv/bin/python /tmp/_dq_runner.py; rc=$?; '
        'rm -f /tmp/_dq_runner.py; '
        f'echo "{_EXIT_MARKER}$rc"'
    )

    try:
        result = get_client().run_shell(cmd, timeout=timeout)
    except Exception as exc:
        logger.warning("data_query run_shell failed: %s", exc)
        return f"data_query failed to run in the sandbox: {exc}"

    if "error" in result:
        return f"Sandbox error: {result['error']}"

    stdout = result.get("stdout", "") or ""
    stderr = result.get("stderr", "") or ""

    exit_code = 0
    if _EXIT_MARKER in stdout:
        stdout, _, tail = stdout.rpartition(_EXIT_MARKER)
        try:
            exit_code = int(tail.strip().splitlines()[0])
        except (ValueError, IndexError):
            exit_code = 0
    # Missing python venv entirely (image without the venv) → 127.
    blob = f"{stdout}\n{stderr}".lower()
    if exit_code == 127 or "no such file" in blob and "prax-venv" in blob:
        return ("The sandbox python venv (/opt/prax-venv) or its data libraries are "
                "missing. Rebuild the sandbox image (see the prax-sandbox Dockerfile).")

    return _format_result(exit_code=exit_code, stdout=stdout, stderr=stderr)


def build_data_tools() -> list:
    """Return the data tools when enabled — wired into the sandbox spoke."""
    from prax.settings import settings
    if not (settings.data_tools_enabled and settings.sandbox_available):
        return []
    return [data_query]
