"""Prax eval — regression testing from observed agent failures.

The eval system turns production failures into reproducible test cases.
Each failure in the journal becomes a regression guard: replay the input,
score the output, confirm the fix didn't regress.

Pipeline:
  feedback → failure_journal → eval runner → pass/fail report

Usage::

    from prax.eval.runner import run_eval, run_eval_suite

    # Run a single eval case
    result = run_eval(case_id="abc123")

    # Run all unresolved failures as a regression suite
    report = run_eval_suite(user_id="user1")
"""
