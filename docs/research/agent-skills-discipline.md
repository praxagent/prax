# Agent Skills — the authoring discipline (not the file format)

Reference note. **Verdict: reference + principle to reinforce; one adopt-candidate
(success-side procedural capture).** The value for Prax isn't the `SKILL.md` file
shape — Prax already self-authors reusable capability and already does progressive
disclosure — it's the discipline of *when an artifact is born*: from a **gap
discovered by actually solving a problem**, never speculatively for an unsolved one.

- Source: Anson Biggs, *"You're Probably Using Agent Skills Wrong"*,
  notes.ansonbiggs.com (accessed 2026-06).
  <https://notes.ansonbiggs.com/youre-probably-using-agent-skills-wrong/>

## What it is

A corrective on Anthropic's Agent Skills (a `SKILL.md` folder = metadata + procedure
+ optional scripts + `references/`, surfaced by progressive disclosure). The thesis:

- **Wrong way** — ask an agent to *generate* a Skill on the fly for a problem it
  **can't** solve. That's "reinvented thinking blocks, but worse": you're asking the
  model to think harder about something it already failed at. A Skill must "know
  something the fresh model doesn't."
- **Right way** — Skills encode **knowledge gaps discovered through actual
  problem-solving**. Three triggers: (1) **context preservation** — document the
  routine the agent had to "run around to figure out"; (2) **repetition reduction** —
  package an instruction you keep retyping; (3) **hard-problem documentation** —
  after you solve something the hard way / with intervention, formalize *what
  initially blocked you*.

It's a *practice*, not a tool — no license or dependency, so the only real "cost" is
design overlap with what Prax already has.

## Where it maps in Prax

Prax is already ~70% aligned — for **code** and for **failures**:

- **Self-authoring after a real need.** `plugin_write` / `plugin_activate`
  (`prax/agent/plugin_tools.py:135,337`) let Prax write a tool for itself,
  sandbox-test it, hot-swap it (`prax/plugins/loader.py:596`), and persist it to the
  user's workspace (`get_workspace_plugins_dir` → `workspaces/{user}/plugins/`), with
  rationale/provenance tracked in `self_tools/registry.json`
  (`prax/services/self_tool_registry_service.py:21`). The system prompt already
  states the doctrine — *"create tools on the fly… save it. Next time you need it,
  it's there"* (`prax/plugins/prompts/system_prompt.md:81`). That **is** the
  article's "right way," realized as code.
- **Progressive disclosure** is a named, real pattern: `progress_read`→
  `progress_detail` (`prax/services/progress_service.py`), `trace_search`→
  `trace_detail` (`prax/services/trace_search_service.py`), the user-notes
  token-overlap selector (`prax/services/workspace_service.py:626`), OKF `index.md`
  (`prax/services/okf_bridge.py`). Index-then-drill-down is house practice.
- **Post-hoc-from-gaps learning** exists on the failure side: metacognitive failure
  patterns authored only after ≥3 real occurrences then injected as prompt warnings
  (`prax/agent/metacognitive.py`), the failure journal
  (`prax/services/memory/failure_journal.py`), and `review_my_traces`→
  `self-improvement-log.md` (`prax/agent/workspace_tools.py:1498`).

## The two gaps it exposes

1. **Success-side, knowledge-not-code capture.** Every learning loop in Prax keys on
   **failure** (exceptions, negative feedback) or graduates the lesson into a
   **Python plugin** (backlog #16: "repeated recipes graduate to workspace
   *plugins*"). There is no lightweight artifact for *"here's the hard-won procedure
   for this class of task in this project"* — prose, not a tool.
   `metacognitive.record_success` only decays a failure score; it authors nothing.
   That is exactly the `SKILL.md` niche, and it is missing.
2. **Recall by task-similarity.** A stored procedure isn't auto-surfaced when a
   *similar task* recurs — recall today is "the tool now exists in the list" or an
   explicit `memory_recall` / `trace_search`. The progressive-disclosure-on-recurrence
   half is absent.

And one **guardrail worth lifting into Prax's own self-authoring**: don't crystallize
a skill/tool *speculatively* for an unsolved problem — only after the gap is overcome.
This abstracts cleanly onto the existing prompt selectivity + the metacognitive
≥3-occurrence gate (encode the *principle*, per `CLAUDE.md`'s "never spike
benchmarks" — not the article's specific example).

## Caveat — overlap is the real cost

A "skills layer" risks duplicating three things Prax already has: the **Library**
(workspace markdown notes), the **self_tools registry**, and **memory**. The honest
design question is **not** "build `SKILL.md`" but *"is success-side procedural capture
a genuinely new tier, or just a recall policy + a frontmatter convention over stores
we already have?"* Lean to the latter — a new subsystem here would violate "Prax
stays nimble."

## If we adopted it (sketch — not built)

A flag-gated, default-off **success-side capture**: when Prax solves a task that
*initially blocked it* (the same gap signal the metacognitive store already detects,
inverted to the success case), offer to distill the **generalized** procedure into a
workspace artifact — a markdown "playbook" with `name` + one-line `description`
frontmatter (for matching), the abstracted steps, and optional script/reference
links — stored alongside `self_tools` so it round-trips via `workspace_push`,
**rather than as a new store**. Recall: match the current task against playbook
descriptions (reuse the user-notes token-overlap selector or `trace_search`
embeddings), inject the lightweight description, expand on demand. Keep authoring
**gap-triggered, never speculative**, and **measure it** with the
[`skill_capture_reuse`](../../prax/eval/goldens/skill_capture_reuse.yaml) golden.

## Bottom line

The article validates Prax's self-authoring instinct and sharpens one guardrail.
Net-new value is narrow but real: **a knowledge-not-code, success-side capture lane
with similarity recall** — captured here, tracked as
[IDEAS_BACKLOG #17](../IDEAS_BACKLOG.md) and the `skill_capture_reuse` golden, and
gated on resolving the overlap question before any build. See also
[OKF](open-knowledge-format.md) (the markdown-knowledge format it would reuse),
[Bayer reliability](reliable-agentic-systems-bayer.md) (the post-hoc / selectivity
principles), and [`../guides/extending.md`](../guides/extending.md) (the plugin
self-authoring it complements).

**Concrete blueprint:** Browser-BC / "Journey Forge Local"
([github.com/Einsia/Browser-BC](https://github.com/Einsia/Browser-BC)) is a working
implementation of this for *browser* tasks — record a successful trajectory →
capability-bucket (dedup) → distill a reusable `SKILL.md`. It validates the
record-from-real-success discipline and the dedup-by-theme mechanic (cf. signals,
#23), and argues for the **browser spoke as the first capture domain** (repetitive
tasks + an existing CDP/Playwright trajectory stream). Pattern only — separate,
unlicensed tool. See IDEAS_BACKLOG #17 for the borrowables.
