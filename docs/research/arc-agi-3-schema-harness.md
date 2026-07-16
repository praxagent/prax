# ARC-AGI-3 & the "Schema" harness — the interactive-reasoning eval, and how Prax legitimately attacks it

**Sources.** ARC-AGI-3 benchmark ([arcprize.org/arc-agi/3](https://arcprize.org/arc-agi/3),
technical report [arXiv 2603.24621](https://arxiv.org/abs/2603.24621)); the
**[ARC Prize 2026 competition](https://arcprize.org/competitions/2026/arc-agi-3)**
($850K pool, $700K for 100%, Kaggle, offline eval); the
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

**Honesty caveat (important, and stronger than first written).** The 99% is a
**self-reported website number**, and a 2026-07-16 deep-research pass
([executable-world-models.md](executable-world-models.md) §4) sharpens the doubt:
the closest *primary* source, the arXiv paper the site relates to
([2605.05138](https://arxiv.org/abs/2605.05138), released `arc-3-agents-baseline1`),
reports **~58% RHAE** on the public games (GPT-5.5), not 99% — and the review could
not verify the "Schema" name in that paper's text. Meanwhile ARC Prize's
**verified** ARC-AGI-3 frontier is **7.78% semi-private** (GPT-5.6 Sol). So the
real field is single-digit-% and **wide open**; treat 99% as unverified and likely
not reproducible on the private set. Any Prax claim gets reproduced on public *and*
submitted to the verified leaderboard before we state a number. **See
[executable-world-models.md](executable-world-models.md) for the full field
foundation** (paradigm map, the verifier-centric build stack, the offline
small-model playbook + model shortlist, `re-arc` generators, and the FunSearch/
AlphaEvolve recursive-loop template).

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
| **Explore → model → goal → plan → execute** loop | **Orchestrator + a general model-induction capability** (§5) running the loop; plan by simulating the verified world model |
| **Refine** on feedback | Prax's self-improvement / failure-driven retry machinery |
| Read the 64×64×16 frame | Structured integer grid — parse directly (no vision needed; `analyze_image` is a fallback) |

The gap is **integration + a loop**, not a missing fundamental capability. That's
a build, not a research bet.

## 5. How Prax legitimately gets to 100%

"Legitimately" is load-bearing and aligns perfectly with our **never-spike** rule:
the real eval games are **novel and hidden**, and **RHAE punishes brute force
quadratically** — so *the only path to a high score is a general schema-induction
loop that actually learns each game*. You cannot hardcode 25 solutions; a
benchmark-knower reading Prax's code must see a **general model-induction
capability** (next), not an ARC solver. The anti-spike incentive is baked into the
metric *and* into how we build the feature.

### The feature must be general — ARC is a *benchmark of it*, not its purpose

A "game spoke" built to play ARC-AGI-3 would be the spiking anti-pattern in
architecture form: special-case code that only earns its keep on one eval. The
never-spike rule applies to *features*, not just prompts — **a reader of Prax's
code must see a general capability that ARC happens to exercise, not an ARC
solver.** Fortunately the general capability is real, obvious, and broadly useful:

> **Executable world-models** (working name — a general `modeling`/`simulate`
> capability, naturally an extension of the **sandbox**): *given an unknown system
> Prax can observe or interact with, induce an executable model of its rules,
> verify the model against evidence, and use it to plan or answer.* The scientific
> method as an agent loop — hypotheses expressed as **runnable, falsifiable code**,
> kept only when they survive verification.

Its daily, non-ARC uses are exactly Prax's real work:
- **Onboarding an unknown API / tool / CLI** — probe it, build an executable model
  of its contract and behavior, verify, then use it reliably (huge for the plugin
  and tool ecosystem).
- **Empirical debugging** — express the suspected fault as code, verify it
  reproduces the bug, then fix against a confirmed model.
- **Simulation-based planning** — build a runnable model of a task environment and
  test a plan in it *before* acting on the costly/irreversible real system.
- **Rule/mechanism discovery from data or examples** — induce the generating rule
  as code, verify it reproduces the observations.
- **Any abstract-reasoning task** — induce rule → verify → apply.

**The two ARC competitions are two benchmarks of this one capability** — which is
exactly why doing both is coherent, not two projects:

| | ARC-AGI-2 (static) | ARC-AGI-3 (interactive) |
|---|---|---|
| Evidence | input→output example pairs | interaction with a live game |
| Induce | the **transformation** as code | the **world model** as code |
| Verify | reproduces all training pairs | predicts the next frame after an action |
| Use | apply to the test grid | plan a minimal action path, act |
| Score | exact-grid match, 2 attempts | RHAE (action efficiency) |

Same loop — *induce an executable rule from evidence, verify, apply* — pointed at
static evidence vs interactive evidence. Build the **capability** once; the two
adapters are thin.

### The build, in order

1. **The capability first, ARC-agnostic** — the `modeling`/`simulate` capability
   (induce-as-code → verify → plan/apply), reusing the existing sandbox, codegen,
   and memory spokes. Land it with a **non-ARC** demo (e.g. reverse-engineer a
   mock API's behavior) so its generality is visible in the codebase, not implied.
2. **ARC-AGI-2 adapter** (`prax/eval/benchmarks/arc_agi_2.py`) — the *easier first*
   integration: static, deterministic **exact-grid-match** scoring (2 attempts),
   fits the existing benchmark-adapter mold (like GSM8K/MMLU). Validates the
   capability cheaply and keyless-safely before anything interactive.
3. **ARC-AGI-3 adapter + client** (`prax/eval/benchmarks/arc_agi_3.py`) — the game
   API (reset/step/observe), deterministic RHAE scoring, real games cached under
   `PRAX_EVAL_DIR`, flag-gated, keyless-safe with a tiny mock game. It's the *same*
   capability driving an interactive loop.
4. **Plan by simulation, not by acting** — search the verified world-model in the
   sandbox for the minimal action path *before* touching the real game. This turns
   a passing agent into an *efficient* (high-RHAE) one — where Prax's
   "simulate in the sandbox" strength directly buys score.
5. **Persist models across runs** — memory makes Prax *continuously learn*, the
   exact capability ARC-AGI-3 rewards (and useful everywhere else the capability
   is used).
6. **Reproduce, then verify externally** — public set first, then **submit to ARC
   Prize's real leaderboard / Kaggle**; never quote a self-reported number as
   verified.

Adapters live in the **eval engine** (where every benchmark's adapter lives —
that's measurement, not special-casing); the **capability** lives as a general
Prax feature with its non-ARC uses front and center.

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

## 6. The 2026 competition — and the constraint that changes everything

[ARC Prize 2026](https://arcprize.org/competitions/2026) runs **two** competitions
on two benchmarks, **$2M total**, both offline on Kaggle, both open-source-required:

**[ARC-AGI-3](https://arcprize.org/competitions/2026/arc-agi-3) (interactive):**
- **$850K. Grand Prize: $700K for the first eligible agent scoring 100%.** That is
  the literal answer to "how does Prax get 100%." Plus $75K Top-Score (5 places)
  and $75K milestones. Milestone #2 is **Sept 30, 2026**.

**[ARC-AGI-2](https://arcprize.org/competitions/2026/arc-agi-2) (static — the
classic format):**
- Grid input→output puzzles unseen in training; output **2 attempts, exact-grid
  match** = 1/0. **Grand Prize guaranteed to the best open-source solution that
  reaches 85%** on the private set within Kaggle efficiency limits. 85% is
  *brutal* (frontier models sit far below human ~60%+), so expect a low Prax score
  — but it's a legitimate hard benchmark and the **easier first integration**
  (deterministic, keyless-CI-friendly, fits the existing adapter mold).

Shared rules that shape the design:
- **Submit via Kaggle; all submitter code must be open-sourced** — ARC-AGI-2
  demands a **permissive public-domain licence (CC0 or MIT-0)** for the
  *submission*, so a prize entry is released under MIT-0/CC0 even though Prax core
  is Apache-2.0 (a licensing note, not a blocker).
- **⚠️ NO internet access during evaluation.** This is the load-bearing rule.
- **✅ Iteration and test-time code generation are allowed and expected — it is
  NOT one-shot.** This is the rule that makes Prax's whole verify-refine loop
  *legal and rewarded*, not forbidden:
  - **ARC-AGI-2:** internal computation is unbounded (within the runtime/compute
    limit) — you may write, run, verify-against-the-given-train-pairs, and *evolve*
    candidate solution programs as much as you like; only the **2 final grid
    attempts** (pass@2) are submitted. The public SOTA does exactly this —
    [Imbue's "code evolution"](https://imbue.com/blog/2026-02-27-arc-agi-2-evolution)
    keeps a *population* of Python candidate programs and mutates them until one
    reproduces the train pairs. (Prax already has a **self-fixing / self-improvement**
    precursor — the `Self-Fixing` prompt section, `review_my_traces`, the codegen
    `self_improve` path — so this is sharpening an existing instinct into a tight,
    verifier-driven loop, not a new one.)
  - **ARC-AGI-3:** iteration is built into the action space — **`RESET` restarts a
    level and `ACTION7`/Undo reverses a step**, and critically **a RESET clears the
    level's action sequence, so resets don't count against your RHAE**. You can
    explore, hit a wall, reset, and retry for free; only the efficiency of your
    *final successful traversal* is scored.
  - The limit is **compute/runtime + no internet**, not iteration. "Modify its own
    code on the fly and redo" = generate/run/refine solution code at test time =
    the *winning* strategy; what you can't do is fetch external code or models
    mid-eval. Prax's codegen + sandbox + verify-loop is built for exactly this.

**What "no internet" does to the plan.** The submitted agent runs **offline** in
Kaggle's sandbox under (TBD) compute limits. So:

1. **It rules out API frontier models during the scored run.** The Schema
   harness's self-reported 99% used Opus 4.8 + Fable 5 *via API on the public
   set* — a **different, easier regime** than the offline competition eval. Be
   honest about this gap: reproducing 99% on public with API models ≠ winning the
   offline grand prize. The grand prize needs a model that runs **locally on
   Kaggle hardware**.
2. **It makes the executable-schema approach the *only* viable one** — and it
   makes Prax's web/search tools worthless here (they'd be blocked). The score
   comes entirely from **offline reasoning + sandbox simulation**, which is
   exactly the self-contained loop Prax's sandbox+codegen provide. (It also means
   the [tool-economy](#) instinct — don't reach for tools you don't need — is
   *enforced by the rules*, not just good hygiene.)
3. **It splits Prax's attack into two honest targets:**
   - **(a) Public-set reproduction, API models** — validate the schema loop works
     at all; matches the schema-harness regime; the near-term, cheaper goal.
   - **(b) Competition grand-prize, offline model on Kaggle** — the $700K; needs
     Prax's schema loop driven by a **local/open model within compute limits**.
     This is where Prax's [local-inference](../guides/local-cpu-inference.md) and
     someday-finetune lanes stop being optional and become the whole game.

The build order below serves (a) first (it's how we learn the loop); (b) is the
moonshot that (a) de-risks.

## 7. How to prep — public data, our own generators, and the recursive loop

**Yes, there is public data to practice against — and the *right* use of it is the
key to not spiking.**

- **ARC-AGI-2** ships **public *training* and public *evaluation* task sets**
  (hundreds of input→output grid tasks, JSON, in the
  [arcprize/ARC-AGI](https://github.com/arcprize/ARC-AGI) repo) alongside the
  hidden semi-private/private sets used for prizes.
- **ARC-AGI-3** exposes a **public game set** (the ~25 games, playable/queryable
  via the ARC-AGI-3 API — [docs](https://docs.arcprize.org/),
  [arc3.games](https://arc3.games/)); the scored competition games are hidden.

Cache both under `PRAX_EVAL_DIR` (data-only, never committed — contamination
firewall).

### The contamination line — the whole game

The trap: **training/tuning Prax on the public *evaluation* tasks is spiking** —
the model memorizes answers and generalizes nothing (and it may overlap the hidden
set). So the discipline:

- **Develop** on the public *training* half.
- **Hold out** the public *evaluation* half as an untouched generalization check.
- **Never** put eval-half tasks (or the hidden sets) into training, prompts, or
  committed docs.
- The **real Kaggle leaderboard** is the final external check we never train against.

### Generate our own parallel tasks — the unlock TJ is pointing at

This is the correct, non-spiking way to get unlimited training/eval signal, and
ARC is *designed* for it — the whole ARC philosophy is "a handful of core-knowledge
priors → infinitely many novel tasks." So we build **our own generators**:

- **ARC-2 task generator** — synthesize novel input→output grid tasks from
  parameterized transformation rules (symmetry, gravity, object-counting,
  flood-fill, recolor-by-rule…), each with a *known* program so scoring is
  deterministic and we control difficulty.
- **ARC-3 mini-games** — build small interactive environments in the same
  64×64/7-action spirit with *known* mechanics, so we can score RHAE against our
  own "human" (optimal-path) baseline.

Our own tasks are **novel, unlimited, and contamination-free** — they train the
*general* "executable world-models" capability rather than memorized answers, and
they let us dial difficulty and measure generalization to shapes the public set
never showed.

### The recursive loop — this closes #29 with an un-gameable verifier

The reason this is exciting beyond ARC: a self-authored task generator + a
**deterministic verifier** (exact-grid-match for ARC-2, game-win/RHAE for ARC-3) is
exactly the **un-gameable fitness function** that self-regeneration
([#29](../IDEAS_BACKLOG.md), [aide2](aide2-recursive-self-improvement.md)) has been
missing. The loop:

1. **Generate** a batch of novel tasks (ours) + sample the public *training* half.
2. **Attempt** — Prax runs the executable-world-model loop (induce→verify→refine).
3. **Verify deterministically** — no LLM judge, no reward to hack; it either
   reproduces the grids / wins the game or it doesn't ([edge-bench](edge-bench-learning-curves.md)'s
   "verifiable beats judgeable").
4. **Select on held-out generalization** — keep a prompt/harness/capability change
   only if it improves the **held-out** score at equal-or-lower cost (aide2's
   public/private split), never the tasks it trained on.
5. **Improve and repeat** — this is #29's recursive loop, made safe precisely
   because the metric is deterministic and the selection is on unseen tasks.

That is the same self-improvement Prax already gestures at (`Self-Fixing`,
`review_my_traces`, self-regen) — but pointed at a domain where success is
*exactly checkable*, which is what makes closing the loop safe here first.

### Prep order (once the standard-benchmark shakedown is clean)

1. Build the **executable-world-models** capability (§5) with a **non-ARC** demo.
2. `arc_agi_2.py` adapter + cache the public sets → first real ARC number
   (train-half dev, eval-half held out).
3. **ARC-2 task generator** → start the recursive loop on self-generated tasks.
4. `arc_agi_3.py` adapter + the interactive loop; **ARC-3 mini-games** generator.
5. Reproduce on public → submit to the real leaderboard.

## 8. Why this is the right "big" eval — and the sequencing

ARC-AGI-3 measures the thesis (explore/model/plan/act + continuous learning) that
GSM8K/MMLU/GPQA can't touch. It's also where Prax's least-exercised strengths —
**sandbox-as-world-model-simulator, codegen, persistent memory** — become the
*whole game*. It connects directly to the self-improvement through-line
([aide2](aide2-recursive-self-improvement.md), IDEAS_BACKLOG #29): the schema loop
*is* a verify-refine-persist loop over an executable artifact, and the
self-generated-task recursive loop (§7) is the un-gameable fitness function #29
needs.

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
