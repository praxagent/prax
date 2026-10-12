# IdeaScientist: Orchestrating Agents for Grounded Scientific Ideation

**Verdict: document + adopt one honesty check; bank three lessons for the
eval and training lanes; don't adopt the system.**
- **What it is.** A research-ideation harness:
  - an orchestrator over a gap finder, an innovator that borrows mechanisms
    from other fields, and a report writer, with each role RL-trained
    separately;
  - a 2.7M-paper corpus of decomposed ideas;
  - a temporal cutoff that grades proposals against what researchers
    published later.
- **The headline doesn't survive its own appendix.**
  - "In-domain novelty" is a third of the novelty score and a sixth of
    "Overall". Appendix C defines it as a yes/no judgment of whether the
    proposal *imports a mechanism from a different problem setting*, which is
    exactly what the method's innovator is built to do.
  - Recomputed without that column, the 27B system no longer beats Claude
    Code on Opus or Codex on GPT-5.4.
  - The harness's lead over the best open baseline falls from 14.0 to 6.3
    points.
  - The training adds about one point.
- **Adopt:** read-before-cite. A source cited in a research answer should be
  one Prax read in that turn. Today `claim_audit.py` skips external citations
  by design.
- **Bank:**
  - a pairwise judge used as a reward collapsed to rewarding length (second
    sighting);
  - +10 points of per-role reward became about +1 at the system level;
  - temporal-cutoff grading is a grounded, contamination-resistant eval
    design.

Source: Liu, Tao, Liao, Jin, Sun, Yang, Zhang, Chen, Huang, Zhang, Yang, He,
Xuan, Wanga, Kumar, Diab, Yih, Dong, "IdeaScientist: Orchestrating Agents for
Grounded Scientific Ideation", [arXiv 2610.04074](https://arxiv.org/abs/2610.04074)
(October 2026). CMU and Meta (Reality Labs, FAIR), with UIUC, Toronto,
Princeton, Tokyo and RIKEN. Code
[github.com/jiarui-liu/IdeaScientist](https://github.com/jiarui-liu/IdeaScientist);
data [Svalbard Idea Vault](https://huggingface.co/datasets/Jerry999/SvalbardIdeaVault)
(CC BY 4.0 for the generated annotations, CC0 for arXiv metadata; the dataset
card lists 2.67M papers, the paper says 2.77M).

## What it is

- **The task:** research ideation, isolated from execution. Given a problem
  definition and a challenge current methods fail, write a grounded research
  proposal.
- **The harness:** a fixed orchestrator schedules three roles:
  - a **gap finder**, which finds the shared limitation in closely related
    work;
  - an **innovator**, which searches *other fields* for a mechanism that
    solved a structurally similar challenge, and adapts it;
  - a **report writer**, which fills a fixed proposal schema. The schema
    includes falsifiable predictions, and inline citation IDs that must be
    papers the writer read.

  Two second-tier helpers keep full texts out of the planners' contexts: a
  **paper reader** that answers questions about a full paper in its own
  context, and a **reviewer**.
- **The corpus:** 4.7M arXiv papers. 2.77M are decomposed into (problem,
  challenge, gap, intuition, proposal) tuples by an LLM.
- **The temporal design:**
  - retrieval is limited to papers from before 2026-01-01;
  - 14,977 later methodology papers are the "future";
  - 14,500 of them supply training references, 200 validation and 277 test.
- **Training:** a separate LoRA per role on Qwen3.6-27B, trained with
  asynchronous GRPO. The reward is gated on validity:
  - a third reference-free LLM rubric;
  - a third field-level agreement with the later human paper;
  - a third citation-set F1 against that paper.

  A 0.15-weight rule term penalizes protocol violations.

## Results (as reported)

- **Table 1** reports overall scores, judged by Qwen3.6-27B over 277 held-out
  papers:
  - IdeaScientist-Trained on Qwen3.6-27B: 74.6;
  - Claude Code SDK on Claude-4.8-Opus: 68.7;
  - Codex SDK on GPT-5.4: 69.5;
  - the best open autoresearch baseline on the same 27B, DeepScientist:
    60.6.
  - The gain "comes mainly from novelty", most strongly "against same-domain
    literature (+48.0%)".
- **The harness carries most of it.** Without training, IdeaScientist-Base
  already scores 73.5 on Qwen. The same harness on Opus scores 77.7, above
  both the trained 27B system and Claude Code SDK on Opus.
- **Ablations, on LLM-judged novelty:**
  - adding the gap finder, then the innovator, raises novelty against all
    literature from 0.595 to 0.66 on Qwen;
  - cross-domain retrieval adds 5.3 points;
  - their strictest measure ("mechanism non-obviousness") moves 33.5% →
    35.7%.
- **Judges:**
  - two expert annotators agree at r = 0.56;
  - the top LLM judges match the human average at r = 0.51–0.59;
  - self-preference is negligible for the Qwen judge (−0.003) and higher for
    Claude-4.8 (+0.048).

  The authors measured this rather than assuming it. Credit to them.
- **Humans:** a blind pairwise study, 50 papers, two annotators. The trained
  system beats most baselines. It **does not beat DeepScientist** (43% win
  rate), and its edge over the untrained harness is not significant.
- **Compute:** the trained system uses 11% fewer output tokens than the
  untrained one (169k against 190k per proposal), so its gain is not a larger
  budget.
  - Raising the per-role turn budget lifts the writer's reward from 0.29 to
    0.47, and the completion rate from 60% to 100%.

## What the table shows without the method's own column

Appendix C defines the three novelty columns:
- **All** and **Cutoff** are 1–10 judgments of how much the proposal adds
  beyond the closest papers.
- **In-domain** is different:
  > "the percentage of proposals that exhibit structural cross-setting
  > transfer. A proposal receives one when it imports a mechanism from a
  > different problem setting and adapts it through a shared underlying
  > structure"

That is the innovator's job description. The main text instead describes the
column as novelty "against literature in the same problem domain". The column
is a third of the novelty average, and novelty is half of "Overall".

Recomputed from Table 1 with that column removed, novelty is the mean of
All and Cutoff:

| System | Overall (reported) | Overall without In-domain |
|---|---|---|
| Claude Code SDK, Claude-4.8-Opus | 68.7 | **77.4** |
| IdeaScientist-Base, Claude-4.8-Opus | 77.7 | **81.5** |
| Codex SDK, GPT-5.4 | 69.5 | **78.2** |
| IdeaScientist-Base, GPT-5.4 | 71.9 | 75.2 |
| DeepScientist, Qwen3.6-27B | 60.6 | 67.8 |
| IdeaScientist-Base, Qwen3.6-27B | 73.5 | 73.3 |
| IdeaScientist-Trained, Qwen3.6-27B | 74.6 | 74.1 |

**What the recomputation changes:**
- **The abstract's claim fails.** "On this 27B open backbone, IdeaScientist
  even outperforms Claude Code SDK with Claude-4.8-Opus" now reads 74.1
  against 77.4. Against Codex on GPT-5.4 it is 74.1 against 78.2.
- **On GPT-5.4, the harness loses to Codex:** 75.2 against 78.2.
- **The lead over the best open baseline on the same 27B model** is 6.3
  points, not 14.0.
- **Two results survive.** The harness on Opus still beats Claude Code on
  Opus (81.5 against 77.4). And IdeaScientist-Base leads every open system on
  All and Cutoff novelty, on all three open backbones.

**Why it matters here:** this is our own "audit the checker first" rule,
seen from outside. A metric that checks whether the output did what the
method was designed to do will always favour that method. It should be
reported as a manipulation check, not counted in a quality score.

**Other limits:**
- **Human evidence:** 50 papers and two annotators, who agree with each
  other at r = 0.56.
- **Training is weak at the system level.** Per-role validation reward rose
  about 10 points, but the system gained about one, and humans could not
  separate the trained system from the untrained one.
- **Contamination:** the check queried 10 titles per model. The authors
  state it cannot detect method-level memory.
- **The references are LLM-built.** Every reference "proposal" is a Qwen
  rewrite of a human paper.

## Against Prax

| The paper | Prax |
|---|---|
| A read-before-cite gate: the writer may cite only papers it read. About half of early writer episodes failed it, and the authors changed the citation format in response, so part of that was formatting | **None for external sources.** `claim_audit.py` checks numbers against tool results, narrative grounding, and links to Prax's own artifacts; it skips external citations by design ("they legitimately never appear in tool output") |
| A judged penalty for "stating predictions as measured results" | The numeric claim audit flags figures absent from tool results. That partly covers it, but nothing targets a prediction written as an outcome |
| A paper reader in its own context, answering targeted questions | Spokes and the research agent read pages into the requesting context. Fetched pages are clipped, not delegated |
| Cross-domain mechanism search | Not a mode of the research spoke. The research lane does it by hand: several of our adopts came from another field's version of the same problem |
| A temporal cutoff as the held-out set | Prax's goldens are authored; [CRUX](crux-shadow-evals.md) and [FutureSearch](futuresearch-calibrated-forecasting.md) are the other grounded designs we have read |

## Adopt: read-before-cite for research answers

- **The check:** an external URL that a final answer presents as a source
  should be one Prax fetched, or at least saw in a search result, in that
  turn. A citation that appears in no tool call or result came from the
  model's memory.
  - That doesn't make it wrong, but it is unread.
  - The paper's gate failed about half of early episodes. Part of that was
    citation format, which the authors changed, so it is an upper bound on
    how readily a model cites what it hasn't read.
- **Where it goes:** a sibling of `audit_fabricated_links` in
  `prax/agent/claim_audit.py`, with three tiers:
  - **read:** fetched;
  - **seen:** in a search result;
  - **unread:** neither.
- **What it does:** log only at first, then annotate. It is flag-gated, like
  the claim audit's other layers.
- **Reuse:** the URL provenance guard (#276) already tracks which URLs a
  turn has seen.
- **Costs:** a false flag on a well-known URL quoted from memory; and a
  citation that names a paper by title rather than URL is not caught.

## Bank

- **A pairwise judge used as a reward collapsed to rewarding length**
  (Appendix B.2).
  - The comparative innovator reward ("is this field better than the
    reference's?") spiked to about 0.83 by favouring longer, more elaborate
    answers, then repeatedly collapsed.
  - The absolute, reference-anchored rubric stayed stable at about 0.66.
  - This is the second sighting, after [Co-Scientist's length
    exploit](gemini-co-scientist-real-world.md). It bears on the queued
    "taste signal in the accept-gate" row: a pairwise preference judge is
    safe as a reported signal, and unsafe as anything an optimizer pushes
    on, unless length is controlled.
- **Role rewards don't compound.** About +10 per-role validation reward
  became about +1 overall, and humans could not tell the difference.
  - For the GPU lane (the parked proposer in
    [harness-learning](harness-learning.md)): gate training on the
    system-level eval, never on the role reward that training raised.
- **The future as the held-out set.**
  - Grading against work published after a retrieval cutoff is grounded,
    because nobody authored the answer key for the test. It also resists
    contamination, as far as retrieval is concerned.
  - Its weak point is model memory, which a title probe cannot rule out.
  - This is the third design of this kind we have read, after CRUX and
    FutureSearch. A Prax research eval built this way would need a corpus
    with dates; this one is CC BY 4.0.
- **Analogy search as a research pattern.** Searching for the *challenge*,
  abstracted from its field, instead of the topic is a cheap prompt-level
  pattern for the research spoke.
  - The measured gain is modest: 5.3 points of LLM-judged novelty.
  - Its strongest number is the column above that rewards it by definition.
  - Not worth building before Prax has research-quality eval coverage.

## Don't adopt

- **The system:** three role-specific 27B LoRAs trained with GRPO on a 4.7M
  paper corpus. Research ideation is not Prax's product.
  - The part that transfers, the harness, is carried by the untrained
    variant.
  - The part that needs the GPU added about a point.
- **"In-domain novelty" as a metric.** Any eval of ours that checks whether
  an output followed the method's design is a manipulation check, and it
  stays out of the quality score.

## Related

- [Co-Scientist in the real world](gemini-co-scientist-real-world.md): the
  first length exploit by an optimizer.
- [ProofJudge & taste](proofjudge-taste.md): the pairwise taste signal the
  bank item above bears on.
- [CRUX shadow evals](crux-shadow-evals.md),
  [FutureSearch](futuresearch-calibrated-forecasting.md): grounded evaluation
  by construction.
- [Judge noise floor](judge-bias-audit-2026-08-20.md): our own measurement
  of how far judges disagree with themselves.
- [Harness learning](harness-learning.md): the parked GPU plan the
  role-reward lesson applies to.
