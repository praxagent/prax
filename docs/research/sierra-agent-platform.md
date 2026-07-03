# Sierra's agent platform — what Prax can learn

[← Research](README.md)

Reference note on **[Sierra](https://sierra.ai)** (Bret Taylor / Clay Bavor), via a
[conversation with Zack Reneau-Wedeen](https://youtu.be/uCKhOmth2ms) (Head of
Product) on building agents across the whole customer lifecycle. Sierra is a
production "agent operating system" for customer-facing agents (chat / SMS /
WhatsApp / email / **voice** / ChatGPT) with outcome-based pricing.

**Verdict: document + adopt the *evaluation methodology*; reinforce directions
Prax is already converging on; document-don't-adopt the voice/payments/commerce
machinery until those become product directions.** Sierra is a customer-service
company, not an agent harness — but its eval discipline and a few primitives are
directly transferable.

## What Sierra does (the relevant parts)

- **Lifecycle: Build → Optimize → Operate.** *Ghostwriter* ("the agent-building
  agent") turns SOPs, transcripts, photos, and audio — or plain-English goals —
  into "production-ready, multilingual, multichannel" agents with built-in
  guardrails. *Optimize* gives "full visibility into every change — review,
  validate, and ship with confidence." *Operate* is the *Insights* suite
  (Explorer, Monitors, Experiments, Observability).
- **Evaluation by simulation.** Agents are evaluated against **simulated user
  personas and scenarios** before release — a regression gate, not just offline
  metrics. Sierra **open-sourced the τ-bench universe** to standardize this:
  [τ-bench](https://github.com/sierra-research/tau-bench) and
  [τ²-bench](https://github.com/sierra-research/tau2-bench) (airline / retail /
  telecom / banking domains; an **AI user-simulator**; **Pass@k** reliability
  metrics; leaderboard at taubench.com).
- **Memory as a first-class primitive.** The *Agent Data Platform* unifies agent
  memory, customer data, recommendations, and proactive engagement —
  personalizing from conversation history to build long-term relationships.
- **Context engineering.** "Give the model everything it needs but nothing more"
  — treated as the core craft of building a reliable agent.
- **Voice architecture.** A modular design that **parallelizes thinking,
  listening, and talking** for low latency and naturalism in voice-first flows.
- **PCI-isolated payments / agentic commerce.** A dedicated, PCI-certified
  voice-payment path that keeps financial data **isolated from the external
  LLMs**; the thesis that agentic commerce will exceed traditional e-commerce.

## What Prax already has that maps

| Sierra | Prax equivalent |
|---|---|
| Build → Optimize → Operate, "ship with confidence" | Governed-tool layer + the **eval gate** (`make eval`) + the new resumable capability/harness-lift/GAIA suites as a pre-ship gate |
| Simulation vs personas before release | The deterministic **capability suite** + **harness-lift** (today single-turn) |
| Memory as a first-class primitive | The **memory spoke** + consolidation/retrieval |
| Context engineering | Orchestrator context management + the `loop_cost_per_accepted_change` golden |
| Ghostwriter (agents from transcripts) | **Self-regeneration** + `skill_capture_reuse` / `failure_driven_trace_diff` goldens |
| Insights / Observability | Live-traffic reference-free eval + Prometheus `prax_eval_quality` |

## Transferable seeds (prioritized, CPU-feasible)

1. **Persona / user-simulator multi-turn evals (HIGH).** Prax's capability suite
   is single-turn; τ-bench's core idea is an **LLM user-simulator** driving a
   multi-turn conversation, graded **deterministically on database/action state**
   (not a judge) with **Pass@k** reliability. This is the single biggest gap —
   and it's CPU-cheap because grading is deterministic. It extends the existing
   `capability.py` `CaseRun`/`grade_case` model with a conversation loop + a
   state-check kind. (Tracked in the benchmark-scan adoption plan.)
2. **Adopt τ²-bench directly (HIGH).** Open-source, deterministic, maps straight
   onto the Prax **tool spoke** (~97 tools). Run it overnight on a local model
   via the same OpenAI-compatible endpoint. See
   [`prax-benchmarks.md`](prax-benchmarks.md) and the eval suites in
   [`../../prax/eval/README.md`](../../prax/eval/README.md).
3. **Pass@k reliability, not just pass@1 (MEDIUM).** Sierra reports k-sample
   success because customer agents must be *reliable*, not occasionally right.
   Prax's batch runner already stores per-task results — add a `--samples k`
   option and report pass@k / pass^k. Cheap, high-signal for a weak local model.
4. **Personalization/memory as a *measured* capability (MEDIUM).** Sierra treats
   memory as load-bearing; Prax should score it (LongMemEval / LoCoMo — in the
   scan) rather than assume it.
5. **Eval-case generation from transcripts (MEDIUM).** Ghostwriter builds agents
   *from* real transcripts; Prax can invert it — auto-generate capability cases /
   goldens from failure transcripts (ties to `failure_driven_trace_diff`).

## Document-don't-adopt (out of current scope)

- **Voice (parallelized think/listen/talk).** Prax has SMS/voice channels but not
  a low-latency full-duplex voice agent. If Prax pursues voice-first, Sierra's
  **modular think/listen/talk parallelization** is the reference design. Not a
  CPU-eval concern today.
- **PCI-isolated payments + agentic commerce.** The pattern — *keep financial
  data out of the LLM, in a certified isolated path* — is the right reference
  **if** Prax ever does commerce/payments. Document the boundary now; build later.

## Sources

- Video: [Zack Reneau-Wedeen on Sierra](https://youtu.be/uCKhOmth2ms) · [sierra.ai](https://sierra.ai)
- [τ-bench](https://github.com/sierra-research/tau-bench) · [τ²-bench](https://github.com/sierra-research/tau2-bench) · [τ-bench paper (arXiv 2406.12045)](https://arxiv.org/pdf/2406.12045)
- Related Prax notes: [prax-benchmarks.md](prax-benchmarks.md) · [awesome-evals.md](awesome-evals.md) · [diffuse-ai-control-judge-robustness.md](diffuse-ai-control-judge-robustness.md)
