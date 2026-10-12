# Harness Learning Enables Generalizable Test-Time Adaptation

**Verdict: document + adopt two changes to the #29 loop's protocol; decline the
training.**
- **What it is.** A small model (the *proposer*) is trained with RL to rewrite
  a frozen agent's harness code from execution feedback. At test time it keeps
  revising the harness of a task it never trained on.
- **Why it matters to Prax.** It is the third trained harness editor in the
  tracker, after [Harness-R1](harness-r1.md) and
  [HarnessCompass](harness-compass.md). It is the first to measure what Prax's
  dormant self-regeneration loop (`prax/eval/self_regen.py`, IDEAS_BACKLOG #29)
  gets wrong in its protocol, as opposed to its safety rules.
- **Adopt 1: show the proposer some cases and score it on others.** Three
  disjoint question sets: feedback, scoring, held-out. Prax's loop has
  held-out, but it hides the cases from the proposer and scores it on the same
  suite that produced its signal.
- **Adopt 2: revise the kept candidate, with fresh feedback from it.** Prax
  draws every candidate independently from one fixed signal.
- **Decline:** training a proposer (GPU wall). Prax's proposer is a hosted
  frontier model.
- **Correction to an existing row:** Harness-R1's "rerun the same tasks"
  reward needs the disjoint split. Rerunning the batch whose failures the
  proposer saw rewards fixing exactly those cases.

Source: Zhang, Liu, Wang, Tajwar, Arora, Salakhutdinov, Khashabi, Song,
Zanette, "Harness Learning Enables Generalizable Test-Time Adaptation",
[arXiv 2609.35738](https://arxiv.org/abs/2609.35738) (September 2026). Compute
acknowledged from Johns Hopkins (ARCH) and Carnegie Mellon (FLAME, Babel),
plus DOE leadership facilities.

## What it is

- **The setup.** A *harness* is a Python program that organizes a frozen
  solver's calls, tools and information flow. The proposer reads three things:
  - the task description;
  - the current harness;
  - an execution report: the score, failed examples with trace excerpts, and
    the revision history.

  It answers with search/replace edits and a change manifest.
- **Framed as meta-learning.**
  - **Inner loop:** the proposer revises a harness over rounds.
  - **Outer loop:** RL trains the proposer.
  - Harness edits play the role that weight updates play in MAML.
- **Training** is GRPO on the proposer only, with the solver frozen.
  - The reward is the revised harness's score on a scoring set, plus small
    validity terms. A harness that runs but answers everything wrong earns 0.30
    on Reasoning Gym, and gets +0.1 on QA.
  - During training, **the scoring questions are disjoint from the questions
    whose execution report the proposer saw.**
- **Three question sets per task:**
  - Q<sub>fb</sub> builds the report;
  - Q<sub>score</sub> selects among candidates;
  - Q<sub>eval</sub> is held out and never shown.
  - In Reasoning Gym *evaluation*, feedback and scoring share 25 development
    questions, with 75 held out. In QA they are disjoint within each round:
    6 feedback, 64 scoring, about 300 held out.
- **Two settings:**
  - **Reasoning Gym:** a Qwen3.5-4B proposer and solver; SFT on 432 revisions
    from a Qwen3.6-35B teacher, then RL. 21 families are excluded from both
    training stages. The seed harness is one solver call.
  - **Multi-hop QA:** a Qwen3-4B proposer and Qwen3-8B solver, RL straight from
    the base model. Trained on HotpotQA; tested on MuSiQue and 2WikiMultihopQA.
    The seed is a two-hop retrieve-and-summarize workflow over BM25.
- **The prompt** asks the proposer to diagnose failures and make a targeted
  change "without hard-coding answers". The SFT corpus was screened for answer
  echoing, and re-executed by Claude Opus 5 reviewers checking for oracle
  access, memorization and grader exploitation.

## Results (as reported)

- **Revision quality transfers.** On the 21 unseen families, the mean
  single-revision held-out score rises from 0.32 (base) to 0.49 (SFT) to 0.62
  (RL). The 35B teacher scores 0.56 (Table 9).
  - Over all 26 unseen families, the five hardest included: 0.282 / 0.415 /
    0.528, against 0.501 for the teacher.
  - **Failed or zero-scoring proposals** on unseen families fall from 38%
    (base) and 35% (SFT) to 8% (RL) (Table 10). Most of the gain is
    reliability: more candidates that run at all.
- **Sequential revision of the kept harness beats independent draws** at the
  same number of proposals.
  - **Reasoning Gym:** five sequential proposals, each kept only if the
    development score improves, approach or exceed the oracle best of eight
    independent ones (Fig. 4c).
  - **QA, MuSiQue:** independent revisions average 0.15 exact match, barely
    above the 0.14 seed. Ten rounds of revising the kept harness reach 0.27.
  - **Both unseen QA benchmarks:** the average final ten-round run beats the
    oracle best of 80 independent proposals (Fig. 5).
- **Training on revision sequences adds nothing consistent** over training on
  single revisions. Multistep RL wins on canonical questions, loses on
  format-varied ones, and ties on QA. Iterative improvement emerges from
  single-revision training.
- **What the revisions build:**
  - **Reasoning Gym:** mostly *interpreter loops*. The solver writes code and
    the harness runs it: 77–78% of revisions on unseen families (Fig. 6b).
  - **QA:** three kinds of rewiring:
    - the answering call reads the retrieved passages directly, instead of
      the summaries;
    - a third retrieval hop;
    - a verification branch: accept a draft answer only if it appears in the
      retrieved passages, and otherwise search again with the draft.
- **Composition mostly fails at the interfaces.** Harnesses asked to integrate
  a helper tool into the solver's loop seldom get any use out of it. The
  helper's result rarely reaches the answer. The failures surround a *correct*
  helper:
  - question parsing;
  - malformed tool messages;
  - answer extraction (Table 20).

**Limitations the authors state:**
- one teacher;
- prescribed revision prompts;
- a fixed solver;
- small models only;
- the sequential-versus-independent comparisons don't isolate the
  contribution of feedback, because parent and selection differ too;
- the QA best-run comparison is not compute-matched: 320 proposals against 80.

## What the paper shows that it does not headline

- **A fixed, hand-written harness beats the average learned revision.**
  - Table 9's "Tool loop" column is the same Python execution loop of up to
    five turns for every family. It scores **0.69** on the 21 unseen families.
  - That is above the trained proposer's mean single revision (0.62) and the
    teacher's (0.56).
  - Only the oracle best of eight beats it (0.83), and that oracle selects on
    held-out scores. The paper itself says it is not a deployable rule.
  - The main figures compare against the one-call seed (the dashed line), not
    against this loop.
  - Most learned revisions *are* interpreter loops, so on Reasoning Gym much of
    what the proposer learns is to rediscover a known-good generic harness.
  - The QA results are the stronger evidence. The seed there is a real
    workflow, and the revisions change it in ways no fixed baseline already
    did.
- **The extra multistep families were partly chosen by the result.** Six were
  picked where single-step RL beat SFT, SFT beat base, and RL's best of eight
  reached the tool loop. The remaining six only had to show both trained
  proposers beating base (Table 12). The authors disclose this.
- **The main figures drop five unseen families** that every model found hard.
  The all-family numbers above still keep the ordering.
- **Rewards beat directives.** The authors prompted for helper-tool
  composition and rewarded it.
  - Under a tool-use reward, composed tool loops fell from 25 of 40 draws to 1
    as training went on.
  - Under the harder-task reward with a composition bonus, no recorded rollout
    ever earned the bonus.
  - The reward-maximizing structure (an interpreter loop) won over the
    instruction.

## Against Prax

| The paper | Prax's #29 loop (`prax/eval/self_regen.py`) |
|---|---|
| A frozen solver; only the harness changes | The same: the loop edits only a system-prompt overlay |
| Proposer input: harness + report with **failed examples and trace excerpts** | `_weak_signal_from_baseline`: **dimension averages only** ("Lowest-scoring capability dimensions — content: avg …; harness: avg …"), "deliberately WITHOUT the specific cases (so the proposer can't memorize them)" |
| Feedback set ≠ scoring set (disjoint in training and in QA) | The proposer's signal and the keep decision come from **the same capability suite** |
| A held-out set, never shown | ✅ the private golden split, through `accept_change`, with a measured noise floor (`_gate_on_private_holdout`) |
| Each round revises the **kept** harness, with fresh feedback from it | Every round calls `pr(signal)` with **the same signal**. Candidates are independent draws; none sees the current best |
| Retention: Reasoning Gym keeps a candidate only if it improves; QA advances to the best candidate even when worse | Keeps a candidate on a margin of at least one case, or on an Occam tie (shorter at equal score) |
| A prompt directive against hard-coding, plus screened data | `inoculate()`, an LLM auditor, and `_deterministic_spike_veto` |
| Scorer out of reach | ✅ `_scorer_fingerprint` before and after; a moved scorer voids the run |

**A gap found while checking:** `_deterministic_spike_veto` collects answer
tokens only from `contains`/`absent` checks. The capability suite checks
84 of its 95 checks with `regex` (57) or `regex_absent` (27), so the veto set
is two tokens (`526`, `536`). Answers checked by regex, such as the `604` in
`computation_aggregate_exact`, are not in it. That matters little while the
proposer is shown no cases. It becomes a precondition for Adopt 1.

## Adopt

### 1. Three disjoint sets: show failures, score elsewhere

Prax's anti-memorization defense today is **secrecy**. The proposer is told
only which dimension is weak, so it has nothing to fix *with*: an average names no
failure mechanism.

The paper's defense is **measurement**:
- show the proposer real failures with traces, from a feedback set;
- keep a change only on a disjoint scoring set;
- report on a third, held-out set.

A change that fixes only the cases it saw earns nothing on the scoring set.
That makes the anti-spike rule a property of the protocol rather than of the
proposer's restraint. It is the same move as [HarnessCompass](harness-compass.md)
(constrain what may be proposed) and [Weng](weng-harness-engineering.md) (scorer
read-only), applied to *which cases count*.

**Precondition: enough cases per family.**
- The split has to be *within* a family. A fix for an honesty case can only
  show up on another honesty case.
- The capability suite has 30 hand-written cases in 10 families: honesty 12,
  computation 4, and one to three in each of the rest.
- At that size, splitting leaves single-case families with nothing to score
  on, and halves the keep decision's resolution elsewhere.
- **The workable path is family generators.** This is the "generator +
  deterministic verifier" row the tracker already holds for ARC, applied to
  the capability families that are parametric. The computation cases are: lists
  with exact sums and medians, percentages, unit conversions.
- Until then, a split within the honesty family alone is a pilot.

**Also needed before cases are shown:** the spike veto must read answer
literals out of `regex` checks, not just `contains`/`absent`.

### 2. Revise the kept overlay, with fresh feedback from it

- Each round should give the proposer:
  - the current kept patch;
  - a report from running *it* on the feedback set.

  Today every round is an independent draw from one fixed signal.
- **What the paper reports:** at a matched proposal count, revising the kept
  harness matched or beat the *oracle* best of N independent proposals, on
  both settings.
- **Caveat:** the comparison changes the parent and the feedback together, so
  it does not prove feedback is the cause. It is still the paper's most
  consistent result.
- **Keep Reasoning Gym's retention rule** (only on improvement), not QA's
  (advance even when worse). [Harness-delta attribution](harness-delta-attribution.md)
  attributed most automated harness-evolution gain to overfitting (98% on
  ALFWorld, 79% on LiveMath) or test-time scaling (73% on CREATE), so #29's
  gate stays rejection-biased.
- **Cost:** one feedback-set run per round on top of the scoring run. With
  generated families, the feedback set can be small: the paper's QA used six
  questions.

### Lens, not code: compare against the best fixed harness

The paper's main comparison is against a one-call seed. A generic tool loop
already beat the average learned revision. Two consequences:
- **For #29:** the baseline must stay the *current production
  configuration*. It already is: the loop's baseline is `ev("")` on the live
  base prompt.
- **For reading the next harness-evolution paper:** ask what the strongest
  fixed harness scores before crediting a learned one. This adds to the HDA
  row's "compute-matched baseline" requirement: matched compute, and the best
  known fixed design.

## Bank

- **Rewards beat directives under selection.** The composition experiments
  are clean evidence: what was rewarded won over what was asked for.
  - For #29, `inoculate()` and the proposer prompt's "abstract the class"
    are directives.
  - The guarantee has to come from the split, the gate and the veto. The
    loop's design already assumes this; this is measured support for it.
- **Wired in is not used.** A helper placed in the tool loop seldom
  contributed to the answer, and the failures were at the interfaces around a
  correct helper.
  - Prax writes its own plugins (`plugin_write`).
  - The question to ask of a self-written tool is whether its result reaches
    the answer, not whether it registers.
  - Traces can count this: the tool was called, and its output was used
    downstream.
  - See also the 2026 "harness updating is not harness benefit" line the
    paper cites (Lin et al., arXiv 2605.30621).
- **The grounding branch.** In QA, a draft answer is accepted only if it
  appears in the retrieved evidence; otherwise it becomes the next query.
  - It is a cheap deterministic check for extractive questions.
  - It is a candidate for the research spoke, behind the same kind of flag
    as the claim audit.
- **Small trained proposers can beat their teacher on average** (4B over
  35B), while the teacher keeps the better best-of-eight. If #29 ever runs at
  volume, the proposer need not be the most expensive model. Selection,
  meaning the split and the gate, is what carries quality.

## Don't adopt

- **Training a proposer.** GRPO on a 4B model is the same GPU wall as the
  other weights-level assessments, and Prax's proposer is a hosted model.
- **Interpreter loops as a discovery.** Prax's agents already have
  `run_python` and the sandbox. This is the structure the Reasoning Gym
  proposers mostly rediscover.

## Related

- [Harness-R1](harness-r1.md): the closest prior work, with the same recipe
  (frozen target, SFT then GRPO). This paper's appendix A notes that R1's
  reward reruns the batch that supplied the failure feedback, while its own
  scoring questions are disjoint from the feedback questions. That is the
  correction to R1's tracker row.
- [HarnessCompass](harness-compass.md), [Weng](weng-harness-engineering.md):
  the other two invariants of #29 P1 (task-agnostic patches; read-only
  scorer). The disjoint split is the third, and the only one about data.
- [Harness-delta attribution](harness-delta-attribution.md): why the gate
  stays rejection-biased.
- [No universally superior harness](harness-generalization.md): sequential
  best-of-N was the baseline nothing beat there. Revising the kept candidate is
  a close relative.
- [Prime Agent](prime-agent.md): the "wire `self_regen`, it has zero consumers"
  row. Both adopts here change that loop before it gets a consumer.
- [Learnable novelty](learnable-novelty.md): which failures are worth
  showing the proposer at all.
