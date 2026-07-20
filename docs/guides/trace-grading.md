# Grading the trace, not just the answer

Most benchmarks score only the **output**: right letter, right number. But two
runs can reach the *same* answer by very different **processes**, and only one of
them is the process you want to ship. This is the eval-side of the ProofJudge
lesson (`docs/research/proofjudge-taste.md`) — *"the tests pass, but nobody wants
the PR"*: correctness is not the whole of value.

`prax/eval/trace_grade.py` grades the **process** against three general quality
axes — the ones that matter on *any* task — from the run's captured metadata
(answer, tools used, tokens). It's fully deterministic and keyless: no judge
model, so a trace grade is as reproducible and un-gameable as the deterministic
answer scorers.

## The three axes

| Axis | Question | The failure it catches |
|---|---|---|
| **committed** | Did the run emit a real, non-empty answer at all? | Reasoning for 13k tokens and emitting nothing (the GPQA non-commitment / timeout failure). |
| **verification** | For a claim it *could* check with a tool, did it? | Hand-asserting a load-bearing, tool-checkable fact instead of computing it ("estimate, don't compute"). |
| **efficiency** | Did it stay within a token / tool-call budget? | Verifying the same thing six times — burning the budget the tool-economy rule exists to protect. |

A case attaches a `trace_rubric` (all optional):

```python
{"expected_tools": ["run_python"],   # tools that verify a load-bearing claim here
 "token_budget": 15000,              # the "efficient" token ceiling
 "ideal_max_tool_calls": 3}          # redundancy penalty past this
```

Omit `expected_tools` when nothing is tool-checkable — the verification axis then
doesn't apply and the weights renormalise over the other two. `committed` carries
the most weight (a run that never answers is a failed process regardless of how
elegant the reasoning was).

```python
from prax.eval.trace_grade import grade_trace, grade_run

grade_trace({"answer": "...", "tools": ["run_python"], "tokens": 9000}, rubric)
# -> {"trace_score": 1.0, "criteria": {committed, verification, efficiency}, "summary": ...}

# Or both axes at once — answer grade + trace grade, kept separate on purpose:
grade_run(run, answer_grade={"passed": True, "score": 1.0}, trace_rubric=rubric)
```

## Worked example — the same question, two processes

praxbench Q1 asks Prax to interpret a polynomial map (a stated Jacobian
determinant and a stated collision) — the well-reasoned answer is that it's a
counterexample to the Jacobian Conjecture. Two real runs of the *identical*
prompt reached that answer very differently:

| Run | trace_score | committed | verification | efficiency |
|---|---|---|---|---|
| by-hand (0 tools) | **0.65** | 1.0 | **0.0** (took the determinant on faith) | 1.0 |
| computed (`run_python`×6) | **0.83** | 1.0 | 1.0 | **0.31** (124k tokens) |
| **ideal (target)** | **1.0** | 1.0 | 1.0 (verify once) | 1.0 |

Both got the answer right. Scoring only the answer, they'd tie. The trace grade
shows they're *not* equivalent — and, more usefully, **names the axis each one is
missing**: the by-hand run should have computed the determinant; the computed run
should have done it in one call, not six. That gap — *verify, efficiently, and
commit* — is a general capability target, not a benchmark quirk (see the
verify-and-commit design note).

## Why deterministic (no judge)

The same reason the answer scorers are deterministic: a process grade you can
compute from captured metadata can't be gamed by a persuasive-sounding trace, and
it runs offline in CI. Qualitative judgement (was the *reasoning* sound?) is a
separate, optional judge layer on top — but the floor is these three mechanical
axes, and they already catch the failures we actually see.
