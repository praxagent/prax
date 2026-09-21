# Code as Agent Harness (UIUC / Meta / Stanford) — assessment

**Source:** [arXiv 2605.18747](https://arxiv.org/abs/2605.18747), *"Code as Agent
Harness: Toward Executable, Verifiable, and Stateful Agent Systems"* — Ning,
Tieu, Fu, Wei, … Lu, Zhang, Zhang, Tong, He (**42 authors**, UIUC / Meta /
Stanford), submitted **18 May 2026**. Landing page:
[code-as-harness.github.io](https://code-as-harness.github.io/code-as-harness-webpage/).
Companion artifact: [Awesome-Code-as-Agent-Harness-Papers](https://github.com/YennNing/Awesome-Code-as-Agent-Harness-Papers),
a curated bibliography. Assessed 2026-09-14. Asked as: *"is this worth
documenting and does it have anything to adopt?"*

**⚠️ Source limits, stated up front.** This is built from the landing page and
the arXiv abstract. **The ~100-page PDF was not read.** Everything below about
the three-layer structure and the six open challenges comes from the authors'
own abstract and page; no section-level claim, figure or number is quoted,
because none were available. A survey of this size will contain material this
note does not cover. Treat it as a routing decision, not a summary.

**Verdict: document — don't adopt it as a lens, and don't let it count as
validation. Bank exactly one thing: it is the *third independent* source to name
Prax's shared-state gap, which promotes that gap from "an idea two papers had"
to a structural finding.**

---

## Why the bar is high, not low

The existing rule in this directory, set by the
[agent-memory survey](agent-memory-survey-2026.md), is that **the marginal value
of taxonomy papers here is roughly zero**, and a new one must be assessed for
*what cell it names that the incumbent does not*. Prax already runs two standing
lenses: [CoALA](coala-cognitive-architectures.md) for memory and
[ETCLOVG](agent-harness-engineering-survey.md) for the harness. This is a third.

It does not replace ETCLOVG, and the reason is structural rather than about
quality. ETCLOVG partitions by **capability layer** — Execution, Tooling,
Context, Lifecycle, plus an Observability / Verification / Governance control
plane — which is what a self-audit needs, because each layer maps to code we own
and a gap we can name. This survey partitions by **the role code plays**:
reasoning substrate, action interface, environment representation; then
mechanisms; then scaling to multi-agent. That is a fine way to organize a
literature and a poor way to audit a system, because a single Prax component
lands in all three at once. **ETCLOVG stays the lens.**

There is a second reason to be careful. Two surveys in one year have now made
"the harness is the unit of analysis" their thesis, and Prax *is* a harness. The
temptation is to read that as the field agreeing with us. It is not evidence of
anything: a survey organizes existing work, it does not measure. **Nothing in
this paper should be cited as support for a Prax design decision.** Its value is
vocabulary and a map of what remains unsolved.

## The six open challenges, against what Prax actually has

This is the part worth reading, because it is the field's own statement of what
nobody has solved. Four of the six are Prax punch-list items under other names,
which is mildly reassuring and not new information. One is a real escalation.

| Their open challenge | Prax's position |
|---|---|
| **Evaluation beyond final task success** | Already the house position — grade the **trace**, not the answer ([praxbench](prax-benchmarks.md), the dual-axis benchmark, cost-per-outcome in the HAL axis). Not a gap. |
| **Verification under incomplete feedback** | The un-gameable-verifier line of work, plus the standing *audit the checker first* rule — which has caught our own scorer under-crediting Prax [four times](eval-rigor-review-2026-07.md). Not a gap. |
| **Regression-free harness improvement** | This is self-regeneration #29, and [harness-delta attribution](harness-delta-attribution.md) already supplies the uncomfortable measurement (most reported harness-evolution gain is overfitting). Open here, open everywhere. |
| **Human oversight for safety-critical actions** | Prax's strongest area, not its weakest: `governed_tool` risk tiers, the lethal-trifecta guard, approvals, capability ceilings. Worth noting the field lists this as *open* — consistent with [ETCLOVG finding governance thinnest in open source](agent-harness-engineering-survey.md) and with [deepseek-harness](deepseek-harness.md) shipping without one. |
| **Multimodal environments** | The sandbox already is this — desktop, browser, CDP. Nothing to take. |
| **Consistent shared state across multiple agents** | **The one that matters. See below.** |

## The one finding: three independent sources, same gap

Prax's spokes run **blind to one another**. Parallel spokes have no shared
agent-centric memory; the hub is the only integration point. That was first
named here by [DeLM](decentralized-shared-context.md), then independently by the
[agent-memory survey](agent-memory-survey-2026.md) (multi-agent memory topology,
the axis CoALA lacks). This survey's entire third layer — *scaling the harness*,
where shared code artifacts, repositories and review workflows are the
coordination medium — lands on the same place from a third direction.

The [agent-memory survey](agent-memory-survey-2026.md) entry set the precedent
explicitly: two independent sources naming the same missing piece **raises it
from one paper's idea to a structural gap**. Three is no longer a coincidence,
and the useful output of this assessment is the promotion, not the paper.

It also sharpens the shape. DeLM argued for decentralizing; the memory survey
framed it as a memory topology; this one frames the shared artifact as **code and
repository state** — which is the framing Prax can actually act on, because Prax
already has the artifact. The git-backed per-user workspace, `agent_plan.yaml`
and the per-space progress files are exactly "shared state expressed as files".
What is missing is not a store; it is that **spokes do not read each other's**.

Worth holding against [scaling-agent-systems](scaling-agent-systems.md), which
found hub-and-spoke *beats* decentralized coordination when verification is
preserved. These reconcile the same way DeLM did: the gap is spokes seeing one
another's working state, not replacing the bounded orchestrator. Any fix must not
become the multi-agent org layer, which remains a deliberate non-goal.

## Declined

- **Adopting the three-layer framework as a lens.** ETCLOVG is better shaped for
  self-audit and is already mapped. A third maintained taxonomy is cost without
  return, per the standing rule.
- **Citing it as validation of the harness thesis.** A survey is not evidence.
- **Any architecture import.** There is none to import; it is a bibliography.

## Worth keeping

- The **awesome-list** as a bibliography to mine when a specific mechanism comes
  up — that is the reusable artifact, more than the PDF.
- The observation that **human oversight for safety-critical actions is still an
  open challenge** in a 42-author survey from three strong institutions. That is
  the third independent measurement of the governance gap, alongside ETCLOVG's
  ecosystem count and deepseek-harness's silence. Same caveat as before: a
  differentiator nobody else builds may be one nobody else needs.

## If someone reads the full PDF later

Two things would change this assessment: a section giving **measured** results
for any harness mechanism (the abstract promises none), or a treatment of shared
multi-agent state concrete enough to implement rather than name. Both are worth
checking before the next time the blind-spokes gap comes up for work.
