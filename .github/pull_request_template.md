## What & why

<!-- One or two sentences: what this changes and why it's needed. -->

## How it was tested

<!-- Commands run and what you observed — `make ci` output, manual verification, a driven end-to-end check, etc. -->

## Pre-merge checklist

- [ ] `make ci` is green — the required **`test`** check enforces this on the PR
- [ ] Ran **`/code-review`** on the diff (use **`/code-review ultra`** for anything non-trivial) and addressed the findings
- [ ] Any behavior change is flag-gated and defaults to prior behavior, so keyless CI stays green
- [ ] No secrets, databases, `.env`, or runtime data staged — `git diff --cached --name-only | grep -iE '\.db($|[.\-])|\.bak|(^|/)\.env$|secret|token|\.pem$|\.key$'` returns nothing
- [ ] Docs updated if behavior, flags, or setup changed
