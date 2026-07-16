# ARC-AGI-3 & the "Schema" harness — the interactive-reasoning eval, and how Prax legitimately attacks it

**Sources.** ARC-AGI-3 benchmark ([arcprize.org/arc-agi/3](https://arcprize.org/arc-agi/3),
technical report [arXiv 2603.24621](https://arxiv.org/abs/2603.24621)); the
**Schema** harness ([schema-harness.github.io](https://schema-harness.github.io/),
[Haven Feng announcement](https://x.com/HavenFeng/status/2077770348876247502)); the
published traces ([hf.co/datasets/schema-harness/arc-agi-3-schema-traces](https://huggingface.co/datasets/schema-harness/arc-agi-3-schema-traces)).

**Verdict: document + adopt the *approach*, not the code.** ARC-AGI-3 is the
right north-star eval for what Prax is *for* (an agent that explores, models, and
acts), and the winning harness's core primitive — *write each game's mechanics as
an executable program, verify it against reality, plan inside it* — is something
Prax already has every foundational tool to do (sandbox + codegen + memory +
governed verify loop). Prax will score near-zero **until** an ARC-AGI-3
integration + a schema-induction loop exist; once they do, this is Prax's
wheelhouse, not a stretch. Build it **after** the standard-benchmark shakedown is
clean — it is the hardest eval we run and depends on the harness plumbing the
others are shaking out.

---

## 1. What ARC-AGI-3 actually is

Not the static grid puzzles of ARC-AGI-1/2. ARC-AGI-3 (ARC Prize, 2026) is the
first **interactive** reasoning benchmark: the agent is dropped into a novel
*game* with **no instructions, no stated goal, no rule sheet, and no shaped
reward**, and must figure out how the world works by playing.

- **Observation:** a **64×64 grid of 16 colour indices** (structured integers,
  not pixels — an agent reads the frame as an array).
- **Action space:** **7 standardized actions** — `RESET`, `ACTION1…ACTION6`, and
  `ACTION7`/Undo. That's it. The *meaning* of each action is game-specific and
  must be discovered.
- **What it tests** (the four capabilities): **Exploration** (act to gain
  information), **Modeling** (turn observations into a generalizable world
  model), **Goal-setting** (identify desirable states with no instruction), and
  **Planning & Execution** (path to a goal, course-correct on feedback).
- **Scoring — RHAE (Relative Human Action Efficiency, "ray"):** per level,
  `score = min(1.0, (human_baseline_actions / AI_actions)²)`. The human baseline
  is the **2nd-best of 10 human testers on first exposure**. The square makes it a
  power law: **brute-force exploration is punished hard; only genuinely learning
  the game earns a high score.** 100% means beating every public level as
  action-efficiently as a good human on first try. There is no way to grind it —
  wasted actions cost quadratically.

This is the cleanest possible test of the thing Prax claims to be: not a
question-answerer, but an agent that builds and acts on world models.

## 2. The Schema harness — "make the LLM think like a physicist"

Schema (Haven Feng et al.) is a **harness** — no weight changes — that reportedly
hits **99% RHAE with Opus 4.8 + Fable 5** and **95.35% with GPT-5.6 Sol** on the
**25-game ARC-AGI-3 public set**. The core idea:

> The model writes each game's mechanism **as an executable program**, tests that
> program against reality, and plans *inside* it. (The name is Kant's: a *schema*
> is "a rule of construction that bridges an abstract concept to concrete
> perception.")

Why executable: *"the executable harness reduces the cost of using a theory — it
makes the theory persistent, exactly verifiable, and searchable."* The loop is,
in effect:

1. **Perceive** the grid frame.
2. **Induce a schema** — hypothesize the hidden objects, state variables, and
   transition rules, and *write them as code*.
3. **Verify** — run the code forward, compare its predicted next-frame to the
   real one after an action; where they diverge, the schema is wrong → revise.
4. **Plan** — search the *verified world model* (the program) for the shortest
   action path to a goal state, then execute in the real game.

Their own framing of the key insight: *"search is only complete relative to the
world model it operates over. Once the missing object / state variable /
transition is represented, the existing verifier and planner are often enough."*
i.e. the hard part is **representation** (getting the schema right), not search.

**Honesty caveat (important):** the 99% is **self-reported and NOT verified by
ARC Prize**. Any Prax claim must be reproduced on the public set *and* submitted
to ARC Prize's verified leaderboard before we state a number.

## 3. What their published traces show (and the data cost)

The HF dataset (`schema-harness/arc-agi-3-schema-traces`):

- **50 gameplay trajectories** — 25 from GPT-5.6 Sol, 25 from Claude (Opus 4.8 +
  Fable 5) — one per public game per model collection.
- **768 MB total**, native **JSONL event streams** (auto-converted to Parquet).
  Each trace = `run.json` (metadata) + `events.jsonl` (streamed gameplay events)
  + session/level snapshots + shareable text/image outputs.
- **It's outcome/efficiency data, not reasoning.** It records **action sequences,
  per-level action counts, level completions, and the RHAE score** — *not* the
  model's intermediate reasoning or the induced schema programs. The 768 MB is
  heavy precisely because it stores per-level **image snapshots**.

So "admire their transparency" is right — but it's *shallow* transparency:
you see *what* actions were taken and how efficient they were, not *why* or the
world-model the agent built. **Prax can do better here** (see §5).

## 4. Where Prax stands — honest

**Today: near-zero.** Prax has no ARC-AGI-3 API client, no game loop, no
schema-induction harness. Dropped into a game it would flail — it's built for
tool-use over language tasks, not a 7-action game controller with no instructions.

**But every primitive the winning approach needs, Prax already has as a
first-class, *governed* tool** — which is exactly why TJ's read ("not great, but
foundational tooling to do amazing") is correct:

| Schema-harness primitive | Prax tooling that already does it |
|---|---|
| Write the game mechanism **as an executable program** | **Sandbox + codegen** (`run_python`, `sandbox_shell`, the codegen tools) — Prax writes and runs code as a matter of course |
| **Verify** the program against reality (predicted vs observed frame) | The same sandbox + Prax's verify-refine discipline; deterministic diff of two 64×64 arrays |
| Keep the theory **persistent & searchable** | **Two-layer memory** (persist the induced schema per game) + the world-model *is* a file in the workspace |
| **Explore → model → goal → plan → execute** loop | **Orchestrator + a dedicated spoke** running the loop; plan by simulating the verified world model |
| **Refine** on feedback | Prax's self-improvement / failure-driven retry machinery |
| Read the 64×64×16 frame | Structured integer grid — parse directly (no vision needed; `analyze_image` is a fallback) |

The gap is **integration + a loop**, not a missing fundamental capability. That's
a build, not a research bet.

## 5. How Prax legitimately gets to 100%

"Legitimately" is load-bearing and aligns perfectly with our **never-spike** rule:
the real eval games are **novel and hidden**, and **RHAE punishes brute force
quadratically** — so *the only path to a high score is a general schema-induction
loop that actually learns each game*. You cannot hardcode 25 solutions; a
benchmark-knower reading Prax's code must see a general game-learner, not answers.
The anti-spike incentive is baked into the metric.

The build, in order:

1. **ARC-AGI-3 client + a benchmark adapter** (`prax/eval/benchmarks/arc_agi_3.py`)
   — talk to the game API (reset / step / observe), deterministic RHAE scoring,
   real games cached under `PRAX_EVAL_DIR` (never committed). Flag-gated
   (`ARC_AGI3_ENABLED`), keyless-CI-safe with a tiny local mock game.
2. **A `game`/`arc` spoke** that runs the schema loop: perceive → induce schema
   *as a sandbox program* → verify prediction vs reality → plan inside the
   verified model → act → refine. Reuses the sandbox, codegen, and memory spokes
   we already have.
3. **Plan by simulation, not by acting** — search the verified world-model in the
   sandbox for the minimal action path *before* touching the real game. This is
   what turns a passing agent into an *efficient* (high-RHAE) one; it's also where
   Prax's "simulate in the sandbox" strength directly buys score.
4. **Persist schemas across levels/runs** — a game's early-level schema seeds
   later levels; memory makes Prax *continuously learn*, which is the exact
   capability ARC-AGI-3 rewards.
5. **Reproduce, then verify externally** — hit the public set first (target:
   match/beat the reported 99%), then **submit to ARC Prize's real leaderboard**.
   We never quote a self-reported number as verified.

**Transparency, done better than theirs (§3).** Prax already records execution
traces internally (`prax/agent/trace.py`, JSONL per run, Qdrant summaries). For
ARC-AGI-3 we can publish a HF traces dataset that includes what theirs omits:
**the reasoning, the induced schema program per game, and the verify/refine
history** — not just action counts. That's a stronger transparency claim and it's
on-brand for Prax's whole thesis.

- **Data cost is small.** Store frames as the raw 64×64 integer arrays (~4 KB
  each, gzips to almost nothing), not PNG snapshots. A ~100-action game ≈ a few
  hundred KB of events + the schema program + frames. 25 public games × a few
  seeds ≈ **tens of MB text-only, low-hundreds of MB with frames** — well under
  their 768 MB, and far richer. Full traces go to a **public HF dataset** (the
  public games are already public, so no contamination of the *hidden* eval);
  the in-repo record stays **aggregates-only** per the contamination firewall
  (see [`docs/guides/eval-matrix.md`](../guides/eval-matrix.md)).

## 6. Why this is the right "big" eval — and the sequencing

ARC-AGI-3 measures the thesis (explore/model/plan/act + continuous learning) that
GSM8K/MMLU/GPQA can't touch. It's also where Prax's least-exercised strengths —
**sandbox-as-world-model-simulator, codegen, persistent memory** — become the
*whole game*. It connects directly to the self-improvement through-line
([aide2](aide2-recursive-self-improvement.md), IDEAS_BACKLOG #29): the schema loop
*is* a verify-refine-persist loop over an executable artifact.

**Earn it first.** This depends on the exact harness plumbing (isolation,
timeouts, config-snapshot reproducibility, real-dataset caching) that the
standard-benchmark shakedown is hardening right now. Build the ARC-AGI-3 adapter +
schema spoke **once those run clean end-to-end** — then this becomes the flagship
of the eventual publish-whether-good-or-bad run.

## Takeaways

1. **ARC-AGI-3 is the eval that matches Prax's thesis** — interactive, no
   instructions, world-model-and-act, scored on human-relative *efficiency* (RHAE),
   not accuracy.
2. **The winning primitive is Prax's wheelhouse** — "write the game's mechanics as
   an executable program, verify, plan inside it" = sandbox + codegen + memory +
   verify loop, all of which Prax already has as governed tools.
3. **The metric enforces honesty** — hidden games + quadratic brute-force penalty
   mean the only route is a *general* schema learner; spiking is structurally
   impossible.
4. **We can out-transparency the leaders** — publish the reasoning + the induced
   schema programs, not just action counts; the data cost is sub-GB.
5. **99% is self-reported** — reproduce on public, then submit to ARC Prize's
   verified leaderboard before claiming anything.
