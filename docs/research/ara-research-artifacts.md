# ARA — Agent-Native Research Artifacts

[← Research](README.md)

[ARA (Orchestra Research)](https://www.orchestra-research.com/ara) proposes a
structured replacement for the academic PDF: research packaged as a
multi-layered artifact that captures both the chosen path and the abandoned
ones. Their pitch is aimed at reproducibility ("less than half of what an
agent needs to reproduce a paper is in the PDF — 45.4%") and at the
"storytelling tax" — the way papers reduce exploratory work into a linear
narrative and discard the dead ends.

The four layers:

1. **Cognitive (`logic/`)** — claims with epistemic status, formal
   definitions, experiment plans.
2. **Physical (`src/`)** — algorithm code, annotated configs, exact
   environment specs.
3. **Exploration graph (`trace/`)** — the full research DAG including
   pivots and dead ends.
4. **Evidence (`evidence/`)** — machine-readable results, logs, metrics.

A "Live Research Manager" silently captures the trajectory during
human–AI collaboration; reported lift is +21.3pp on understanding and
+7.0pp on reproduction accuracy versus PDF baselines.

## Relevance to Prax

ARA solves a problem Prax doesn't have — Prax isn't a research-publishing
platform — so this is **not a roadmap item**. Two ideas are worth keeping
in mind if the shape of the work ever shifts that way:

- **Structured layout for research-mode spaces.** Library spaces are
  freeform today. An opt-in template along ARA's lines (`logic/` /
  `src/` / `trace/` / `evidence/`) would slot next to the existing
  per-space progress files. The closest existing primitives are
  `progress_append` and `trace_search` — they capture *what happened*,
  not *what was tried and abandoned*.
- **Dead ends in trace summaries.** `trace_search` indexes traces by
  trigger + top-span summaries. ARA's claim that abandoned branches
  carry as much signal as the chosen one is plausible — summaries that
  surface "tried X, pivoted because Y" rather than only the final
  outcome could improve recall on "have I tried this before?" queries.

Both are speculative and neither addresses an observed Prax failure
mode. Filed here as references, not as proposals.
