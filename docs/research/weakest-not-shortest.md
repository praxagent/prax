# The Optimal Choice of Hypothesis Is the Weakest, Not the Shortest (arXiv 2301.12987)

**Michael Timothy Bennett. AGI 2023 (16th Conf. on Artificial General
Intelligence, Stockholm), LNCS 13921 pp. 42–51; v4 Apr 2024.** A short theory
paper attacking a load-bearing assumption of machine learning: that the
*shortest* hypothesis generalises best (Occam / minimum description length /
compression-as-intelligence).

Bennett's counter-proposal is **weakness**, defined as the **cardinality of a
statement's extension** — how many situations it permits. "It's raining" is
weaker than "it's raining heavily at noon on Tuesday" because more worlds
satisfy it. Maximise weakness *subject to sufficiency*: among hypotheses that
still account for what you observed, prefer the one that rules out the least.

Results: compression is proven **neither necessary nor sufficient** to maximise
the probability of generalising; under a uniform task distribution **no proxy
beats weakness maximisation** without losing somewhere else; and in binary
arithmetic, weakness generalised at **1.1–5×** the rate of MDL (addition
68% vs 24%; multiplication 46% vs 21%, at 14 training examples).

**Verdict: document + adopt as a LENS, and let it CORRECT an existing adopt
row. It supplies the epistemics behind Prax's oldest house rule — never spike a
benchmark — and it tells us the complexity/dead-code gate is measuring the
wrong thing.** Not adoptable as a metric: weakness is not computable for a code
diff, and the theorem's uniformity assumption is false for us.

---

## It names what "never spike" actually is

Prax's prime directive says a fix must "abstract the problem class" and that a
reader who knows the benchmark "must NOT be able to tell which task it
targeted." In Bennett's vocabulary that is exactly a **preference for weak
hypotheses**:

- A **spike** is a *strong* hypothesis. Its extension is tiny — it fires only
  on the failing case, permits almost nothing else, and therefore cannot
  generalise. That is precisely why it scores well and helps nobody.
- A **general fix** is *weak*. It admits a large class of inputs, constrains
  less, and generalises for the same structural reason.

So the house rule is not merely an ethic we enforce by vigilance; it is the
decision-theoretically preferred choice under his framework. That is worth
having written down, because "don't spike" has always been argued here from
**safety** (reward hacking generalises to misalignment,
[emergent-misalignment](emergent-misalignment-reward-hacking.md)). Bennett
supplies the independent *epistemic* argument: spiking also just doesn't work.
Two different reasons, same rule — the strongest position to be in.

## The correction: simplicity is not generality

The [AIDE²](aide2-recursive-self-improvement.md) row **"complexity/dead-code
gate on self-mods"** has the #29 accept-gate run `/simplify` plus linters
before adopting a change, on the reasoning that score↑ is necessary but not
sufficient. That gate rewards **shortness**. Bennett's central result is that
shortness is the wrong proxy — "a complex formula can be weak, while a simple
one can be highly specific."

Concretely, the failure this predicts: a self-proposed fix that special-cases
a narrow input signature is *short, lints clean, and passes the golden* — and
is a spike. A longer change that generalises the code path scores worse on the
existing gate and is the better hypothesis. **The complexity gate can actively
prefer the spike.**

The fix is not to delete that gate — dead code and bloat are real costs — but
to stop it standing in for generality, and to add the weakness question
alongside it:

> **How narrow is the trigger?** Over how wide a class of inputs does this
> change alter behaviour? A change that fires only on the shape of the failing
> case is a strong hypothesis and should be treated as a suspected spike,
> regardless of how clean it looks.

That is an auditable question a reviewer or an accept-gate can ask, and it
does not require computing anything.

## What we cannot take

**Weakness is not computable for a code change.** The formalism needs a finite
specified vocabulary and a well-defined extension to count; a diff against a
Python codebase has neither. Anyone claiming a "weakness score" for a PR would
be inventing a number. This lands as a lens and an audit question, never a
metric.

**The optimality theorem does not apply to us.** Both propositions assume
tasks are **uniformly distributed**. Prax's distribution is emphatically not
uniform — it is whatever TJ and the channels actually do, which is the
[grounding-gap](ilands-grounding-gap.md) point restated. Under a non-uniform
distribution a proxy tuned to the real distribution can beat weakness, so the
"no proxy does better" result is a statement about an idealisation, not a
promise about Prax.

## Honest limits

Ten pages of theory plus a toy experiment: 8-bit strings, 4-bit addition and
multiplication, 4–14 training examples. The 1.1–5× spread is real within that
domain and tells us nothing directly about code or agents. Results hold inside
Bennett's "enactive cognition" formalism (lattices of declarative programs) —
a specific model of cognition, not a neutral one. The Apperception Engine
explanation is offered as an *argument*, not a controlled comparison. And the
uniformity assumption above is doing a great deal of work in both theorems.

The reason to bank it anyway is that its central distinction — extension vs
syntax, generality vs brevity — is exactly the axis on which our self-modifying
loop will be judged, and we currently have a gate pointed at the wrong one.

## Related

- [aide2-recursive-self-improvement.md](aide2-recursive-self-improvement.md) —
  the complexity/dead-code row this corrects.
- [emergent-misalignment-reward-hacking.md](emergent-misalignment-reward-hacking.md)
  — the safety argument for the same rule; this is the epistemic one.
- [ilands-grounding-gap.md](ilands-grounding-gap.md) — why the uniform-task
  assumption fails for a system with real users.
- [crux-shadow-evals.md](crux-shadow-evals.md) — agents settling early on a
  narrow approach is the same pathology from the behavioural side.
