# AutoBenchmark (Meta RAM): can an agent build a benchmark for agents?

**Verdict: document + adopt two checks; don't run the loop.** Its headline
result is one Prax already lives by, now measured: left alone, an agent builds
benchmarks that are nearly saturated. Human direction helps in proportion to its
specificity. Two things are worth adopting for any case an LLM helps author
(TJ's synthetic ARC variations, praxbench, the private battery):

1. **Judge difficulty on a solver from outside the loop.** Pick with one model
   and report with a model from another provider.
2. **Treat "hard for every solver" as a reason to audit the answer key, not as
   proof of difficulty.** The post doesn't say this, but its selection rule
   makes it necessary (below).

Source: [AutoBenchmark](https://facebookresearch.github.io/RAM/blogs/autobench/),
a blog post from Meta's RAM team (Chuanyang Jin, Tianjian Li, Chenxi Whitehouse,
Jason Weston, Weizhe Yuan, Ilia Kulikov, Swarnadeep Saha, Jack Lanchantin et
al.), September 2026. The technical report is "planned" for arXiv, and no code
or data is released yet, so every number below is from the post.

## What it is

A loop in which a research agent builds a benchmark meant for other research
agents:

1. **Propose.** From a task specification and grounding material, the agent
   defines a construct, turns it into runnable tasks, and writes reference
   solutions and grading.
2. **Solve.** Solver agents attempt the tasks (4 CPUs, 16 GB, a 24-hour budget
   per task). Their scores are the difficulty signal.
3. **Review.** LLM judges score five criteria: construct validity, correctness,
   feasibility, usefulness, and an overall verdict. Only iterations that pass
   all five seed the next one. The kept checkpoint is the passing one with the
   **lowest solver scores**.

**Models.** Muse-Spark-1.1 is the proposer, one of the two solvers inside the
loop (with Muse-Glimmer-30B), the answer judge, and all five quality judges.
Two solvers sit outside the loop: Nemotron-3.5-Lightning-30B-A3B after every
iteration, and Claude Opus-5 on the selected checkpoint.

**Three benchmarks.**
- **Graveyard Bench:** does the agent avoid research directions already known
  to be dead ends (medical reversals, failed trials, replication failures)?
- **SilentTrain Bench:** can it find training bugs that degrade results
  silently?
- **Rebuttal Bench:** does a rebuttal actually resolve a reviewer's point?

## Results (as reported)

| Setting | In-loop solver (Muse Spark) | Nemotron (outside the loop) | Opus-5 (outside the loop) |
|---|---|---|---|
| Unaided | above 80 | — | 98.0 |
| Rebuttal, coarse feedback (a one-sentence intent) | 84.4 | 81 | 83.1 |
| Rebuttal, fine feedback (a detailed spec + curated grounding) | **43.5** | **39.1** | 65.9 |
| Graveyard, coarse | up to 13.8 points lower | 75.4 | 90.7 |
| Graveyard, fine | 28–46.5 points lower | 52 | 84.4 |
| SilentTrain, without / with human execution guidance | 88.1 / 64.2 | — | — |

Their reading: "Human feedback helps substantially, and the gain grows with how
specific that feedback is." Detailed direction "halves the score of the
solvers, while a brief statement of what to build helps only marginally".

The verifier caught defects the score couldn't see:
- answers leaking into files the solver can read;
- constructs too shallow or too memorised to separate solvers;
- grounding material the creator claimed to use but didn't retain.

In one run, an iteration scoring 47.4 would have seeded the next and was
stopped by the feasibility criterion.

Their stated limit: "deciding which problems are meaningful and worth making
difficult is a judgment we do not yet know how to delegate."

## Caveats

- **One model in three roles.** The proposer, a solver inside the loop, and
  every judge are the same model. The solvers outside the loop guard against
  over-fitting to the loop's own solvers. They do not guard against a judge
  that shares the proposer's blind spots: the shared-model correlated failure
  from [ibm-ai-agents](ibm-ai-agents-primer.md) and the judge noise in
  [judge-bias-audit](judge-bias-audit-2026-08-20.md).
- **Selection by lowest score rewards broken items.** A task whose reference
  answer is wrong, or whose grading is too strict, scores low for *every*
  solver. So agreement between solvers inside and outside the loop cannot tell
  real difficulty from a wrong key. The correctness judge is the only defence,
  and it is the proposer's own model. The post reports no human check of the
  reference answers and no count of human hours. This is our standing
  "[audit the checker](eval-scorer-audit-2026-08-07.md)" lesson: four times, a gap in
  Prax's scores turned out to be our scorer.
- **Opus-5 stays high.** 83–91 on four of the five selected checkpoints. The
  difficulty is partly specific to the solvers that were measured, which is
  the saturation pattern in [benchmark-saturation](benchmark-saturation.md).
- **Blog-level evidence.** No report, code or data yet; one run per setting
  as far as the post shows.

## Against Prax

| AutoBenchmark | Prax |
|---|---|
| Unaided benchmarks saturate; human specificity is what makes them hard | **Agrees.** [benchmark-saturation](benchmark-saturation.md) found resilience tracks *expert curation*, not privacy. This is the same finding from the building side |
| A solver outside the loop, after every iteration | **Missing for authored cases.** The #29 gate splits instances public/private, but the same model family writes, solves and judges |
| The verifier checks for answers leaking into solver-readable files | **Known gap.** Eval isolation is a precondition for #29 (see the eval-hillclimbing assessment, PR #245): the sandbox's `/workspace` mount and the source tools can reach `../prax-evals` |
| Keep the hardest passing checkpoint | **Not done, and risky** without an independent check of the answer key |
| The human writes a detailed spec and curates grounding | TJ's ARC plan has him generate synthetic variations with an LLM; the post says a one-line intent barely helps. Fine-grained specs per family are the part worth his time |

## Adopt

**1. A cross-provider difficulty check for authored cases.** Any case an LLM
helped write (ARC synthetics, praxbench items, battery cases) records its
difficulty on a solver from a *different provider* than the one used to write
or select it. We would report both numbers, and never select on the second.

**2. "Hard for all" means audit the key.** A case that every solver fails
goes to a person, who checks the reference answer and the grader before the
case counts as hard. This is a small rule for the battery's README and the #29
accept gate. It pairs with the existing discrimination check (#245).

## Bank

- **The verifier's three defect classes** as a checklist for generated cases:
  a leaked answer, a construct too shallow to separate solvers, and claimed but
  missing grounding.
- **Spend the human time on specification, not on generation.** One detailed
  spec plus a curated source list beats many one-line prompts.

## Don't adopt

- **Running the loop.** No code; 24-hour, 16 GB solver budgets per task; and
  our own suite fails the discrimination check (medium = low tier, 6/7). Fix
  the measurement before generating more of it.
