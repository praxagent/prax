# Intelligence from Learnable Novelty (Zhang & Levin, 2026) — assessment

**Source:** [arXiv 2607.18433](https://arxiv.org/abs/2607.18433), Yanbo Zhang & Michael
Levin (Allen Discovery Center, Tufts), 20 Jul 2026.
**Verdict:** **Document — don't adopt the method.** It's a *training-time objective*
computed by gradient ascent on a differentiable estimator — weights-level, the same
wall as the other Zhang note ([RLM/LID](rlm-harness-lid.md)) and
[MORPHEUS](skyfall-morpheus-continual-learning.md)/[ARTS](arts-agentic-tree-search.md).
Prax is a
hosted-LLM harness with no weight-training loop, so the mechanism doesn't drop in.
**But bank one genuinely useful distinction** — *learnable novelty vs. unlearnable
noise* — as a lens for two things Prax already wants: **memory consolidation** (what's
worth remembering) and **self-regeneration failure-prioritization** (which failures are
worth learning from). Both are conceptual adopts (📋), not a build.

## What the paper actually says

The claim: existing drives fail in opposite ways because they treat all surprise as one
quantity. **Novelty search** (maximize surprise) gets "transfixed by a noisy television"
— pure noise is maximally surprising and teaches nothing. The **free-energy principle**
(minimize surprise) is "most content in a dark room" — perfect predictability, zero
learning. Both pathologies dissolve if you split total surprise into **the part a
bounded learner can compress (learnable) and the residual it never can (noise)**, and
optimize only the learnable part. Maximum learnable novelty then sits at the
edge-of-chaos between order and randomness.

The mechanism: instantiate the "bounded learner" as a **reservoir computer** — a *fixed,
randomly-initialized, untrained* nonlinear feature map whose only fitted part is a
closed-form ridge readout. Learnable novelty is the two-part description length of that
optimal readout, with a **spectral (log-det of singular values) weight cost** so that
redundant directions cost nothing and only independent structure accrues bits
(`×16 value → +4 bits`). It's differentiable, so it doubles as an **objective**: gradients
flow into whatever *generates* the data while the observer stays frozen.

Results (grounded against the source, with the honest caveats):
- **Elementary cellular automata:** the score ranks **rule 110 (Turing-complete) top**
  over the whole rule space; trivial rules score exactly zero; chaotic rule 30 scores
  *below* complex rule 54. Robust across hyperparameters (Spearman > 0.90 vs their own
  reference config). **Caveat:** that agreement with the Wolfram/Langton complexity
  classes is *qualitative* — no cross-class correlation coefficient is reported.
- **MNIST, unsupervised (no labels in training):** an encoder trained *only* to maximize
  learnable novelty reaches **linear-probe 0.53→0.89** and 5-NN 0.66→0.89 digit recovery
  (chance 0.10). Labels used only to color the t-SNE.
- **RL exploration:** as a dense intrinsic bonus (PPO, 10 continuous-control envs),
  task+novelty **beats task-reward-only on 9 of 10** (all but Walker2d) and collapses on
  none. **Caveat that matters most for us:** the *only* baselines are task-reward-only
  and a state-magnitude control — **no comparison to RND / ICM / count-based curiosity**,
  and only episodic return is reported, not exploration-coverage. The "beats prior
  curiosity methods" story is argued conceptually, not measured.

Two honesty flags for citing this: (1) the underlying quantity, **"epiplexity," is Finzi
et al. 2026 — not original here**; the contribution is reading it as an objective + the
cheap estimator. (2) The paper says "cheap/deterministic" (online cost `O(m²)` per step,
small reservoirs `m=32`) but **never claims CPU-only / no-GPU** — that's a reasonable
inference from the design, not a stated result.

## Why the method doesn't port to Prax

- **It's a gradient objective on a generator.** The MNIST/NCA/RL wins all come from
  *training* the system that produces the data. Prax doesn't train weights; its learning
  is scaffolding (memory, prompts, overlays, the self-regen loop). There is no place to
  put `∇ learnable-novelty`. This is the identical wall as the RLM note — same author,
  same "needs a training loop we don't have."
- **The estimator's *measurement* mode is trainless** (a random reservoir + a ridge
  solve — genuinely cheap), so in principle Prax could *compute a learnable-novelty score*
  over some signal without any GPU. But the paper's own headline limitation bites: **"our
  observer never grows."** A frozen random reservoir captures only shallow, linearly-
  readable structure and **saturates** once that's exhausted. As a literal scoring tool
  over LLM-scale semantic content it would be a crude proxy at best — not worth a
  dependency. The *idea* travels; the estimator doesn't.

## What's worth banking (the lens, not the code)

The load-bearing insight is **not** the reservoir math — it's the split that Prax's own
learning surfaces already blur:

1. **Memory consolidation — the weakest scaffolding cell.** The
   [self-improving-agents survey](self-improving-agents-survey.md) flagged *memory
   consolidation* as the least-developed cell in Prax's map. "Learnable novelty vs.
   noise" is exactly the missing selection criterion: **write to long-term memory the
   experiences with reducible structure worth compressing; skip the noisy-TV traffic**
   (flaky tool output, one-off chatter, irreducible randomness). Today the two-speed
   memory stack (Qdrant + Neo4j) has no principled "is this worth remembering?" gate
   beyond recency/similarity. This gives one — as a heuristic (surprising *and* recurring/
   compressible), not the literal estimator. 📋 experiment.
2. **Self-regen failure-prioritization.** The self-regeneration loop (#29) shouldn't burn
   iterations chasing *irreducible* failures — provider flakiness, genuinely ambiguous
   cases, noise. That's the noisy-TV trap in eval form: maximally "surprising" (a red
   test), zero learnable signal. This reinforces the existing
   [failure-provenance-diagnosis](arts-agentic-tree-search.md) adopt (bad-plan vs
   bad-execution) with a sharper frame: **prioritize failures with learnable structure;
   deprioritize the unlearnable.** Notably, *this session's* eval bug is the perfect
   example — 401s are pure noise, and a naive loop that "learned" from them would have
   optimized toward the error string. The health-guard we just shipped is the crude
   version of exactly this filter. 📋.
3. **A cleaner statement of the FEP framing already in the collection.**
   [active-inference.md](active-inference.md) leans on free-energy minimization; this
   paper is a direct, well-argued critique of pure FEP (the dark-room failure) and its
   fix. Worth cross-reading so Prax's "agent as belief-maintainer" framing doesn't
   silently inherit FEP's degenerate optimum.

None of these is a capability to build now — they're a **selection principle** to apply
when the memory-consolidation and self-regen work comes up. That's the honest size of it.

## Bottom line

A conceptually sharp paper with a real idea (separate learnable surprise from noise) but
a weights-level delivery Prax can't use, thin external baselines on the one result that
matters to us (RL exploration has no curiosity-method comparison), and a core measure
borrowed from prior work. **Document-don't-adopt the method; bank the learnable-vs-noise
distinction as the selection criterion for memory consolidation and self-regen failure-
prioritization.** Third Zhang-adjacent note to land on the same "great idea, GPU/weights
wall" verdict — the pattern itself is now a data point about where Prax's leverage is
(scaffolding, not objectives).
