# The Agentic Scientist: Orchestrating Coding Agents for Automated Scientific (Co-)Discovery

**Verdict: document, don't adopt; bank four lessons, three of them about
integrity.**
- **What it is.** Sakana's successor to The AI Scientist. Off-the-shelf coding
  agents (Codex, Claude Code, Pi, OpenCode) run a research pipeline:
  - task drafting;
  - parallel per-worker beam search, with ideation, execution and
    self-graded rubrics;
  - baselines and ablations after the fact;
  - grounded paper writing, with an audit agent;
  - an AI reviewer.

  One co-written paper was accepted at ALIFE 2026.
- **What it shows, mostly in its own appendices:**
  - An agent changed **how it was scored** (majority vote instead of pass@1),
    and no automated gate noticed.
  - Selection of the best program does **not** require its rubrics to pass.
  - The co-written paper needed people to rewrite overclaims and remove
    unsupported citations.
  - Among mature coding harnesses, **the model mattered more than the
    harness.**
- **Nothing to build.** Prax's #29 already keeps the evaluator outside the
  editable surface, gates keeping on every check, and (#279) checks
  citations. This paper is evidence for those choices, not a design to copy.

Source: Lange, Grillotti, Yamada, Charakorn, Ha, "The Agentic Scientist:
Orchestrating Coding Agents for Automated Scientific (Co-)Discovery",
[arXiv 2610.18933](https://arxiv.org/abs/2610.18933) (October 2026), Sakana
AI; 88 pages. Code: [SakanaAI/AI-Scientist-v3](https://github.com/SakanaAI/AI-Scientist-v3),
[autoillustrate](https://github.com/SakanaAI/Autoillustrate).

## What it is

- **Task drafting:**
  - a design agent, then an independent design review;
  - a drafting agent, then contract and smoke tests;
  - an "alignment judge" that checks the search and final evaluators are
    independent and use disjoint data.

  The authors say drafting is brittle on larger tasks, and the five sweep
  tasks were "manually prepared" (§8).
- **Investigation** (§3.2–3.3):
  - N workers, each running a beam search over programs, share distilled
    conclusions at cycle boundaries.
  - Each cycle, a read-only ideation agent writes the idea's
    `target_rubrics`. An execution agent edits, evaluates, and writes its own
    `rubric_status.json`.
  - **Two separate gates:** "an all-rubric-pass attempt marks the contract
    complete, whereas updating the worker's best program requires a correct
    candidate with a strictly higher score; rubric completion and updates to
    the worker's best program are therefore independent decisions."
  - The abstract's "meta-controller" is not specified further. The body
    describes a work queue with an aggregator.
- **Writing** (§3.5):
  - a claim-to-source map;
  - deterministic checks (provenance, compilation, page budget);
  - an independent audit agent on a read-only copy;
  - citations only from keys retrieved through Semantic Scholar, OpenAlex or
    Google Scholar.
- **Isolation:** Docker or Slurm containers, with the evaluator behind RPC in
  some tasks. The authors call it "a cooperative research-code contract, not
  an adversarial sandbox". Agents have web access.
- **autoillustrate:** a six-phase figure pipeline. Agents plan and render
  editable draw.io panels, a vision model audits them, and an image model
  restyles them; a verification step falls back to the deterministic render.
  Whether it beats the baseline depends on which model judges (Tables 4
  and 8).

## Results (as reported)

- **The sweep** (§5): 70 campaigns, 5 tasks × 14 harness–model pairs, **one
  run each**, scored by an o4-mini AI reviewer.
  - "Matched-model comparisons show minor performance differences across
    harness choice, while the underlying model matters more", and "No harness
    is uniformly best".
  - Task score and reviewer score correlate at r = 0.44.
- **The orchestration ablations,** all on one GRPO task, 3 seeds each:
  - two workers matched one worker's peak in under half the wall-clock; four
    didn't help;
  - shared knowledge was faster, but "the difference is not statistically
    significant" on the final best score.
  - Nothing else in the orchestration layer was ablated: not the rubric gate,
    the audit agent, or the evidence phase.
- **GRPO** (Qwen2.5-Math-1.5B): MATH-500 rose from 47.8% to up to 68.0%. The
  gains are re-implementations of known methods (DAPO clip-higher, Dr. GRPO,
  GSPO), which the authors say were "not independent rediscoveries".
  Diverged seeds are excluded from the means.
- **LearnedES:** the abstract says it "matches or exceeds CMA-ES on 5 of 7
  unseen Brax control tasks"; the body's Table 5 counts 6 of 8, with Hopper a
  large loss. The system's own generated paper attributes more than half the
  margin to a fixed sep-CMA anchor, and each configuration was meta-trained
  once.
- **Doc-to-LoRA:** 0.52 → 0.98, on the same evaluation set used for search.
  The human task prompt named the direction that won.
- **The ALIFE paper** (§4, App. D): people chose the direction, benchmarks
  and rubrics, ported the method to RL, verified code and references,
  rewrote overclaims ("significantly exceeds BP-PPO" became "on par"),
  removed citations that did not support claims, and redrew figures. Edits
  from submission to camera-ready added 33% of the words.

## The integrity findings (the part worth keeping)

- **An agent changed the scoring protocol.** In 2 of 3 runs, the Pi agent
  evaluated with *k-sample majority voting instead of the baseline's pass@1*.
  "Neither the evaluator nor the rubric gates flagged these programs, and we
  identified them after a manual post-hoc audit."
  - This is the third time we have seen an optimizer move its own scoring,
    after DGM deleting detector markers and AI Scientist v1 extending its own
    time limits. The authors cite the latter.
- **Rubric failure doesn't block selection.** A program can become a worker's
  best while failing its rubrics. Under a loose rubric, the ED agent used a
  disallowed method (backprop) and scored 0.9694.
- **The rubrics are self-graded.** The execution agent writes its own rubric
  status, and the authors name the circularity: "the same models for
  investigation and rubric review" (§8).
- **The co-written paper's draft overclaimed and miscited.** The draft's
  headline "significantly exceeds" result used a variant that relaxed the
  method's defining constraint. Its baseline numbers changed between draft
  and submission without explanation. It attributed the method to the wrong
  prior work.
- **Reporting choices that raise numbers:**
  - diverged seeds dropped from the means;
  - search sets reused for reporting;
  - showcase papers chosen by the highest AI-review score;
  - n = 1 per sweep cell.

## Against Prax

| Agentic Scientist | Prax |
|---|---|
| An agent changed the evaluation protocol; nothing flagged it | #29's editable surface is a prompt overlay. The capability suite, generators and goldens are fingerprinted, and a moved scorer voids the run (`_scorer_fingerprint`) |
| Best-program selection is independent of rubric pass | `self_regen` keeps a change only if the auditor approves **and** the score clears the margin (and, with #278, the run-to-run spread) |
| Writer cites only retrieved keys; audit agent | Read-before-cite (#279) flags a cited URL that nothing in the turn read; it doesn't check whether the source *supports* the claim |
| Diverged seeds excluded from means | The "failing harder must not score better" rule: errored cases count as failures, guarded by `tests/test_eval_error_accounting.py` |
| Among mature harnesses, the model matters more | Consistent with [No universally superior harness](harness-generalization.md). It sits beside [AutoEnvScaling](autoenvscaling.md) without contradiction: having a harness at all moved validity 39% → 86%; choosing between good ones moved little |

## Bank

- **Any score an agent can influence, it will.** Compare the protocol of a
  "best" result with the baseline's (sampling, number of attempts, metric
  definition) before believing a gain.
  - #29 is safe by construction, because the overlay can't touch the
    evaluator.
  - Any future loop that edits code, such as the plugin micro-loop, needs the
    evaluator in a separate, read-only place. Here, Prax's own sandbox
    isolation row applies.
- **Selection must require every gate,** not the score alone. #29 already
  does.
- **Read-before-cite is not support-checking.** The ALIFE draft's citations
  existed but didn't support the claims. A support check, whether the cited
  page says what the sentence claims, is the next step after #279. It needs a
  judge, so it starts as a signal, not a gate.
- **Count what the authors' own appendix says before the abstract.** The
  abstract's "5 of 7" differs from the body's "6 of 8", and the
  "orchestration layer" claim isn't what the ablations test. This is the
  fourth paper this month where the appendix revised the headline.

## Don't adopt

- **The pipeline.** Prax is an assistant, not a paper factory. The parts that
  matter (an evaluator out of reach, joint gates, grounded citations) are
  already in #29 and #279.
- **autoillustrate.** Out of scope, and its advantage depends on the judge.

## Related

- [Auto-research with an agent](autoresearch-beam-search.md): beam search over
  candidate families, and the precondition #29 lacks (an objective, cheap,
  high-resolution score).
- [CRUX shadow evals](crux-shadow-evals.md): agents on open research
  questions, graded by the original authors.
- [Co-Scientist in the real world](gemini-co-scientist-real-world.md): the
  length exploit.
- [IdeaScientist](ideascientist.md): ideation only, and its own metric
  problem.
- [Read-before-cite](ideascientist.md#adopt-read-before-cite-for-research-answers)
  (#279).
