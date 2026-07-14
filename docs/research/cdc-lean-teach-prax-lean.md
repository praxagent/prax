# cdc-lean (OpenAI) — "Can we teach Prax Lean?"

**Assessed:** 2026-07-12 (TJ dropped the link the week the repo went public).
**Source:** [github.com/openai/cdc-lean](https://github.com/openai/cdc-lean) + its README/VERIFICATION.md/source modules (fetched raw), the GitHub API, and press/community coverage of the associated announcement. Claims below were adversarially re-verified against the live repo; one recon claim was refuted and corrected in the process.

**Verdict: document + adopt the *pattern*, not the repo.** cdc-lean is a verification *artifact* (and unlicensed), not a library — there is nothing in its code for Prax to import. What Prax should take: (1) its **axiom-audit trust gate** as the template for a governed proof-checking tool, (2) a **sandbox Lean toolchain + `lean_check` tool** (flag-gated, spoke-internal), and (3) a **Lean eval adapter sliver** later. The finetune reading of "teach Prax Lean" is explicitly unrealistic today — say so plainly. Answer to TJ's question: **yes — as a capability (verify-and-iterate loop), buildable in ~1-2 days on existing rails; no — as "make Prax a competitive prover" (that's a specialized-RL-model game, not a harness game).**

> **UPDATE 2026-07-14 — Shape 1 SHIPPED.** The `lean_check` tool is built and **verified live** on known-result theorems (see the Verification Ledger). Lean 4.31.0 toolchain in the sandbox image (`ELAN_HOME=/opt/elan`, toolchain-only, no mathlib), the tool in `prax/agent/lean_tools.py` behind `LEAN_TOOLS_ENABLED` (default off), shipping the axiom-audit trust gate — a `sorry`-holed proof compiles but is correctly flagged, and an injected non-standard axiom is caught. Shapes 2 (eval adapter) and the mathlib extension remain the open follow-ups.

---

## What cdc-lean actually is

A 100%-Lean-4 repo under the OpenAI org that **kernel-checks an unconditional Cycle Double Cover theorem** — the classical ~50-year-open CDC Conjecture (Szekeres 1973 / Seymour 1979). The endpoint is:

```
theorem CDCLean.cycleDoubleCover_of_bridgeless {V E} [Fintype V] [Fintype E] …
    (G : FiniteGraph V E) (hb : G.Bridgeless) : Nonempty G.CycleDoubleCover
```

Source-level checks confirm the definitions are the real thing, not a weakening: `FiniteGraph` is a loopless multigraph with genuinely distinct edge objects; `Bridgeless` is the no-single-edge-cut characterization; a `Cycle` is a nonempty inclusion-minimal even edge set (genuine simple circuits, parallel-edge pairs count as 2-cycles); a `CycleDoubleCover` covers every edge exactly twice. Proof route: Jaeger–Kilpatrick eight-flow (nowhere-zero `(ZMod 2)^3` flow on a cubic expansion) converted into a double cover, with a Nash-Williams–Tutte tree-packing step (that last step evidenced by module name/docstring — `NashWilliams.lean` citing "Kaiser's elementary tree-packing argument" — not by the README).

It is the **formal companion to OpenAI's 2026-07-10 claim that GPT-5.6 "Sol Ultra"** (64 cooperating subagents, under an hour) proved the conjecture. The repo itself is silent on provenance — the only in-repo signal is the merged branch name `codex/unconditional-cycle-double-cover`; the proof PDF's statement of AI use ("entirely due to GPT 5.6 Sol Ultra") lives on OpenAI's CDN, not here.

**Maturity/trust, honestly:** created 2026-06-24, ~8 commits, 2 authors (org affiliation inferred from `-oai` logins, not stated), no CI (issue #3 asks for one), **no LICENSE** (all rights reserved by default), and `VERIFICATION.md`'s clean-build claim (1,727 jobs, no `sorry`/`admit`/`native_decide`/`unsafe`, only `propext`/`Classical.choice`/`Quot.sound`) is a **self-report with no independent reproduction found as of 2026-07-12**. Reception is contested: HN front page was split (elegance vs. "the heavily-engineered prompt did the steering" — the prompt includes "assume a proof exists" and adversarial verifier subagents; cost estimates $275–$13K); mathematician Thomas Bloom called it "a very nice proof… short, elementary, and could have been discovered in the 1980s" while criticizing a missing 1983 citation. Scope nuance: the kernel-checked statement covers **finite loopless bridgeless multigraphs**; loops/infinite cases rest on standard paper reductions. None of this undermines the *adoption* case below — but nothing Prax publishes should launder the self-reported audit as independently verified. Pinned toolchain: Lean v4.31.0, Mathlib commit `9a9483a…`.

## The one directly reusable thing: the audit pattern

cdc-lean operationalizes "don't trust a green build" as a two-step gate any harness can adopt wholesale:

1. `lake env lean CDCLean/Audit.lean` — `#print axioms` on the endpoint theorem; expected output is **exactly** the three standard axioms (`propext`, `Classical.choice`, `Quot.sound`).
2. A ripgrep sweep over `*.lean` for `sorry|admit|native_decide|axiom|opaque|unsafe`.

Compiling is necessary, not sufficient — the statement must mean what's claimed and depend on nothing exotic. This is the same "verifiable beats judgeable" doctrine Prax's eval engine already runs on (and the Lean kernel is the ultimate deterministic judge — cf. the pipeline-math entry in this README: the un-gameable-verifier pattern behind "breakthrough" results). A Prax `lean_check` tool should ship this gate from day one, not as a follow-up.

## What "teach Prax Lean" means — capability, not training

The 2026 shape of "an agent that knows Lean" is a **generate-verify loop**: the LLM writes proof text; the Lean compiler/kernel is the ground-truth judge; errors/`sorry` goals feed revision. (Whole-proof generate-compile-revise dominates — DeepSeek-Prover-V2, Kimina, Goedel-Prover, Seed-Prover; tactic-step REPL interaction à la lean-gym/LeanDojo is the finer grain.) The harness contribution is exactly four layers: pinned toolchain → lake build orchestration → interactive check loop → trust-audit gate. **Prax already has ~90% of the harness shape** — `sandbox_shell` can run arbitrary container commands today; the increment is the toolchain in the image plus a structured tool plus eval coverage.

Calibration: a `lean_check` tool makes Prax able to **verify and iterate** — including independently rebuilding and auditing cdc-lean itself, which nobody appears to have publicly done yet. It does **not** make Prax a competitive prover; Goedel/Kimina-class systems at 90%+ miniF2F are specialized RL-trained models with large sampling budgets. That's the honest boundary of the harness-lift thesis here.

## Adoption shapes, ranked

**Shape 1 — sandbox Lean toolchain + governed `lean_check` (recommended core; ~1-2 days).**
Image-level elan install in `../prax-sandbox/sandbox/Dockerfile` with `ELAN_HOME=/opt/elan` + symlinks into `/usr/local/bin` (the `/root` bind-mount rule forbids elan's `~/.elan` default; precedent: `/opt/prax-venv`). Mathlib via `lake exe cache get` into `/workspace` or a named volume — **never** built from source in-container, never baked into the image. Tool lands spoke-internal in the sandbox spoke (`prax/agent/spokes/sandbox/agent.py` `build_tools()`), shells `lake env lean <file>` through the sandbox client, returns structured pass/fail + extracted errors/goals, ships the audit gate above, auto-wrapped by `governed_tool.py` (MEDIUM risk), flag `LEAN_TOOLS_ENABLED` default-off. Orchestrator stays at ~42 tools.

**Shape 2 — eval benchmark adapter (cheap keyless half now, sandbox-scored half after Shape 1).**
One file `prax/eval/benchmarks/lean.py` on the `gsm8k.py` pattern: inline hand-verified seed cases + registry + key-free test. Split scoring: CI-safe deterministic checks keyless; ground-truth `lake env lean` scoring excluded from keyless CI like other sandbox-dependent tests. Full datasets (miniF2F Lean 4, PutnamBench, ProofNet) staged in `PRAX_EVAL_DIR`, never committed; reference proofs never quoted into committed docs/prompts (contamination firewall). "Never spike benchmarks" has teeth here: a fix for a failed Lean eval must abstract the problem class.

**Shape 3 — finetune angle: not a near-term option.**
The finetune spoke is a default-off LoRA loop for a *local* vLLM model, fed only by conversation-correction harvesting, GPU-presuming, and it never touches the frontier model doing Prax's reasoning. "LoRA a small open prover on harvested Lean attempts" is a someday-with-GPU backlog line, nothing more.

## Costs / risks (blunt)

- **Disk:** ~2-3 GB toolchain + ~5-8 GB mathlib olean cache. 93 GB free of 154 GB today — fits, but this box has a "container overlay IS the host disk" outage history (2026-07-08). Cache placement deliberate + monitored.
- **Build-time trap:** with the cloud cache, setup is tens of minutes; without it, a mathlib source build is hours and tens of GB. cdc-lean's pinned Mathlib rev may have no cloud cache — **check before promising the reproduce-cdc-lean demo**; a generic capability can pin a rev known to be cached.
- **Maintenance:** mathlib API churn makes pins mandatory and stale; toolchain bumps mean sandbox image rebuilds; eval seeds must pin a toolchain to stay deterministic.
- **License:** cdc-lean has none — never vendor its code/definitions into Prax repos. Building/auditing it locally is fine. The real dependencies (Lean 4, mathlib, community REPL) are Apache-2.0 — the actual path is legally clean.

## Open questions for TJ

1. Ambition: doc only, or doc + Shape 1 (~1-2 days)? The independent cdc-lean rebuild+audit is the flashy middle deliverable — and would double as a live `VERIFICATION_LEDGER` row.
2. Live-box appetite for ~10 GB toolchain+cache on the shared disk, or prototype in a scratch container first?
3. Spoke-internal tool (core, flag-gated) vs prax-plugins plugin (opt-in)? The extending guide implies spoke for a core capability.
4. Stage miniF2F/PutnamBench into `PRAX_EVAL_DIR` in the same push, or tool first, benchmarks later?
5. If Prax reproduces the cdc-lean audit: publish (notable first; but attaches Prax's name to an unlicensed repo and a contested claim) or keep internal?
