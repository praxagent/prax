"""Lean 4 proof-checking tool — compile Lean source in the sandbox and run the
cdc-lean axiom-audit *trust gate*.

Verdict behind this: docs/research/cdc-lean-teach-prax-lean.md. "Teaching Prax
Lean" is a capability, not fine-tuning — the Lean kernel is an un-gameable
verifier, and the harness contribution is a governed compile-and-audit loop.

Design:
- Compilation runs in the **sandbox container** (never the harness host — the
  sandbox-only-execution stance), via ``run_shell`` on the shared client. Plain
  ``lean <file>`` handles Lean-core theorems (Nat/Int arithmetic, rfl, basic
  logic, propositional/first-order proofs) with **no mathlib and no lake
  project** — that's the toolchain-only scope. mathlib-dependent proofs (which
  need ``lake exe cache get``) are a documented later extension.
- Compiling is necessary but **not sufficient** (cdc-lean's lesson): a proof
  with ``sorry`` still "compiles". So the tool also (a) scans the source for the
  forbidden tokens ``sorry``/``admit``/``native_decide`` and (b) when a theorem
  name is given, appends ``#print axioms <name>`` and checks the result depends
  only on Lean's three standard axioms (``propext``, ``Classical.choice``,
  ``Quot.sound``).

Flag-gated (``LEAN_TOOLS_ENABLED``, default off) and sandbox-gated; degrades with
a clear message when either the flag or the toolchain is absent. Pure parsing
helpers are separated out so they're unit-tested with zero sandbox and zero keys.
"""
from __future__ import annotations

import base64
import logging
import re

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.services.sandbox_bridge import configured_client as get_client

logger = logging.getLogger(__name__)

# Lean's three standard axioms — a kernel-checked proof depending only on these
# (and no sorry/native_decide) is trustworthy. This is exactly cdc-lean's gate.
STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

# Tokens that make a "successful" compile meaningless (an unproven hole, or a
# decision procedure that trusts unverified compiled code).
FORBIDDEN_TOKENS = ("sorry", "admit", "native_decide")

_EXIT_MARKER = "___LEAN_EXIT___"
_AXIOM_RE = re.compile(r"depends on axioms:\s*\[([^\]]*)\]")


def _find_forbidden(source: str) -> list[str]:
    """Return the forbidden tokens present in *source* as whole words.

    Lean comments are stripped first so an English "sorry"/"admit" in a comment
    doesn't trip the trust gate on a genuine proof. The strip is non-nesting
    (a token buried in a nested block comment may still be flagged — the
    conservative direction). When a theorem name is given, the axiom audit
    (``sorryAx``) is the authoritative signal; this scan is the fallback.
    """
    code = _strip_comments(source)
    found = []
    for tok in FORBIDDEN_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", code):
            found.append(tok)
    return found


def _strip_comments(source: str) -> str:
    """Remove Lean line (``-- …``) and block (``/- … -/``) comments.

    Non-nesting block match — good enough to cut the common false positive.
    """
    no_block = re.sub(r"/-.*?-/", " ", source, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", no_block)


def _parse_axioms(stdout: str) -> list[str] | None:
    """Extract the axiom list from a ``#print axioms`` line, or None if absent.

    Lean prints e.g. ``'thm' depends on axioms: [propext, Classical.choice]``.
    A declaration proved without any axioms prints ``... does not depend on any
    axioms`` → we return ``[]``.
    """
    if "does not depend on any axioms" in stdout:
        return []
    m = _AXIOM_RE.search(stdout)
    if not m:
        return None
    inner = m.group(1).strip()
    if not inner:
        return []
    return [a.strip() for a in inner.split(",") if a.strip()]


def _format_result(*, lean_rc: int, stdout: str, stderr: str, source: str,
                   theorem_name: str) -> str:
    """Turn a raw compile result into the tool's human/agent-facing report.

    Pure — no I/O — so the whole verdict logic is unit-tested without a sandbox.
    """
    combined = f"{stdout}\n{stderr}".strip()
    compiled = lean_rc == 0
    forbidden = _find_forbidden(source)
    lines: list[str] = []

    if compiled:
        lines.append("✓ Lean compiled successfully (kernel-checked).")
    else:
        lines.append("✗ Lean compilation FAILED.")

    # The trust gate: a clean compile with a `sorry` is NOT a proof.
    if forbidden:
        lines.append(
            f"⚠ TRUST GATE: source contains {', '.join(forbidden)} — a compile that "
            "uses these is NOT a real proof."
        )

    # Axiom audit only means something on a proof that COMPILED. On a failed
    # compile Lean still emits a `depends on axioms:` line for partially-
    # elaborated decls — never present that as a clean audit under a ✗.
    if theorem_name and compiled:
        axioms = _parse_axioms(stdout)
        if axioms is None:
            lines.append(
                f"⚠ Could not read axioms for '{theorem_name}' — check the name "
                "matches a declaration in the source."
            )
        else:
            extra = [a for a in axioms if a not in STANDARD_AXIOMS]
            shown = ", ".join(axioms) if axioms else "(none)"
            if extra:
                lines.append(
                    f"⚠ AXIOM AUDIT: '{theorem_name}' depends on NON-standard axioms: "
                    f"{', '.join(extra)} (all: {shown})."
                )
            else:
                lines.append(
                    f"✓ AXIOM AUDIT: '{theorem_name}' depends only on standard axioms "
                    f"[{shown}] — clean."
                )

    if not compiled and combined:
        # Surface the compiler diagnostics (bounded) so the agent can fix them.
        lines.append("\n--- Lean output ---\n" + combined[:2000])
    elif combined and ("warning" in combined.lower() or "error" in combined.lower()):
        lines.append("\n--- Lean output ---\n" + combined[:2000])

    return "\n".join(lines)


@risk_tool(risk=RiskLevel.MEDIUM)
def lean_check(source: str, theorem_name: str = "", timeout: int = 120) -> str:
    """Compile Lean 4 source in the sandbox and run the axiom-audit trust gate.

    Use this to VERIFY a Lean proof is real — not just that it type-checks. A
    proof that compiles but uses ``sorry`` is flagged; when you name the theorem,
    its axiom dependencies are audited against Lean's three standard axioms
    (propext, Classical.choice, Quot.sound), exactly as OpenAI's cdc-lean does.

    Handles Lean-core proofs (arithmetic, rfl, logic) with no mathlib. For
    mathlib-dependent proofs the toolchain needs a lake project + cache (not yet
    wired). Runs entirely in the sandbox container.

    Args:
        source: The Lean 4 source to check (a complete snippet — imports,
            defs, and the ``theorem``/``example`` you want verified).
        theorem_name: Optional. The declaration to axiom-audit via
            ``#print axioms``. Omit to only compile + scan for ``sorry``.
        timeout: Max seconds for the compile (default 120).

    Returns a report: compile pass/fail (with diagnostics on failure), a trust-gate
    warning if the source uses sorry/admit/native_decide, and the axiom audit.
    """
    from prax.settings import settings
    if not settings.lean_tools_enabled:
        return ("Lean tools are disabled (LEAN_TOOLS_ENABLED=false). Enable the flag "
                "and ensure the sandbox image carries the Lean toolchain.")
    if not settings.sandbox_available:
        return "Sandbox is disabled (SANDBOX_ENABLED=false); lean_check needs the sandbox container."

    full = source.rstrip()
    if theorem_name:
        full += f"\n\n#print axioms {theorem_name}\n"

    # base64 through the shell so arbitrary Lean source (quotes, heredoc markers,
    # unicode) survives intact; a fresh mktemp dir keeps the container /tmp clean.
    # ELAN_HOME falls back to the documented sandbox install path (/opt/elan) so
    # the elan proxy resolves its toolchain even under a `docker exec` shell that
    # didn't inherit the image ENV — without overriding an explicit ELAN_HOME.
    b64 = base64.b64encode(full.encode()).decode()
    cmd = (
        'export ELAN_HOME="${ELAN_HOME:-/opt/elan}"; '
        'd=$(mktemp -d) && '
        f'printf %s {b64} | base64 -d > "$d/Check.lean" && '
        'lean "$d/Check.lean"; rc=$?; rm -rf "$d"; '
        f'echo "{_EXIT_MARKER}$rc"'
    )

    try:
        result = get_client().run_shell(cmd, timeout=timeout)
    except Exception as exc:
        logger.warning("lean_check run_shell failed: %s", exc)
        return f"lean_check failed to run in the sandbox: {exc}"

    if "error" in result:
        return f"Sandbox error: {result['error']}"

    stdout = result.get("stdout", "") or ""
    stderr = result.get("stderr", "") or ""

    # The Lean exit code is carried in our marker (the shell's own exit is the
    # trailing echo, which always runs — so the marker is present even when
    # `lean` itself is missing).
    marker_present = _EXIT_MARKER in stdout
    lean_rc = 1
    if marker_present:
        stdout, _, tail = stdout.rpartition(_EXIT_MARKER)
        try:
            lean_rc = int(tail.strip().splitlines()[0])
        except (ValueError, IndexError):
            lean_rc = 1

    # Missing toolchain: `lean` not on PATH → exit 127 (the shell's `echo` still
    # fires, so we detect it from the code, not from marker-absence). Give
    # actionable guidance instead of a generic compile failure.
    blob = f"{stdout}\n{stderr}".lower()
    if lean_rc == 127 or (not marker_present and ("not found" in blob or "no such file" in blob)):
        return ("Lean toolchain not found in the sandbox. Rebuild the sandbox image "
                "with elan/Lean installed (see the prax-sandbox Dockerfile), or the "
                "tool cannot run.")

    return _format_result(lean_rc=lean_rc, stdout=stdout.strip(), stderr=stderr.strip(),
                          source=source, theorem_name=theorem_name)


def build_lean_tools() -> list:
    """Return the Lean tools when enabled — wired into the sandbox spoke."""
    from prax.settings import settings
    if not (settings.lean_tools_enabled and settings.sandbox_available):
        return []
    return [lean_check]
