# Lilian Weng — Harness Engineering for Self-Improvement

**lilianweng.github.io, 2026-07-04.** A survey-style post from one of the
field's most careful writers, defining the **harness** as "the system
surrounding a base model that orchestrates execution and decides how the model
thinks and plans, calls tools and acts, perceives and manages context, stores
artifacts, and evaluates results," and arguing that **harness engineering — not
raw model intelligence — is the critical path to recursive self-improvement**.

Its organising idea is a **ladder of optimization targets**: instruction
prompts → structured context → workflow → **harness code** → **optimizer
code**. Each rung moves the thing being improved further from the prompt and
closer to the machinery.

**Verdict: document + adopt as the reference map for the [#29](../IDEAS_BACKLOG.md)
self-regeneration cluster — it is the best-organised external statement of the
thesis Prax is built on, it names our position on the ladder, and it supplies
one concrete safety mechanism we should copy. It also lists seven open
problems, five of which we already have adopt-rows for, which is a useful
independent check that the backlog is pointed at real things.**

---

## Where Prax sits on the ladder

Weng's ladder is the cleanest framing we have for what #29 is actually
proposing to do, and it makes the scope argument concrete:

| Rung | Prax today |
|---|---|
| Instruction prompts | system prompt, spoke prompts — edited by hand |
| Structured context | `agent_plan`, progress files, the overflow ladder, two-layer memory |
| Workflow | orchestrator + spokes, task runner — edited by hand |
| **Harness code** | **#29 P1's target** (propose a plugin/overlay edit, verify, adopt-or-drop) |
| Optimizer code | not attempted, and shouldn't be yet |

So #29 is a rung-4 project, and the rung-5 jump — an agent improving *the
optimizer that improves it* — is exactly where the un-gameable-fitness problem
becomes unbounded. Naming the rungs makes "how far up do we go, and what gate
guards each step" a question with a vocabulary instead of a vibe.

## The one mechanism to copy

Among the systems surveyed, the **AHE framework makes the runs directory,
tracer, verifier and LLM configuration read-only, which "disables a set of
reward hacking."**

That is a sharper, cheaper version of an adopt-row we already hold in policy
form. Our tracker says *"eval-harness edits = HIGH-risk / human-gated"* — a
rule enforced by governance and vigilance. AHE's move is to enforce it
**structurally**: the loop physically cannot write to its own scorer, so the
entire class of "improve the score by editing the thing that measures the
score" is unreachable rather than forbidden. Same instinct as the secrets
proxy — a compromised Prax has nothing to steal because the keys aren't there,
not because policy says don't touch them.

Concretely for #29: the self-regen loop should run with the goldens, the
scorer, `prax/eval/` and the trace store **mounted read-only**, not merely
flagged HIGH-risk. Cheap to implement, and it converts a policy into a
property.

## The seven open problems, scored against our backlog

Useful as an external audit of whether the tracker is aimed at real gaps:

| Weng's bottleneck | Prax status |
|---|---|
| **Weak evaluators** ("research taste, novelty… much harder to measure") | tracked — the [proofjudge](proofjudge-taste.md) taste row, and the [CRUX](crux-shadow-evals.md) verifiable/open-ended boundary is the same claim |
| **Context lifecycle** | tracked — [ACM](acm-agentic-context-management.md) agent-initiated compaction, plus CRUX's instruction-preserving-compaction constraint |
| **Negative results** — models fail to acknowledge failure, from data imbalance | **fixed this week, twice**: `task_done` now requires evidence + a calibrated probability, and `agent_step_done` records failed/skipped instead of marking everything done |
| **Diversity collapse** — loops exploit known high-reward patterns | tracked — population/MCTS search ([kernel-forge](kernel-forge-mcts-optimization.md)), and CRUX's no-project-level-backtracking finding is the behavioural twin |
| **Reward hacking** — "optimizes whatever signal it is given" | our oldest rule, now with the [weakness](weakest-not-shortest.md) epistemics and the read-only mechanism above |
| **Long-term success** — maintainability, ownership, migration cost, backwards compatibility over short-term completion | **NOT tracked. New gap.** |
| **Human role** — "humans should move up the stack, not be removed from the loop" | the TeamWork thesis, and the [(Im)Paired Programming](impaired-programming-comprehension.md) comprehension row |

Five already covered, one fixed in the last few days, and **one genuine hole**:
nothing in the accept-gate values *long-term* code health. A self-proposed
change that passes goldens at lower cost can still be a maintenance disaster —
and this is the same axis as the [Asari](asari-inference-optimization.md)
behaviour-preserving/behaviour-changing split and the
[weakest-not-shortest](weakest-not-shortest.md) correction, all three pointing
at "the accept-gate optimises the wrong horizon."

## Numbers worth remembering

- **STOP failed with GPT-3.5 and Mixtral, succeeded with GPT-4** — the base
  model must be capable enough to improve the mechanism. A capability floor
  under any self-improvement plan, and a reason not to test #29 on a cheap
  model and conclude it doesn't work.
- **Non-monotonic harness benefit**: a 9B model showed "similar harness
  updating capability" to Claude Opus, while **middle-tier models benefit most
  from updated harnesses.** That is a direct, checkable prediction about
  Prax — and it reframes our own Terminal-Bench baseline, where a 30B model
  scored the same under our harness as under harbor's reference agent.
- **PaperBench ~21%** for the best model on replicating ICML papers, not
  outperforming ML PhDs; **RE-Bench**: humans non-zero in 82% of 8-hour
  attempts while agents scored 4× humans at a 2-hour budget. The shape CRUX
  found — agents strong on short verifiable horizons, weak on long open ones.
- **Darwin Gödel Machine**: agents modifying their own harness, "20% to 50%"
  improvement on SWE-bench. The existence proof rung-4 is worth attempting.

## Honest limits

A blog post, not a paper: it surveys and organises rather than establishing
anything new, and every number is transcribed from the cited work rather than
reproduced. The RSI framing is the author's thesis, argued from a curated
reading list — the cited systems are selected, not sampled, so "harness
engineering is the critical path" is a position, not a finding. Several
described systems (ACE, MCE, Self-Harness, AHE) we know only through this
summary. Its value here is organisational: it does not tell us anything we
could not have derived, but it names the ladder, and the read-only mechanism
is a real, immediately implementable idea.

## Related

- [aide2-recursive-self-improvement.md](aide2-recursive-self-improvement.md) —
  the #29 proposer this maps.
- [self-improving-agents-survey.md](self-improving-agents-survey.md) — the
  academic taxonomy; this is the practitioner's version, and they agree.
- [crux-shadow-evals.md](crux-shadow-evals.md) — the verifiable/open-ended
  boundary, restated here as "weak evaluators."
- [asari-inference-optimization.md](asari-inference-optimization.md) +
  [weakest-not-shortest.md](weakest-not-shortest.md) — the other two rows
  pointing at the accept-gate's horizon problem.
