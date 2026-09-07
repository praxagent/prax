# ripwire (Red Hat ET) — ranked, deterministic code maps for agents — assessment

**Source:** [github.com/redhat-et/ripwire](https://github.com/redhat-et/ripwire) —
"the ripgrep of AI context." A self-contained C++ binary (21 vendored tree-sitter
grammars, zero runtime dependencies, offline) that emits a ranked call-graph map of a
repository: what to touch, what it breaks, which tests to run. CLI (145 flags) and an
MCP server; Apache-2.0; ~1.2k stars, ~1.9k commits, active through 2026-09; maintained
by Red Hat Emerging Technologies. Evidence: `docs/EVALS.md` (12.7k lines) with pinned
corpora, per-instance ledgers, and a counterexamples section the README tells you to
read first. Assessed 2026-09-07 from the README and EVALS.md; **not run here.**

**Verdict: document + adopt as a sandbox-installed tool behind a flag.** It attacks a
failure this suite's own 2026-09-05 review just documented — Prax's `source_read` /
`source_grep` are whole-tree read primitives whose reach (and token cost) is the
problem — with a deterministic, keyless, offline map. And its evaluation write-up is
the closest external match to the house eval rules I have seen; two of its
disclosed failure shapes are ones we have already paid for.

## What it does (claims as stated)

- Parses the tree, builds a call graph, ranks symbols by several "lanes" (name-exact,
  sub-token + body BM25, mention anchors) with a **router** that picks the lane from
  the query's shape; computes cyclomatic complexity, git churn, change amplification
  (blast radius), colocation, test coverage.
- Task-shaped entry points: `--for="<task>"` (ranked symbols + one-hop context, no
  bodies by default), `--callers=SYM`, `--impact=SYM` (blast radius + which tests),
  `--test-gate` (exit 4 while untested obligations remain), `--pack-task` (a budgeted
  bundle), `--quality-panel` (six evidence families, *ranked by agreement, never
  blended*), `--from-trace=FILE` (stack frames → symbols).
- Output is minified XML with an `est_tokens` attribute, explicit `confidence`, dashed
  edges for ambiguous resolver guesses, `unindexed=` counts for unsupported files.
- **Honesty contract** (verbatim): *"Every count ripwire cannot prove ships labelled a
  floor, every truncation is disclosed where it happens, and a zero means none found —
  never none exists. Two runs over the same tree are byte-identical, and a warm run
  equals a cold one."*

## The numbers, with their own caveats attached

- **LocBench** (560 instances, repo-disjoint train/held-out; ground truth = files the
  fix patch touches): strict file@10 **60.9%**, up from 27.6% before the router. The
  authors lead with the cliff: **73.4% single-file gold vs 18.2% multi-file** — *"the
  honest shape of this problem and it shows up on every corpus below."*
- **Head-to-head, N=60** paired instances: ripwire 36.7% strict file@10 vs
  codebase-memory-mcp 26.7%, graphify 21.7%, Aider repo-map 13.3%; later rounds add
  repowise, codeseek, GitNexus (27 wins / 7 losses / 14 ties on 48). Index 0.31 s
  median vs 1.2–34 s. Token cost ≈ 5% of grep-and-read on mid-task questions. The
  README's "58.3% vs 40.0%" is a later round; EVALS.md also warns the speed multiple
  compares a warm ripwire against a cold Aider — *"quote the two medians, or quote the
  multiple with this sentence attached."*
- **Disclosed weaknesses, in their words:** a recall lane that *"ratcheted 85→83→78→69
  in five days"* from documentation edits alone (corpus frozen at a commit as the
  fix); a prompt-router widening whose coverage was **0.023 on held-out vs 0.825 on
  tuning data — "36× gap … which is the definition of overfitting"**; the substitution
  meter *"heavily biased toward this repository, the least representative corpus
  available"*; agent-outcome rounds *"underpowered as configured"*; Simpson's paradox
  from unbalanced arms. Forced routing to the wrong lane takes MRR 0.993 → 0.427 —
  *"not degradation but collapse."*

**Calibration.** Small N on the head-to-head; a single operator; the gains are
*localization* metrics, and the authors say the agent-outcome measurements are not
yet powered. What is unusual is that every one of those caveats is theirs, printed
next to the number. I did not build or run the binary.

## Why it fits Prax — three specific places

1. **`source_read` / `source_grep` are the wrong primitive, and the review proved it.**
   They are whole-tree reads: prefix containment that admitted sibling repos (the eval
   ground truth), `grep` over `workspaces/` and `app.log`, and — separately — the
   token cost of "grep around and read whole files" during self-improvement and
   sysadmin turns. A ranked map with `est_tokens` on every node is what the
   self-improve and plugin-fix agents should be reading first. This is the
   [neurosymbolic](neurosymbolic-lens.md) shape — a deterministic structure
   proposes, the model chooses — applied to code navigation.
2. **`--test-gate` is a mechanised version of our calibration finding.** The
   [Terminal-Bench sweep](futuresearch-calibrated-forecasting.md) found 85% of failures
   *claimed* success. A tool that exits non-zero while blast-radius symbols lack test
   coverage is an un-sweet-talkable "you are not done" — exactly the checker-not-
   generator role symbols hold in Prax.
3. **Their EVALS.md is the house standard, written by someone else.** Frozen corpora
   to separate ranker changes from corpus drift (our commit-stamped run dirs); a
   published overfitting number (our public/private split for #29); counterexamples
   first; "zero means none found, never none exists" (our fail-closed error
   attribution). Worth linking from `docs/guides/eval-matrix.md` as an external
   example of the discipline — not because it is famous, because it is right.

## Adopt (one, cheap, flag-gated)

**Install the binary in the sandbox image and expose it as a governed tool** —
`code_map(query, mode)` wrapping `ripwire <repo> --for=…` / `--impact=` / `--callers=`
/ `--test-gate`, run through `get_client().run_shell` like `run_python` and
`lean_check`, MEDIUM risk, available to the sysadmin spoke, the self-improve agent and
the plugin-fix agent, behind `CODE_MAP_ENABLED` (default off). Prerequisites and
shape: a release binary or a build stage in the sandbox Dockerfile (CMake 3.24 +
C++23 — build once in the image, never on the host); index the mounted checkout
(`/source` only exists on prax's compose today — see the boundary doc; for the
self-improve flow the sandbox needs a *read-only* mount of the repo, which the
2026-09 review already wants restricted anyway); output capped by `est_tokens`
before it reaches the model; the same `.env`/secret-glob exclusions the source tools
carry. Verification-ledger row until run live. Do **not** wire it as an MCP client —
Prax has no MCP client and the CLI in the sandbox is the smaller surface.

## Bank (no build)

- **Publish the overfitting number.** Their router's 0.825 → 0.023 tuning-vs-held-out
  is the number #29's accept gate should print beside every candidate; we have the
  private split, we do not yet print the gap.
- **Freeze the corpus when the score moves without the code moving.** A recall metric
  that fell 85→69 because docs were edited is the [judge-noise-floor](judge-bias-audit-2026-08-20.md)
  lesson from the other side: drift in the *instrument*, not the subject.
- **"Ranked by agreement count, never blended score"** for the quality panel is the
  [constraint-factorization](constraint-factorization-mas.md) rule (distinct evidence
  families, no averaging) in a shipped tool.

## Declined

- **As an MCP dependency or an always-on daemon.** Prax runs keyless and offline
  where it can; a binary in the sandbox is enough.
- **Its "skills" auto-activation for agents** — Prax's prompt selectivity already
  decides when a tool is offered; another activation layer would be a second
  vocabulary.
- **Treating the head-to-head as settled.** N=60, one operator, localization not
  outcomes — by the authors' own account. Adopt for the token and containment
  properties, which do not depend on the ranking winning.
