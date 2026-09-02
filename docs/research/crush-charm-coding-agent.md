# Crush (Charm) — the polished terminal coding agent, and what Prax should take from it

**Source:** <https://github.com/charmbracelet/crush> (~27.2k stars at time of
review, 2026-08-08)
**Verdict:** **document — adopt three patterns, decline the product and its
config/licence model.**

Crush is Charm's agentic coding CLI: Go, a Bubble Tea TUI, multi-provider,
LSP-aware, MCP-capable, session-based. It is the most polished thing in its
category and it is **not** a competitor to Prax — it is a coding agent for one
developer at one terminal, where Prax is a governed multi-channel harness.
Comparing them head-on is a category error. But three of its choices are
directly useful, and one is a clean external illustration of the critique in
`fable_feedback.md`.

## The lineage matters, because Prax has already touched it

Crush **is** the original `opencode`, continued at Charm with the original
author; `sst/opencode` (now `anomalyco/opencode`) is a fork of that earlier
work. Prax's sandbox once bundled OpenCode and removed it (PR #142). So this is
not a new arrival to evaluate from zero — it is the same lineage, matured, and
the removal decision still holds: Prax's sandbox needs an *execution* surface,
not a second agent with its own opinions about models, sessions and permissions.

## What Crush is, precisely

| Dimension | Crush |
|---|---|
| Language / UI | Go; TUI, plus `crush serve` for shared workspaces at the same `--cwd` |
| Providers | OpenAI, Anthropic, Gemini, Groq built in; `openai-compat` / Anthropic-compatible custom; **local discovery** for Ollama, llama.cpp, LM Studio, litellm |
| Provider registry | **Catwalk** — an open-source registry, updated dynamically |
| Permissions | approve-per-tool, `permissions allow/deny`, blanket `--yolo` bypass |
| Code context | **LSP** — "Crush uses LSPs for additional context, just like you do" |
| MCP | client across `stdio` / `http` / `sse`, with OAuth (dynamic + pre-registered) |
| Extensibility | **Agent Skills** (`SKILL.md`), `.agents/skills` + `.crush/skills`, `user-invocable: true`; preliminary hooks |
| Config | Bash-based `crushrc` (JSON deprecated) |
| Licence | **FSL-1.1-MIT** — source-available, converts to MIT after a delay |

## Adopt 1 — LSP as a context source for the coding lane (strongest fit)

Prax navigates code with grep, AST search (`code_search_ast`) and the sandbox.
An LSP gives *resolved* symbols: definitions, references, types, diagnostics —
ground truth from a language server rather than a textual guess.

This is the house rule "verifiable beats judgeable" applied to code navigation.
An agent that asks the language server "where is this defined" cannot
hallucinate the answer; an agent that greps for a name can, and does, when the
name is common. It is the same argument that put deterministic checks in the
capability suite instead of a judge.

Placement is `prax-sandbox` (the language servers are toolchain, and the
sandbox already carries toolchains — this is the `lean_check` shape from the
cdc-lean assessment, which shipped), consumed through a governed tool. It is a
real capability gap, not a nicety: it is the difference between Prax reading a
repo and Prax *understanding* one.

## Adopt 2 — Agent Skills as packages (second sighting)

Crush implements the `SKILL.md` Agent Skills standard, with project-local and
global paths and `user-invocable: true`.

This is the **second** independent sighting of skills-as-packages as something
Prax lacks — the Prime Agent assessment named it as one of three real gaps.
Prax has plugins (with a capability gateway and trust tiers, which is the
*better* security model) but no lightweight, user-authorable, discoverable unit
of "here is how to do this task in my repo". Plugins are the right shape for
credentialed capability; they are heavyweight for procedural knowledge.

Two sightings is the threshold at which a gap stops being one project's opinion.
Queued rather than started, and the design question to settle first is the one
Prax always asks: **a skill is untrusted content that shapes behaviour**, so it
must arrive tainted and must not be able to widen a capability ceiling. Crush
has `option disable-skill`; Prax would need the inverse posture — skills
constrained by the existing gateway, not merely disableable.

## Adopt 3 — local model endpoint auto-discovery (cheap)

Crush discovers Ollama, llama.cpp, LM Studio and litellm endpoints
automatically. Prax makes you set `OPENAI_BASE_URL` and friends by hand, which
is exactly the friction the SIE / local-inference assessment was about. This is
a small, self-contained UX win for the local-model audience with no
architectural consequence. Lowest priority of the three, and the least
interesting — but the cheapest.

## Decline 1 — the `--yolo` flag, and why it is a useful mirror

Crush's permission model is approve-per-tool with a single flag that turns the
whole thing off. That is a **perimeter**: correct until crossed, then absent.

This is the critique `fable_feedback.md` levels at Prax itself
(perimeter-only trust as one of two root causes behind its eight criticals),
arriving from outside and in a much starker form. Prax's tiered model — risk
classification per tool, audit on every call, the lethal-trifecta guard, HIGH
never grantable to an MCP caller — is the better shape, and seeing a 27k-star
project ship a one-flag bypass is corroboration that the tiered design is worth
its cost rather than over-engineering.

The honest caveat: Prax has bypasses too, they are just spelled as flags rather
than `--yolo`. The difference is that flipping one does not disable the audit
trail. That difference is the whole thesis, and it is only true as long as it
stays true — worth a check the next time the governance layer is touched.

## Decline 2 — Bash-executable config

`crushrc` is a Bash script. For a single-developer terminal tool that is
ergonomic. For Prax it would be a straight downgrade: executable configuration
is arbitrary code execution at startup, and Prax's Pydantic settings give typed
validation, a documented env alias per field, and a snapshot in every eval run's
`config` block (which is how the 180s timeout in the 2026-08-07 capability run
was traced to a deliberate choice rather than a bug). Keep the boring config.

## Decline 3 — FSL licensing, and note the tension in Catwalk

FSL-1.1-MIT is source-available, not open source, until it converts. Prax is
Apache-2.0 and its safety claim rests on being auditable by anyone, so this is
not a model to copy.

**Catwalk** — the dynamically-updated provider registry — is more interesting
and cuts both ways. Prax deliberately went the other direction:
`prax/services/credential_registry.py` is a static, in-repo, single source of
truth with a **drift-guard test that fails CI** if a credential is added to
`settings.py` without a registry row. A registry that updates itself from the
network is a supply-chain surface pointed directly at the credential path, and
Prax's version is the one you want when the thing being registered is where the
keys go. The genuinely good idea in Catwalk is *community-maintained provider
metadata*; the part to decline is fetching it at runtime.

## The result that reframes this whole assessment

After the above was drafted, TJ supplied a measurement (Joël Niklaus,
[@joelniklaus, 2026-08-07](https://x.com/joelniklaus/status/2085725862142623875))
that puts Crush in a very different light: **10 coding-agent harnesses run
against two models on the same 250 SWE-bench Pro tasks**, one rollout each,
priced at list rates for tokens actually spent.

Reported:

- Harness swing on one model: pass@1 **23% → 52%** (GLM-5.2), **15% → 36%**
  (Gemma 4 26B-A4B). Wider than most model releases buy.
- **The ranking does not transfer** — rank correlation between the two models'
  harness leaderboards ≈ **−0.05**.
- Every **model-vendor** harness drops on the small model (Codex 2nd→9th,
  Claude Code 3rd→7th, Qwen Code 4th→6th); every **model-agnostic** one climbs
  (crush 7th→**1st**, opencode 8th→2nd, pi 9th→4th).
- Gemma 4 + crush: 36% at **$0.30/task**; GLM-5.2 at $3.61/task. Cost per
  *solved* task $0.84 vs $7.05 for the cheapest GLM setup scoring as well.
- Output tokens per task span **16k to 621k** — a 39× cost spread buying a 2×
  outcome spread.
- **97% of input tokens are re-sent conversation prefix.**

**Verification status: NOT independently verified.** I could not find the
underlying study; the numbers above are from the post. The *direction* is
corroborated by independent sources — SWE-bench Pro shows a ~22-point swing
from scaffold changes alone, and Claude Opus 4.5 scores ~45.9% in a
standardised independent run against ~49.8–51.8% in vendor harness
comparisons. Treat the direction as solid and the specific figures as
unconfirmed. (The
[Agent Harness survey](agent-harness-engineering-survey.md) lesson applies: a third-party
summary invented numbers once already.)

The weakest claim is the one that sounds strongest. A rank correlation of
−0.05 over **10 harnesses at one rollout each** is barely estimable: at ~36%
pass rate on 250 tasks the per-cell 95% interval is roughly ±6 points, which is
comparable to the gaps between adjacent ranks. "The ranking does not transfer"
is plausible and consistent with the vendor/agnostic split, but this design
cannot establish it. Per
[Harness Generalization](harness-generalization.md), significance
testing belongs before an eval win is declared.

### What it means for Prax

**This is the strongest external evidence yet for the thesis Prax is built on.**
"Harness contribution rivals model choice" is exactly what `harness_lift`
exists to measure, and what "fly like a bird" asserts. Prax should stop
treating that claim as a conviction and start treating it as a finding with
citations.

**And it reframes Crush.** Crush ranks 7th on the large model and 1st on the
small one. Read through the adopt-list above, its model-agnostic design is not
merely a licensing-adjacent detail — it is plausibly *why* it wins where it
wins, which raises the value of Adopt 3 (local endpoint discovery) for the
local-model audience specifically.

**The mechanism, as a hypothesis:** scaffolding density trades against model
capability. A vendor harness can assume its own model's instruction-following
fidelity and give it rope; a model-agnostic harness substitutes structure for
capability. Structure rescues a weak model and *taxes* a strong one, by
overriding a better plan and spending tokens to do it. Prax has already banked
two independent predictions of this shape: "adaptive simplification — delete
scaffolding as models improve" ([Agent Harness survey](agent-harness-engineering-survey.md))
and Weng's finding that **middle-tier** models benefit most from harnesses
([Weng](weng-harness-engineering.md)).

That makes it a genuine, pre-registerable prediction rather than a
post-hoc story, and Prax owns the instrument to test it — `harness_lift` is
per-model, per-case, and nobody in that comparison computed it. Tracked as
**task #60**: run harness-lift at two tiers over the 30-case suite toggling
scaffolding flags component-wise, and ask one question — *does the sign of the
lift flip between tiers?* If nothing flips, the idea is dead cheaply. Only if
it flips is there a per-tier scaffolding profile worth building, and it must
then be validated on held-out cases or it is overfitting with extra steps.

### Adopt 4 — prompt caching (immediate, and the largest single number here)

"97% of input tokens are re-sent conversation prefix" is checkable against our
own code, and the check is damning: **`prax/` contains no `cache_control`, no
ephemeral cache blocks, no caching header anywhere.** Every tool-calling round
re-sends the system prompt, the tool definitions and the whole accumulated
conversation at full price. The 2026-08-07 capability run that spent 727,550
tokens in a single turn made ~29 orchestrator-level calls to do it.

Provider-dependent, and the distinction decides the size of the win:
OpenAI-compatible endpoints generally cache prefixes automatically server-side
(so Prax may already benefit — *measure, don't assume*), while Anthropic
requires explicit `cache_control` breakpoints that Prax never emits. The
secrets-proxy must also be confirmed not to strip them.

Tracked as **task #59**. Expected shape of the win is **cost, not
correctness** — and the 39× token spread for a 2× outcome spread above is the
reminder that cost is the axis where harness choices actually differ most.

## What this does not change

Nothing about Prax's architecture. Crush validates the shared-workspace idea
(`crush serve`, multiple clients at one `--cwd`) that TeamWork already
implements more fully, and it confirms MCP as table stakes, which Prax shipped.
There is no orchestration, no memory layer, no governance story to learn from —
Crush does not attempt them, and is better for it.

The reason to read it is that Charm are excellent at the part Prax is weakest
at: making the thing pleasant to use. That is not an adopt-item, it is a
standing reminder.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Prompt caching — Prax emits NO cache markers; ~97% of input tokens are re-sent prefix** | 📋 task #59 |
| **Adaptive per-tier scaffolding profiles learned from `harness_lift`** (does the lift's SIGN flip between a small and a large model?) | 📋 task #60 |
| LSP as a context source for the coding lane (in `prax-sandbox`, governed tool) | 📋 queued |
| Agent Skills as packages — 2nd sighting (Prime Agent was 1st); must arrive tainted + gateway-constrained | 📋 queued |
| Local model endpoint auto-discovery (Ollama / llama.cpp / LM Studio / litellm) | 📋 queued |
