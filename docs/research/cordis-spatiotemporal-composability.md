# Cordis — *A Programming Paradigm for Spatiotemporal Composability*

**Source:** Yifan Shi (PKU / DeepSeek-AI), Wei Zhang (PKU), Tianyi Cui
(DeepSeek-AI), 88pp. The paper behind
[deepseek-harness](deepseek-harness.md), which this project **parked** on
2026-08-13 with *"the concept is referenced but never explained in the
available material… if someone reads the paper and it names a real capability,
reassess then."* This is that reassessment.

**Verdict: the mathematics is sound and the framing is genuinely good.
Document-don't-adopt — it is a rigorous foundation for a capability Prax
deliberately does not have. Take one definition as vocabulary, and take the
paper's *habit* rather than its machinery.**

## Is the math valid?

**As far as I checked, yes — and the paper is unusually honest about its own
negative results.** Scope of the check, stated plainly: I read §3.1 and §4.4 of
88 pages, verified the §3.1 proofs line by line, and read the §4.4 theorem
statements and proof structure. I did **not** verify Definitions 43–58 or
Table 1, which the big metatheory results rest on. So this is
"correct-where-checked", not a certification.

What I verified:

- **Theorem 4** (`pr₁ ∘ track(f,g) = f ∘ pr₁`) — correct, one line.
- **Theorem 5** (`track` is a monoid homomorphism) — correct. The central
  construction is `∂Γ := Γ × (Γ→Γ)` with an *accumulator* holding the composite
  inverse, and `track` mapping into the **twisted composition monoid**
  `𝔗_Γ = (Γ→Γ) × (Γ→Γ)ᵒᵖ`, where `(f₁,g₁)∘(f₂,g₂) := (f₁∘f₂, g₂∘g₁)`. The
  inverses accumulating in the opposite order is exactly `(ab)⁻¹ = b⁻¹a⁻¹`,
  and packaging effect-tracking as a homomorphism into a monoid-times-its-
  opposite is a clean, correct way to say it.
- **Theorem 7** (recovery is invariant under a tracked step whose inverse
  witnesses at that state) — correct.
- **Theorem 15** — the notable one. They prove their own lift **fails** to
  carry witnessed effects `𝔈*_Γ` into `𝔈*_∂Γ` in general, and pin the exact
  condition under which it does (`g∘f = id_Γ`). A paper willing to prove its
  own construction does not close a triangle is not overselling.
- **Lemma 18** — the centralizer argument (maps commuting with a generating set
  form a submonoid). Standard and correct.
- **Theorem 20** — the induction pushing an inverse out through later forward
  maps is right.

The §4.4 metatheory has the right shape for a serious operational-semantics
paper: **Preservation**, **Recovery exactness**, **Ordering**, **Resolution
coherence**, **Progress** (no-deadlock *plus* termination with an explicit
bound `S(n) ≤ (K+4)(V(n)+1)` and a well-founded recursion over an acyclic
precedence relation), and **Confluence** proved by transposing adjacent
independent steps — the standard Mazurkiewicz-trace argument, which they cite.

**One honest calibration.** The core mathematics is *elementary* — monoid
homomorphisms, centralizers, well-founded induction — wearing 88 pages of
notation. That is not a criticism: in PL theory the work is choosing the right
definitions, and these definitions do real work. But the density should not be
read as depth, and nothing here required a new idea in algebra.

## Is it helpful to Prax? Mostly no, and the reason matters

The theory buys **hot-loading and unloading components at runtime with complete,
verified effect reversal**, plus reactive dependency resolution. Prax does not
do that, and the gap is deliberate:

- Prax **restarts the process** (~2s Flask reloader). That is precisely the
  "coarse-grained workaround" §1.2.3 criticises — and the paper's cost argument
  (restarts discard caches and connections, take seconds to minutes, force
  redundant replicas) is an argument about **fleet services**, not about one
  assistant on one box.
- **#29 self-regen modifies the system prompt, not running code.** No hot-swap
  is involved.
- And [harness-delta attribution](harness-delta-attribution.md), assessed two
  days ago, measured **up to 98% of automated harness-evolution gains as
  overfitting**, with train-selected harnesses *worse* than baseline on
  held-out in 4 of 16 settings. So the capability this theory underpins —
  continuous self-modification of live components — is one we have fresh
  empirical reason **not** to rush toward.

Building Cordis-style machinery now would be a rigorous foundation for a
capability we have chosen not to have. That is the "rule engine looking for
rules" error at a larger scale.

## The one thing worth taking: Definition 19

**Independence.** Two effect functions are independent when (1) every
transformation of one commutes with every transformation of the other, and
(2) neither disturbs the inverse the other yields.

That is a precise statement of the condition **#64** violates. #64 is *"parallel
spokes share one workspace with no locking and no atomic write"* — i.e. two
components whose transformations **do not commute**, run as though they did.
The paper gives the property a name and the exact obligation: safe parallelism
requires commutation, and commutation only has to be checked on the generators
(Lemma 18).

That does not change the fix — atomic write plus a lock is still the fix — but
it says *why* it is the fix, and it gives the shared-findings record from
[DeLM](decentralized-shared-context.md) a criterion: entries from two spokes
are safely mergeable exactly when their writes commute.

## Why we had no theory section — the useful half of the question

Not defensiveness: an honest test. **Which of this week's bugs would a formal
foundation have prevented?**

- `decay_graph` never ran (Cypher param passed as a Python kwarg) — **no
  theory helps.** That is a language wart.
- Judges inheriting `AGENT_TEMPERATURE` — **no theory helps.** Configuration.
- Errored cases dropped from the pass rate **and** `avg_tokens` — a *missing
  invariant*: the score must be monotone in failures.
- `delegate_parallel` counting dispatched rather than returned — a missing
  invariant: the merge reports on a **complete** set.
- PDF partial extraction reported silently — the same missing invariant again.

**Three of the five share one shape: a function reporting on a set without a
stated invariant about that set.** That is not category theory. It is writing
the invariant down.

And the paper models exactly that habit: it names `φ(γ) = γ₀` the *soundness
invariant* and then carries it through every theorem. The transferable thing is
the **discipline**, not the calculus.

So the answer to *"should we have a theory section?"* is: **not a theory
section — an invariants section.** For each aggregate, merge and rewrite step,
state the property it must preserve, in one line, next to the code. Cheap,
checkable by test, and it would have caught the defect class that dominated
this week.

## Why we didn't find it first

We did find the pointer — three days ago, in the deepseek-harness assessment —
and **parked it rather than inventing a summary from the title.** That note
asked for exactly this reassessment if someone read the paper. So the process
worked; what it lacked was the PDF, which was not linked from the repo README
we could read.

The more useful admission is the other one: **our research lane is entirely
empirical.** Every assessment this month asks "what did they measure". A
formal-semantics paper is a different kind of contribution, and we have no
habit of reading for *definitions* rather than *results* — which is why the
one durable takeaway here (Definition 19, and the invariant habit) came from
the definitions section rather than the numbers.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **An "invariants" line beside every aggregate / merge / rewrite** — 3 of this week's 5 defects were a missing set-invariant | 📋 queued; the cheap, real version of "a theory section" |
| Definition 19 (independence = commutation + inverse non-disturbance) as the criterion for safe parallelism | ✅ vocabulary adopted — sharpens #64 and gives the [DeLM](decentralized-shared-context.md) shared record a merge criterion |
| Revertible effects / reactive coeffects / the dynamic-composition calculus / Cordis | ❌ declined — a rigorous foundation for hot component swap, which Prax deliberately does not do, and which [HDA](harness-delta-attribution.md) gives fresh reason not to rush toward |
