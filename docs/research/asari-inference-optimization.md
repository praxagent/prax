# Asari — self-improving agents optimizing an inference stack

**asari.ai blog, "inference optimization".** Company pitch: "co-inventors —
self-improving agents that design and optimize high-performance
infrastructure," currently pointed at LLM inference. Reported: agents
optimized DeepSeek v4 Pro and GLM 5.2 on **NVIDIA B200** under **vLLM v0.23**,
improving "**both throughput and interactivity by up to 16%** across multiple
max-concurrency levels," at roughly **one day of optimization per concurrency
level**, and claiming that "as of vLLM v0.26, most of the changes made by our
agents have no upstream equivalent." The agents work across kernels (PTX,
CuTeDSL, CUDA C, Triton), PyTorch, Python glue, runtime, scheduling, caching
and load balancing — M=1 skinny-GEMM kernels, fusion, parallelism, launch
overhead, scheduler and load-balancer algorithms.

**Verdict: document-don't-adopt the domain — GPU serving is our providers'
concern, as with [kernel-forge](kernel-forge-mcts-optimization.md) and the
inference-listicle verdict. Adopt ONE idea: their *verification* standard —
distribution-level equivalence checking for changes that are supposed to
preserve behaviour.** That idea is hardware-independent and fixes a real gap
in both our proxy tests and the #29 accept-gate.

---

## The idea worth taking

Buried under the speed numbers is the harder engineering problem: **how do you
know a 16%-faster stack still computes the same thing?** A rewritten kernel can
be subtly wrong in ways an aggregate benchmark cannot see — a couple of points
of accuracy looks like noise, and the regression ships.

Asari's answer is to stop trusting aggregate scores: **"stringent
distribution-matching checks" on token-level probability distributions across
multilingual datasets**, rather than benchmark accuracy alone. The principle
generalises well beyond kernels:

> For a change that is *supposed* to preserve behaviour, the test is
> **equivalence**, not "the score didn't drop."

Two places Prax needs exactly this:

1. **The secrets proxy.** It sits in the streaming path of every model call.
   A proxy that mangles a stream fails **silently** — the
   [FailureAtlas](failure-atlas-assessment.md) row already names silent
   corruption (HTTP 200 + wrong semantics) as the worst failure class, and
   flags a proxy silent-failure suite as the adopt. This supplies the missing
   technique: assert the response is **equivalent to the un-proxied one**, not
   merely that it arrived with a 200.
2. **The #29 accept-gate.** Today every self-proposed change is judged the same
   way — does it improve the goldens at acceptable cost. But changes come in
   two kinds, and conflating them is a bug in the gate:
   - **Behaviour-changing** (a fix, a new capability) → judge on goldens.
   - **Behaviour-preserving** (a refactor, an optimisation, a simplification —
     precisely what the complexity/dead-code gate encourages) → the correct
     test is that outputs are **unchanged** on a corpus. A refactor that
     improves the golden score has changed behaviour and deserves scrutiny,
     not applause.

That second point pairs directly with the
[weakest-not-shortest](weakest-not-shortest.md) correction: `/simplify`-style
changes are exactly the behaviour-preserving class, and we currently have no
equivalence check on them at all.

## What it confirms

Asari is a **self-improving agent that works**, and it works for the reason
[CRUX](crux-shadow-evals.md) predicts: it lives entirely on the *verifiable*
side of the boundary. Throughput and latency on real hardware are a fast,
objective, un-gameable evaluator — the same precondition
[kernel-forge](kernel-forge-mcts-optimization.md) identified and the same one
every working system in the CRUX survey shares. It is not doing open-ended
research; it is searching a space with a deterministic scorer. That is a
useful data point *for* the #29 design and *against* over-reading it: the
achievement is real and narrow.

## Honest limits

A vendor blog with no paper, no code, and no independent replication.
"**Up to** 16%" is best-case framing over unspecified baselines and
concurrency levels; the median is not given. "No upstream equivalent as of
vLLM v0.26" is unfalsifiable from outside and also self-serving. One day of
agent time per concurrency level implies substantial compute that is not
priced. The distribution-matching methodology is described in a phrase, not
specified — no thresholds, no divergence measure, no false-positive rate — so
we are adopting the *principle*, which is sound on its own terms, not their
implementation. Same skepticism as the [FutureSearch](futuresearch-calibrated-forecasting.md)
launch claims: the idea can be good while the marketing is unverified.

## Related

- [failure-atlas-assessment.md](failure-atlas-assessment.md) — silent failure
  as the worst class; this sharpens its proxy-test adopt row.
- [weakest-not-shortest.md](weakest-not-shortest.md) — the
  behaviour-preserving change class is exactly what `/simplify` produces.
- [crux-shadow-evals.md](crux-shadow-evals.md) — why a self-improving agent
  with an objective evaluator works while open-ended research does not.
- [kernel-forge-mcts-optimization.md](kernel-forge-mcts-optimization.md) —
  same domain, same GPU wall, same evaluator precondition.
