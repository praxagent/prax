# (Im)Paired Programming — coding agents improve productivity but harm understanding (arXiv 2607.26375)

**Balepur, Baumler, Chen, Choi, Rudinger, Boyd-Graber (Maryland/CMU/NYU;
in-progress preprint, 2026-07-29).** Controlled study, 54 students building a
website with either (a) an **agent that edits their code** or (b) a **chatbot
they type code from**. Understanding measured *after*, agent-free:
comprehension questions + extension tasks. Result: agents win on speed and
lose on understanding; the damage tracks **low-effort interaction patterns
specifically — copy-pasted prompts and auto-accepted edits**; and users
**prefer the agent anyway, while acknowledging they understood less**.

**Verdict: document + adopt as a design lens with two concrete consequences —
(1) the effort-vs-comprehension finding is the first external evidence FOR
TeamWork's watch-the-agent-work thesis, and it names the specific interaction
to design against (auto-accept); (2) "users prefer what degrades them" is a
warning about our own grounded signal — preference is not outcome.** No
capability to build, no benchmark to add; this changes how surfaces should be
shaped, and how we read TJ-likes-it as evidence.

---

## Why a human-factors paper matters here

Prax's stated purpose is a *trustworthy* harness where humans and agents share
a workspace — which presumes the human stays able to check the agent. This
paper measures the erosion of exactly that capacity, and finds it is not
caused by "using an agent" in the abstract but by **the low-effort path
through the interaction**. That distinction is actionable, because the
low-effort path is a UI choice, not a law of nature:

| Their degrading pattern | The Prax/TeamWork surface that already opposes it |
|---|---|
| Auto-accepted edits | Approval gates with an action **fingerprint** (the approval is bound to *what* was approved), governed-tool risk tiers, HIGH-risk human gating |
| Opaque change ("it just works now") | Execution Graphs, `trace_detail`, the append-only event log, watch-the-agent-work panels |
| Copy-paste prompting with no engagement | `agent_plan` visible per turn; per-space progress notes; the Kanban wall keeping *user* work items human-authored |

So the honest read is: TeamWork's differentiator — you can watch the work —
now has a measured mechanism behind it, not just a preference. The gap it
exposes is that **watching is optional in our UI and auto-accept is
frictionless**; the paper says defaults decide outcomes.

## The uncomfortable half: preference ≠ outcome

Participants preferred the agent *while reporting* they understood less. That
is a direct hit on how we read our own strongest signal. The
[grounding-gap](ilands-grounding-gap.md) row calls TJ's daily-driver box
"grounded selection at n=1" — a counterparty we don't control — and that
remains true for *did it work*. But this paper shows a specific way a
satisfied user is still the wrong oracle: **satisfaction and comprehension can
move in opposite directions**, and comprehension is the thing the safety
thesis depends on. Concretely, "TJ liked it" is evidence about utility, never
about whether oversight capacity was preserved. Those need separate signals.

This also sharpens the [proofjudge](proofjudge-taste.md) taste row: a
preference judge inherits this bias — it will reward the output that *feels*
better to accept, which is precisely the auto-accept affordance.

## What NOT to conclude

Not "add friction." Friction as a virtue is the wrong lesson from a study
whose own subjects rejected it; a harness that is annoying to use gets
replaced by one that isn't, and then oversight is zero. The design target is
**comprehension per unit of effort** — make the *understanding* path cheap
(readable diffs, a one-line why, the trace one click away), rather than making
the *acceptance* path expensive. Where genuine friction is warranted is
already scoped by risk tier, not applied uniformly.

## Honest limits

In-progress preprint, n=54 students on a website task — not professional
engineers on a large codebase, where prior comprehension and stakes differ.
"Agent edits your code" vs "chatbot you copy from" is a coarse contrast that
bundles several variables (who types, whether a diff is reviewed, how much is
generated at once), so the causal claim is about the *bundle*. Post-task
comprehension tests measure retention under exam conditions, which is a proxy
for the thing we care about (can you catch the agent when it is wrong). No
code released. Direction-of-effect is credible and matches the design
intuition; effect sizes should not be quoted as settled.

## Related

- [buzz-teamwork](../../../teamwork/docs/comparisons/) — the watch-the-agent
  differentiator this supports (TeamWork-side doc).
- [ilands-grounding-gap.md](ilands-grounding-gap.md) — grounded signal; this
  is the caveat that satisfaction ≠ preserved oversight.
- [proofjudge-taste.md](proofjudge-taste.md) — preference judging inherits the
  same bias.
- [crux-shadow-evals.md](crux-shadow-evals.md) — the agent-side twin: agents
  that can't calibrate their own success need humans who still can check.
