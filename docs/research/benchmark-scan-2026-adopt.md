# Benchmark landscape scan (2026) — what Prax is missing

[← Research](README.md) · companion to [prax-benchmarks.md](prax-benchmarks.md) (the catalog)

A 104-benchmark web scan of the agent eval/benchmark landscape, each entry scored
for **grading determinism**, **CPU-feasibility**, **cost**, the **Prax spoke** it
exercises, and **current coverage**. This is the prioritized *adoption* view;
[prax-benchmarks.md](prax-benchmarks.md) is the reference catalog.

## The one finding that matters

**Prax has the eval *engine*, not the eval *coverage*.** The resumable batch
runner, capability/harness-lift/GAIA suites, token telemetry, HAL cost-axis,
gaming-detection, disagreement-curation, and the maker≠checker auditor are all
built. But of 104 standard benchmarks:

| | count |
|---|---|
| coverage = **none** | **86** |
| coverage = partial | 17 |
| coverage = covered | 1 |
| grading = **deterministic** | **62** |
| recommendation = adopt-now | 33 |

So the gap isn't capability or infrastructure — it's that almost none of the
field's standard yardsticks are *plugged into* the engine Prax already owns. And
because 62/104 grade deterministically, most of the adoption path fits Prax's hard
constraints: **un-gameable (no LLM judge) + CPU-feasible + keyless CI**.

## Do-first shortlist — deterministic, CPU-feasible, high-leverage

Ranked by (deterministic grading × CPU-yes × coverage gap × alignment with a
strength Prax is already investing in). Each plugs into the existing batch/
capability harness with a per-benchmark adapter.

| Benchmark | Spoke it grades | Why it's first |
|---|---|---|
| **BFCL v3** (Berkeley Function Calling) | orchestrator tool-binding / structured output | The FC standard; **AST + state-transition** grading runs 100% offline, no GPU, no judge. Best regression detector for `governed_tool` / `llm_factory` schema bugs. |
| **IFEval** | orchestrator ReAct + content spoke | Deterministic **instruction-adherence** checks — grades the system prompt directly; free, offline, tiny. |
| **InjecAgent** | governance (trifecta) | Deterministic **prompt-injection** attack-success — grades the lethal-trifecta guard + AgentDojo injection goldens I shipped. Directly extends the security thread. |
| **HaluEval + TruthfulQA** | knowledge (grounding) | Deterministic **hallucination / truthfulness** — grades the honesty-guard + grounding work; CPU, cheap. |
| **GSM8K · GPQA · MMLU-Pro · BBH** | orchestrator core reasoning (+ sandbox) | Deterministic, free, offline **reasoning regression** — Prax has none; the floor everyone reports. |
| **LoCoMo** | memory | Deterministic **long-term conversational memory** — grades the memory spoke (currently zero coverage). |
| **WebShop · MiniWoB++** | browser | Deterministic, free, offline **web-action** environments — the cheapest way to grade the browser spoke. |
| **Aider Polyglot · EvalPlus (HumanEval+/MBPP+)** | sandbox | Deterministic **coding** — grades the sandbox spoke on real edit/execute loops, cheaply. |

## The strategic one, even though it's CPU-*partial*

- **τ²-bench** (`sierra-research/tau2-bench`) — multi-turn tool use under **company
  policy**, scored on **final DB state** with **pass^k** (must succeed *every* run,
  not once). This is the single closest benchmark to Prax's *reliability* thesis
  and its `governance`/`action_policy` + confirmation gate. pass^k is exactly the
  harness-lift axis. Worth the setup cost; adopt after the offline shortlist.

## What validates Prax's *unique* bets (adopt, but as differentiation)

- **GAIA2 / ARE (Meta)** — async, time, and scheduling axes most benchmarks ignore
  but Prax's **scheduler + tasks + task-runner** uniquely own.
- **Agent Security Bench — memory-poisoning** track — pairs with the trifecta +
  trajectory auditor; Prax's governed-memory story should shine here.

## Adopt-later (deterministic + CPU, lower urgency)

API-Bank, NexusBench, BigCodeBench, RGB/RAGTruth/FaithBench (grounding), Mind2Web
(offline), Multi-IF, MuSR — all `coverage=none`, all deterministic-or-mixed; good
second-wave once the shortlist adapters exist and the per-benchmark loader pattern
is proven.

## Recommendation

1. Build **one generic benchmark-adapter seam** (dataset loader → prompt → the
   deterministic scorer the benchmark ships → the existing batch runner), then
2. land the **shortlist** in determinism order (BFCL → IFEval → InjecAgent →
   HaluEval/TruthfulQA → GSM8K/GPQA → LoCoMo → WebShop/MiniWoB++), then
3. τ²-bench for the reliability/pass^k story, then the adopt-later second wave.

Everything above is un-gameable + CPU/keyless, so it hardens the same fitness
function the self-regeneration loop stands on — coverage is the missing multiplier.

## Shipped so far

A generic **`BenchmarkAdapter` seam** (`prax/eval/benchmarks/`) — `cases()` /
`prompt()` / `score()` → the resumable `run_batch`, deterministic scoring — plus a
**registry** (`get_adapter`), a **live orchestrator executor**, a **CLI**
(`eval_suite.py benchmark <name> [--lift]`), `make eval-benchmark BENCH=… [LIFT=1]`,
and a **per-benchmark harness-lift** (`run_benchmark_lift`: full vs bare, same
model). Adapters landed, each deterministic + keyless in CI, runnable against real
Prax:

| Adapter | Category | Grades |
|---|---|---|
| **IFEval** | instruction-following | system-prompt / instruction adherence |
| **BFCL** | tool-calling | function-call AST/structural match |
| **InjecAgent** | agent-safety | indirect-injection ASR + utility (the lethal-trifecta guard) |
| **sycophancy** | epistemic vigilance | challenge-rate on false user premises (inbound honesty) |
| **HaluEval** | grounding | hallucination detection (Yes/No) |
| **TruthfulQA** | grounding | truthfulness vs common misconceptions (MC) |

Remaining shortlist (same pattern, cheap to add): τ²-bench (pass^k), GSM8K/GPQA
(reasoning), LoCoMo (memory), WebShop/MiniWoB++ (browser), Aider/EvalPlus (coding),
and gated full-set loaders for the above.

## Source

Structured 104-entry scan (grading/CPU/cost/spoke/coverage per entry). Companion:
[prax-benchmarks.md](prax-benchmarks.md) · [evals infra](../../prax/eval/) ·
[awesome-evals](awesome-evals.md).
