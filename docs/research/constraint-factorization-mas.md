# Multi-Agent Constraint Factorization Reveals Latent Invariant Solution Structure (arXiv 2601.15077)

**Christopher Scofield (sentienta.ai), Jan 2026, cs.CL. Single author,
LLM-assisted (acknowledged), preprint, no venue, no experiments.** A theory
paper answering *why* multi-agent LLM systems beat single-agent ones even at
identical information and capacity.

The model: each agent is a **constraint-enforcement operator** on a shared
state (the dialog). A multi-agent system is the **factorized composition** of
those operators, which converges to **invariant sets = the intersection of the
agents' constraint sets**. Extended from exact projections to soft constraints
via proximal operators, so the story survives approximate, incremental
updates.

**Verdict: document + adopt ONE design criterion for the spoke architecture —
a spoke earns its existence by enforcing a *distinct constraint family*, not by
having a distinct persona. Bank the empty-intersection pathology as a
diagnosable failure. Do NOT adopt the formalism as predictive: there is not a
single LLM experiment in it, and its headline separation result leans on a
modelling choice that is easy to miss.**

---

## The worked example, and the soft spot

§7 is the whole paper in miniature, and it is refreshingly checkable. Three
agents on ℝ², each with one quadratic penalty:

φ₁ = ½(x₁−1)², φ₂ = ½(x₂−1)², φ₃ = ½(x₁+x₂−1)²

The collective objective F has unique minimiser **x\* = (2/3, 2/3)**. Cyclic
composition of the three proximal operators converges exactly there — although
no agent optimises F, no agent sees all constraints, and no agent computes the
full gradient. Nice result, and the mechanism is real (it is von Neumann
alternating projection / Douglas–Rachford wearing an LLM hat).

**But the claimed separation depends on how the single agent is modelled.**
The monolithic comparison is *regularised* minimisation, argmin F(x) + μ‖x‖²,
giving x_μ = (2/(3+μ), 2/(3+μ)) — which differs from x\* "for all μ > 0". The
qualifier is doing the work: **at μ = 0 the single agent lands exactly on
x\***. So the result is not "a single agent cannot reach the factorized
solution"; it is "a single agent *that adds a norm penalty* cannot." Whether a
monolithic LLM must behave like a regularised compromiser is an assumption
about LLMs, argued rather than demonstrated. The honest reading of §7 is that
factorization changes the *dynamics*, not that it enlarges the reachable set
in any deep sense.

The author is otherwise candid about limits (§9.2): empty intersection ⇒ no
convergence; guarantees weaken when updates are "noisy, approximate, or
inconsistently applied — as in practical systems"; the analysis is
**asymptotic, not finite-time**; agent dominance, imbalance and slow
convergence are "not ruled out by the theory." And the text-based mapping (§8)
is explicitly an analogy: "the claim is not that large language models
explicitly compute projections."

## The adopt: what makes a spoke worth having

§9.1 contains the sentence worth taking:

> "agent differentiation is effective insofar as agents are associated with
> **distinct families of constraints** whose interaction defines non-trivial
> invariant sets. **Personas can therefore be understood as mechanisms for
> partitioning evaluative authority, rather than as ends in themselves.** When
> agents enforce identical or nearly identical constraints, the resulting
> dynamics may reduce to effectively redundant updates."

Prax has 15 spokes. The design rule we have used is *capability* grouping —
browser, memory, sandbox, tasks — plus the orchestrator-tool-count ceiling.
This supplies a sharper, auditable test:

> **Which constraint does this spoke enforce that no other spoke does?**
> If two spokes would accept and reject the same outputs, they are redundant
> as *agents* however different their prompts read, and the fan-out buys
> latency and tokens without enlarging what the system can reach.

That is a genuinely different question from "does this spoke own distinct
tools," and it is the one to ask when a 16th spoke is proposed. It also pairs
with the [scaling-agent-systems](scaling-agent-systems.md) row from the other
direction: that paper says *don't fan out when the orchestrator suffices*;
this one says *when you do fan out, make sure the agents disagree about
something*.

## The pathology worth naming

§9.2: if ⋂Aᵢ = ∅ — the agents' constraints are jointly unsatisfiable — **no
invariant solution exists and convergence is impossible**. In a real system
that does not present as an error; it presents as **oscillation**: agents
undoing each other, a loop that burns budget and never settles.

This is a live risk in exactly one place in Prax: **governance constraints
versus task constraints**. A lethal-trifecta guard, a deny-by-default boundary
or an approval gate that is jointly unsatisfiable with the task the user asked
for produces a thrashing agent rather than a clean refusal. The design
implication is small and worth stating: when constraints conflict, the system
should **detect infeasibility and say so**, not iterate. That is the same
honesty principle as the calibration work — "I cannot do this under these
constraints" beats an expensive loop pretending otherwise.

## Honest limits

No experiments. None. The only concrete demonstration is a 2-D quadratic toy
whose operators are affine contractions; every claim about LLM systems is
transferred by analogy through an unobservable encoding E(dialog). The
convergence machinery is classical (von Neumann 1933, Douglas–Rachford,
ADMM, proximal point) — the contribution is the *interpretation*, not new
mathematics. Single-author preprint with LLM assistance acknowledged, not
peer-reviewed, cs.CL. And per the soft spot above, the flagship separation
result is sensitive to a modelling choice about the single-agent baseline.

Taken at the right size it is still useful: a vocabulary for *why* our
hub-and-spoke shape works, and one crisp test for whether a proposed agent
adds anything.

## Related

- [scaling-agent-systems.md](scaling-agent-systems.md) — the empirical
  counterpart (when multi-agent pays, measured); this is the mechanistic
  story, and the two agree that agent *count* is not the variable.
- [crux-shadow-evals.md](crux-shadow-evals.md) — agents converging early on
  one approach is invariant-set collapse from the behavioural side.
- [weakest-not-shortest.md](weakest-not-shortest.md) — also a constraint-set
  view: a spike is a hypothesis whose constraint set is far too tight.
