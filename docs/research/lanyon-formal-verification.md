# Lanyon.ai — "correctness by construction," and the *misformalization* trap

**Source.** [lanyon.ai/blog/welcome](https://lanyon.ai/blog/welcome/) (launch post,
July 2026). Research assessment per the house pattern.

**Verdict: document-don't-adopt the *system*; bank one sharp concept
(*misformalization*) that reinforces — and re-frames — what Prax already does, and
that lands directly on the in-flight world-model reasoning work.**

## What Lanyon is

A neurosymbolic company building **formally-verified AI for scientific/technical
computing** (aerospace, space propulsion, nuclear — domains where a wrong number
kills). Their thesis: *"code generation is abundant; correctness is scarce."*
Their mechanism:

- Agents reason in a **proprietary DSL that is "mathematically, physically, and
  scientifically aware"** — not natural language.
- A **purely symbolic algorithm expands a formal specification into code *and*
  proofs simultaneously** → "correctness by construction." If a spec can't be
  proven, **no code is emitted**.
- Claims: *"mathematically impossible … to make a mistake,"* "thousands of lines of
  type-correct proof per second," at a fraction of frontier-model cost.

Architecturally this is the **symbolic** end of the spectrum: a narrow-domain DSL +
symbolic proof search, LLM only at the edges. That's the opposite of Prax (a
general-purpose *neural*-agent harness). So the system isn't adoptable — it's
proprietary, domain-narrow, and paradigm-different. But one idea is gold.

## The load-bearing idea: *misformalization*

Their sharpest point: when an LLM writes **both** the implementation **and** its
proof/test, the two can be **jointly wrong** — the proof "verifies" the wrong
thing, so the verification is worthless. LLM self-verification is *not*
verification.

This crisply names the failure mode behind two things Prax **already** does — and
it's a strong argument to keep leaning into them:

- **maker ≠ checker.** Prax's supervising auditor
  ([diffuse-ai-control](diffuse-ai-control-judge-robustness.md), `EVAL_AUDITOR_ENABLED`)
  re-checks the cheap judge's passes with a *different* model. Misformalization is
  exactly why: a checker that shares the maker's blind spot rubber-stamps a
  consistent mistake.
- **Deterministic / symbolic verification over LLM self-grading.** "Verifiable
  beats judgeable" ([edge-bench](edge-bench-learning-curves.md)), the Lean kernel as
  the un-gameable verifier ([cdc-lean](cdc-lean-teach-prax-lean.md), `lean_check`
  shipped), and executable-verification-against-ground-truth
  ([executable-world-models](executable-world-models.md)). Lanyon is the same
  instinct pushed to its symbolic extreme.

## Why it's *timely* — it lands on the world-model work in flight

We are, right now, finding that **world-model reasoning (write code → run it) can
compute the *wrong thing*** — a "REAL-MISS" where the program runs cleanly but
models the problem incorrectly. **That is misformalization**: the code is a formal
artifact that doesn't match intent. Lanyon's framing hardens two design rules for
our world-model loop:

1. **The verify step must be *independent and sound* — not the model's own
   say-so.** Verify the induced program against **ground truth / held-out examples
   / an independent check**, never "the model says it's right." (For ARC: the
   program must reproduce *every* demo pair. For math: cross-check by a second
   method, not self-report.)
2. **A program that runs ≠ a program that's correct.** Passing execution is
   necessary, not sufficient. Weight selection on *evidence of correctness*
   (reproduces the examples, survives counterexamples), not on "it didn't crash."

## Correctness-by-construction, where the domain permits it

Where a domain *has* a cheap sound checker — type systems, the Lean kernel,
exact-match against examples — prefer **generating into a verified form** over
generate-then-hope. Prax has this partially (`lean_check`; the world-model's
verify-against-examples). Lanyon is the maximal version for a domain (scientific
computing) that happens to admit a formal spec. Most of Prax's tasks don't, which
is exactly the boundary below.

## The honest caveat (their claim is oversold)

*"Mathematically impossible to make a mistake"* is marketing. Lanyon **itself
admits** the hard part — **semantic correctness**, i.e. whether the formal spec
matches the user's actual intent — is an **open problem**. That's where real
mistakes live: a perfect proof of the *wrong* spec is still wrong. The honest
version: *correctness **relative to a formal spec**; getting the spec right is still
on you.* This is the same gap in **all** verification (a green test on the wrong
assertion), and it's why Prax's verification is a *floor*, not a guarantee.

## Takeaways

1. **Name the failure mode: misformalization.** Same LLM writing solution + its own
   check ⇒ jointly-wrong ⇒ verification is theater. Keep **maker ≠ checker** and
   **deterministic/independent verification**.
2. **For the world-model loop: verify against ground truth, not self-report; "runs"
   ≠ "correct."** Directly actionable on the work in flight.
3. **Correctness-by-construction where a sound cheap checker exists** (types, Lean,
   exact-match) — otherwise it doesn't apply.
4. **Verification is a floor, not a guarantee** — the spec↔intent (semantic) gap is
   unsolved for everyone, Lanyon included. Don't oversell Prax's checks either.
