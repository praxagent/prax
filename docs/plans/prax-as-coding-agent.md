# Prax as a Coding Agent — Direction Note

**Status:** tabled (as of 2026-04-19). Captures the decision point and
the two paths forward so future-us can pick one up with context.

## The question

Should Prax invest in the Anthropic/OpenAI "harness engineering"
patterns from [`docs/research/harness-engineering.md`](../research/harness-engineering.md)
(Tier C items #3/#4/#6/#7/#12 in [`improvement_tier.md`](improvement_tier.md)):
two-agent initializer/coding split, `feature_list.json` ground truth,
`init.sh` + startup ritual, mandatory end-of-session clean-state
commits, golden-principles cleanup bots — and the Chorus-style
spec-tools layer (#C14)?

Or does Prax continue to treat coding as a **delegated** activity and
invest elsewhere?

## The load-bearing fact

**Prax already delegates coding to purpose-built coding agents.**
This isn't a greenfield decision.

- `prax/agent/claude_code_tools.py` exposes `claude_code_start_session`,
  `claude_code_message`, `claude_code_ask`.
- `SELF_IMPROVE_AGENT` env var selects `claude-code` (default), `codex`,
  or `opencode`.
- `sandbox/Dockerfile` pre-installs all three CLIs.
- The sandbox spoke (`prax/agent/spokes/sandbox/agent.py`) delegates
  coding work through this pathway.

So the choice is not "become a coding agent or don't." It's "how
much of the harness-engineering pattern is worth owning at the Prax
layer, given that Claude Code already owns most of it inside the
sandbox."

## Path A — Invest in Prax-as-coding-agent

Build out the Anthropic/OpenAI harness patterns at the Prax layer:

- **A3.** Initializer/coding agent split when Prax starts a
  multi-session codegen project — initializer writes scaffolding +
  feature list + startup script, coding agent resumes every subsequent
  session.
- **A4.** `feature_list.json` with `passes: true/false` per feature
  as the single source of truth for completeness. Prax writes
  features as failing, coding agent flips them to true only after
  verification.
- **A6.** `init.sh` + the mandatory startup ritual (`pwd` → read
  progress → read feature list → `git log` → run init → smoke-test).
- **A7.** End-of-session clean-state enforcement — every session ends
  with a git commit, an updated progress file, a revert-to-green if
  needed.
- **A12.** Golden-principles + cleanup bots running on a scheduler
  that scan for drift and open refactor PRs.
- **A14 (C14).** Spec-tools layer — Chorus-style AI-proposed, human-
  approved task DAGs before execution. Moves Prax from L0 ("recipe
  selection") to L1 ("parameterised fixed skeleton") per the
  [agentic-todo-flows research](../research/agentic-todo-flows.md#20).

**Cost:** 1-2 weeks of real work. Most of the pattern value is
already present inside Claude Code's own harness (the one Anthropic
wrote about); we'd be reimplementing it at the Prax layer.

**Payoff:** Prax becomes a true multi-session software engineer on
its own, not dependent on delegating to Claude Code. Useful if:
- You want Prax to do long-horizon codegen *without* Claude Code in
  the loop (cost, vendor independence, offline).
- You want the harness state (feature list, progress) to be legible
  to Prax's orchestrator across turns, not just inside a sandbox
  session.

## Path B — Double down on delegation

Keep Prax as an orchestrator. Invest in the seams between Prax and
the coding CLIs:

- **B-a.** Make Claude Code / OpenCode / Codex sessions **first-class
  Prax tasks** that the orchestrator tracks across turns. Today
  `claude_code_start_session` kicks off a session but Prax doesn't
  have a structured concept of "this session is still running,
  these are its artefacts, resume it."
- **B-b.** Bring the coding-agent's progress back into Prax's context.
  When Claude Code inside the sandbox writes its own progress file,
  expose that file through `sandbox_view` / a dedicated
  `coding_session_status` tool so Prax can read what the inner agent
  has done without re-invoking it.
- **B-c.** Multi-agent comparison. When a codegen task is hard, run
  it in parallel under two of {Claude Code, OpenCode, Codex}, diff
  the outputs, merge the better one. Prax is uniquely positioned to
  do this because it already abstracts over all three.
- **B-d.** Let Prax own the **verification surface** (tests, linters,
  browser_verify, observability) while delegating the **writing**
  to the coding agent. This matches the article's harness principle:
  the environment (verification + feedback loops) is the valuable
  bit. Generation is the commodity.

**Cost:** smaller, incremental. Each item is a few days.

**Payoff:** Plays to existing strength. Prax doesn't try to replicate
what Anthropic already shipped inside Claude Code's harness. Prax
becomes better at *orchestrating* coding work, which is the moat.

## Recommendation

**Path B** looks like the right answer for Prax's actual usage
today — you're a single user running Prax as an assistant that
sometimes spawns coding work, not a team running Prax as an
autonomous SWE. Every investment in Path A is a reimplementation of
a harness that already exists one level deeper (inside Claude Code
itself). Path B invests in the *interface* between Prax and that
harness, which is where Prax's value lives.

If Path B items B-a and B-b prove out and there's still demand for
more, **C14 (spec-tools layer) is the one Path A item worth doing
anyway** — because it graduates Prax from L0 to L1 across *all*
Prax work, not just coding. That's a cross-cutting win that a Path B
investment doesn't give.

## When to revisit

Revisit if any of the following becomes true:

1. You want Prax to do multi-session codegen **offline** or without a
   third-party coding CLI in the loop.
2. Claude Code / OpenCode / Codex diverge enough in capability that
   you want a Prax-level harness to paper over the differences.
3. The "wall" between Prax's orchestrator and the coding agent
   becomes the consistent failure mode in codegen sessions — i.e.,
   context lost at the delegation boundary — and the fix requires
   Prax to own more of the state.

Until then, this doc exists so nobody forgets *why* we tabled it.

## Related

- [`docs/research/harness-engineering.md`](../research/harness-engineering.md) — SWE-agent ACI, Anthropic long-running harness, OpenAI harness engineering, awesome-agent-harness taxonomy.
- [`docs/plans/improvement_tier.md`](improvement_tier.md) — full Tier A/B/C breakdown.
- [`docs/research/agentic-todo-flows.md`](../research/agentic-todo-flows.md) — §20–§27 on L0→L1→L2 autonomy levels; Prax is L0 today with L1 machinery built but not exposed.
