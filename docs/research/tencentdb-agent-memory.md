# TencentDB Agent Memory — layered memory with a guaranteed drill-down path

**Source:** [TencentCloud/TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory)
**Repo state at assessment (2026-08-07):** TypeScript, **MIT** (the LICENSE file
says MIT under a Tencent preamble — GitHub's API reports `NOASSERTION`, which is
a parsing artefact, not a restriction), **16.3k stars / 1.5k forks**, created
2026-04-07, pushed the day before this assessment, **526 open issues**.
**Assessed:** 2026-08-07

**Verdict: document + adopt TWO, note ONE candidate, decline the integration.**
The headline contribution is not the layer model — it is an **architectural
invariant**: every abstraction must retain a deterministic pointer back to the
raw evidence it was derived from. That is
[PRO-LONG](prolong-programmatic-memory.md)'s "compact the context, never the
record" arrived at independently **two days apart**, and *mechanised*. Applying
it to Prax found a precise defect in shipped code: our progress layer carries
that pointer at one level and **destroys it during compaction** at the next.

---

## What it is

A team-level memory hub that turns conversations, docs and code into four
reusable assets (Chat Memory, Skills, LLM-Wiki, Code-Graph), consumed as a
plugin by *other* harnesses — OpenClaw, Hermes, Claude Code, CodeBuddy. Two
pillars:

**1. Memory layering (long-term).** A semantic pyramid rather than a flat vector
pile: **L0 Conversation** (raw dialogue) → **L1 Atom** (atomic facts) → **L2
Scenario** (scene blocks) → **L3 Persona** (user profile). The Persona layer
carries day-to-day preferences; the system "drills down to Atoms only when
details matter." Artifacts are plain files on disk (`persona.md`, Markdown
Scenario blocks) and explicitly meant to be opened and inspected.

**2. Symbolic memory (short-term).** Full tool logs are offloaded to
`refs/*.md`; step-level summaries go to `jsonl`; only a **Mermaid canvas**
encoding task state stays in context, and the agent drills down "via `node_id`
when an error occurs."

## The idea worth taking: the drill-down invariant

Stated plainly in their README:

> "Compression often sacrifices traceability. TencentDB Agent Memory avoids
> irreversible compression by maintaining a deterministic path from high-level
> abstractions back to ground-truth evidence … the system guarantees a complete
> drill-down path: top-layer symbol (Persona / canvas) → mid-layer index
> (Scenario / jsonl) → bottom-layer raw text (L0 Conversation / refs)."

This is a **stronger claim than PRO-LONG's**, and complementary to it. PRO-LONG
says *keep the whole record and search it with code*. TDAM says *you may
abstract, provided every abstraction carries a pointer home.* Search is
probabilistic; **dereference is deterministic**. Two independent sources in one
week converging on "the record is sacred" moves this from a lens to a rule I'd
now design against.

## Adopt 1 — the concrete defect it found in `progress_service`

Yesterday's [PRO-LONG](prolong-programmatic-memory.md) note said `.progress/`
detail files are "date-addressed only." Reading the code against TDAM's
invariant gives a sharper and partly different answer, in two layers:

- **Recent-sessions layer: the pointer EXISTS but the retrieval tool ignores
  it.** `append_progress` writes the bullet as
  `{date} · {outcome} · {short_id}` and the detail file as
  `.progress/{date}-{short_id}.md` — so every recent bullet already names its
  own evidence. But `progress_detail(space_slug, date)` takes only a **date**
  and concatenates *every* session file matching it. The pointer is written and
  then thrown away at read time.
- **Archive layer: the pointer is DESTROYED.** When the file exceeds its cap,
  `_compact` folds the five oldest bullets into a single Archive paragraph via
  a LOW-tier LLM summariser. The `short_id`s do not survive that rewrite. So the
  detail files remain on disk (correctly — compaction never re-reads or deletes
  them) and become **unaddressable at exactly the moment the summary becomes
  the only thing in context.** That is the "irreversible compression" TDAM
  names, in our code.

**The fix is smaller than a search tool and strictly better**: make compaction
carry the `short_id`s of the entries it folded into the Archive paragraph, and
let `progress_detail` accept a `short_id` as well as a date. Search
(task #44) is still worth having for the case where you don't know *any*
pointer — but a preserved pointer beats a search every time, and preserving it
costs nothing.

## Adopt 2 — conflict detection at consolidation is a real component, and theirs is the complement of ours

Their `extraction.enableDedup` (default **true**) does "L1 vector dedup /
conflict detection." Yesterday Prax shipped a *symbolic* consistency pass
(`memory/consistency.py`): for declared single-valued relation types, the graph
is queried for a conflicting current edge.

These are complements, and the comparison is clarifying:

| | our symbolic check | their vector dedup |
|---|---|---|
| catches | structural conflicts (one current `lives_in`) | semantic conflicts (dark mode vs light mode) |
| basis | schema — deterministic, no threshold | embedding similarity — needs a threshold |
| false-positive cost | a wrongly-declared type deletes facts | a low threshold merges distinct facts |

Ours deliberately **cannot** see the dark-mode/light-mode case (both are
`prefers` edges; exclusivity is world knowledge). Theirs cannot be deterministic.
Wanting both eventually is the right conclusion — and that an independent team
ships conflict detection **on by default** is evidence the component earns its
place rather than being over-engineering. Sequenced behind our log-only
shakedown, not before it.

## Noted candidate, not adopted — the Mermaid canvas

Encoding in-task state as a compact Mermaid graph with `node_id` handles for
drill-down is a genuinely neat instance of context-offloading (third sighting
after [RLM/LID](rlm-harness-lid.md) and [ACM](acm-agentic-context-management.md),
and the first with a concrete encoding). But it is a substantial feature with a
real eval question, and Prax's equivalent surface (`agent_plan`, trace spans)
already carries some of this. Candidate; not a decision.

## Their numbers, and how much to lean on them

| capability | benchmark | baseline | with plugin | Δ | tokens Δ |
|---|---|---|---|---|---|
| short-term | WideSearch | 33% | 50% | +51.5% rel | −61.4% |
| short-term | SWE-bench | 58.4% | 64.2% | +9.9% rel | −33.1% |
| short-term | AA-LCR | 44.0% | 47.5% | +8.0% rel | −31.0% |
| long-term | PersonaMem | 48% | 76% | +59% rel | — |

**What is good about this**: they measure over *continuous long-horizon
sessions* — "SWE-bench runs 50 consecutive tasks per session to simulate the
context-accumulation pressure of real-world long-horizon agents" — which is the
correct frame for a memory system and better than most memory papers manage.
Reporting tokens *and* pass rate is also right; a memory system that improves
accuracy while inflating context has not obviously won.

**What to discount**: vendor self-reported, **no paper**, no seeds, no n, no
confidence intervals. The baseline is OpenClaw, a harness we do not run, so none
of it transfers to Prax as a prediction. Per
[saturation](benchmark-saturation.md), SWE-bench is exactly the widely-adopted
vintage flagged as saturated — and +9.9% relative off 58.4% is the kind of delta
that needs CIs to mean anything. The PersonaMem jump is the most interesting
number *and* the least verifiable.

## Declines

- **Do not integrate it.** It is a plugin for other harnesses with an HTTP
  gateway, not a library — and its **security posture is the inverse of ours**.
  The Hermes Gateway's two auth switches "**default to off**," i.e. an "open
  localhost sidecar," and the README is explicit that the plugin "only handles
  the client half" — gateway enforcement is configured separately, on both ends,
  with the secret not propagated. Prax's stance (fail-closed, MCP mounts only
  when a client is configured, capability gateway in front) is deliberately the
  opposite. Same call as [Prime Agent](prime-agent.md): learn from it, don't
  adopt its perimeter.
- **Note the cloud defaults** (neutrally): `MODEL_BASE_URL` defaults to Tencent
  Cloud LKE and the shipped image defaults to Tencent Cloud DeepSeek-V3.2. A
  local SQLite backend and on-disk artifacts exist, so self-hosting is real —
  but the defaults route outward, which matters for a keyless-by-design harness.
- **"Skills" is thinner than the headline suggests.** Skills are listed as one
  of the four assets, but the roadmap's **"Automatic Skill generation" is
  unchecked**. So the [Prime Agent](prime-agent.md) skills-as-packages gap is
  *not* closed here either — it remains open in both systems, which is itself
  worth knowing.

## Honest limits of this assessment

- README-only; no paper, and I ran none of it. Everything above about their
  system is their own description.
- 526 open issues against 16.3k stars is a lot of unresolved surface for a
  four-month-old repo — popularity here is not evidence of maturity.
- The two adopts are about **Prax's** code and hold regardless of whether their
  numbers replicate. That is deliberate: the transferable part is the invariant,
  not the benchmark.
