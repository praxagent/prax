# ACM — Agentic Context Management for Long-Horizon Tasks (arXiv 2607.23809)

**Li, Ming, Chu, Shao, Jin, Xiong.** Two contributions in one paper: a
**scaffolding mechanism** (agent-initiated context compression with lossless
offload) and a **post-training recipe** (teacher-guided distillation that
teaches a 9B model *when* to compress).

**Verdict: document + adopt the mechanism's shape as the concrete design for the
already-queued "memory pressure" row; document-don't-adopt the post-training
(the usual GPU wall).** This is the third paper in the MemGPT lineage to land
here in two days, and the most actionable, because its mechanism is two tools we
can build rather than a platform or a training run.

---

## The mechanism

Two tools, and a policy about who calls them:

| Tool | Semantics |
|---|---|
| `manage_context` | Compress previous turns into an LLM-written summary; **offload the raw messages to external storage under an id** — compression is a demotion, not a deletion |
| `query_memory` | Retrieve archived raw messages by id; a querier LLM extracts what is relevant to the current question |

The policy is the actual contribution: **the agent decides when**, from its own
reasoning state — no fixed threshold, no external monitor truncating behind its
back. Their post-training then teaches that judgement: inject compression where
it would have helped in failed rollouts, remove premature compression from
successful ones (teacher: Qwen3.5-397B; student: Qwen3.5-9B; on-policy
distillation, 3 epochs).

## Reported results (not independently verified)

| Benchmark | ACM (post-trained) | ReAct baseline |
|---|---|---|
| BrowseComp-Plus | 0.727 | 0.570 |
| DeepSearchQA | 0.425 | 0.367 |
| SWE-Bench Verified | 0.530 | 0.489 |

Plus lower peak token usage and more consistent solutions across trials. Code,
data and checkpoints are public. Their own caveats are unusually honest and
worth keeping: **weak base models collapse into short trajectories before
compression ever matters** (so the mechanism amplifies strength rather than
rescuing weakness), and the compression baselines were reimplemented, not
original.

## What this changes for Prax

The [MemGPT note](memgpt-virtual-context.md) left "memory pressure as a signal"
queued but underspecified — *tell the agent its context is filling before
truncating*. ACM supplies the missing design decisions, and one of them
corrects the row as written:

1. **Not a pressure warning — a capability.** ACM's agent doesn't react to a
   fullness signal; it owns compression as a first-class action. The row should
   be built as two governed tools (`context_compact` / `context_recall`), not as
   a prompt injection saying "you are at 80%".
2. **Lossless by construction.** Offload-then-summarise, never
   summarise-then-discard. Prax already has the storage side (the progress
   detail files demoted to `.progress/` are exactly this pattern at session
   scale); ACM applies it *within* a turn.
3. **The eval question is pre-registerable.** This lands the same week as
   `prax/eval/prereg.py`, and it is a perfect first customer. Kill condition,
   written before any run: *if agent-initiated compaction does not reduce peak
   tokens on long-horizon goldens without reducing private pass-rate, the flag
   dies.* Their "weak models collapse first" caveat sharpens it further: test on
   the cheap tier too, and expect the mechanism to do *nothing* there — if it
   appears to help the cheap tier, suspect the measurement before the miracle.

## What not to adopt

**The post-training pipeline.** Teacher-guided on-policy distillation into a 9B
— the same GPU wall as RLM, lm-sleep, MORPHEUS, ARTS, learnable-novelty. That's
now six assessments hitting the identical wall, which is itself the strongest
standing evidence for Prax's scaffolding-over-weights bet. The scaffolding half
of ACM stands alone: their ablations attribute real gains to the mechanism even
un-post-trained (on strong bases), which is the configuration Prax runs in.

**Agent-deletable history.** Same line as MemGPT: offload is additive and
auditable; deletion stays governed. An agent that can silently rewrite its own
context can rewrite what the auditor sees.

## Honest limits of this reading

Read via two summarising fetches of the abstract and HTML; numbers are
transcribed from the fetch, not from the tables directly, and no baseline was
re-run. The public checkpoints were not downloaded. The claim I am most
confident in is the mechanism's shape; the claim I am least able to vouch for
is the exact magnitude of the deltas.

## Related

- [memgpt-virtual-context.md](memgpt-virtual-context.md) — the lineage; ACM is
  MemGPT's paging made agent-initiated and lossless, with receipts.
- [ilands-grounding-gap.md](ilands-grounding-gap.md) — the prereg discipline
  this note's eval plan uses.
- [rlm-harness-lid.md](rlm-harness-lid.md) et al. — the GPU wall, sixth sighting.
