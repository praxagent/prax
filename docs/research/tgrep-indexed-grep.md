# tgrep (Microsoft) — trigram-indexed grep — assessment

**Source:** [github.com/microsoft/tgrep](https://github.com/microsoft/tgrep) — a
trigram-indexed, ripgrep-compatible grep with a client/server shape: `tgrep index .`
builds an inverted trigram index, `tgrep serve .` keeps it warm with a file watcher
and background re-indexing, and `tgrep "pattern" .` narrows to candidate files by
trigram intersection before running the real regex. Rust, MIT, prebuilt binaries
and Homebrew, ~120 ripgrep-compatible flags including `--json`. Powers grep in
GitHub Copilot CLI. Assessed 2026-09-07 from the README and `BENCHMARKS.md`; **not
run here.** Asked as: "is this tool worth giving to Prax or nah?"

**Verdict: nah — document, don't adopt.** Not because it is bad; the benchmark page
is honest and the design is sound. Because the win exists only above a corpus size
Prax never searches, and it is the wrong *kind* of primitive for the problem Prax
actually has with code search.

## The numbers, as they state them

- "Repo size is the strongest predictor." Speedups over ripgrep (index prebuilt,
  fresh client process per query, August 2026 sweep): Chromium (504k files)
  3.8–17.6×, gecko-dev (388k) 7.4–51.9×, linux (96k) 9.4–34.8×, rust (62k) 1.6–7.7×,
  **kubernetes (31k) 0.93×–7.1×, go (16k) 1.3×–7.5×.** Geometric means 14.6×
  Windows, 8.6× macOS, **2.8× Linux.**
- The Linux column is the honest one for this suite (both boxes are Linux with a
  warm page cache): 1.29× on 16k files, a **near-tie at 31k files** ("101.8 ms
  versus 94.4 ms per query … where match volume costs tgrep more in delivery than
  the index saves in file selection"). Their own guidance: "Linux's page cache plus
  ripgrep's parallel scan make brute force genuinely cheap on a warm repo, so the
  index buys less."
- Costs: an on-disk index of 113 MB (go) to 2.6 GB (chromium); a build of seconds
  to minutes; a persistent server process with a watcher; ~110–460 MiB private
  memory while indexing.
- Caveat they print: macOS ripgrep timings varied 1.4× across identical runs from
  runner variance alone — "treat a single macOS column as an order of magnitude."

## Why it does not fit Prax

1. **Nothing Prax searches is large.** `prax` is 931 tracked files, TeamWork 234,
   the sandbox 45. The measured crossover where tgrep stops being a near-tie on
   Linux is ~30–60k files; Prax's own checkout is two orders of magnitude below it,
   and a grep of it is already sub-second. A user's workspace in the sandbox is the
   same shape. The tool solves a monorepo problem; Prax does not have one.
2. **It is a faster way to do the thing the review said to stop doing.** The
   2026-09-05 review's finding on `source_read`/`source_grep` was not that grep is
   slow — it was that *grep-and-read the whole tree* is the wrong primitive: it
   reached sibling repos and eval ground truth, and it spends tokens on bodies
   before knowing which symbols matter. A trigram index makes the same primitive
   cheaper; it does not change what the model reads. The primitive Prax lacks is a
   **ranked map with token estimates** — that is [ripwire](ripwire-code-maps.md),
   assessed the same day.
3. **It adds state Prax's execution story avoids.** A server per repo, an index
   directory, a watcher, a flush cycle. The sandbox is deliberately a keyless
   container with one bind-mount; the source tools are one `grep` call. Every one
   of tgrep's advantages is bought with a daemon.

## What is worth taking (no build)

- **The benchmark page's shape** — the one losing cell printed with its mechanism,
  runner variance quantified before the headline, the crossover stated as guidance.
  Same standard [ripwire](ripwire-code-maps.md) meets; same standard we hold the
  matrix to.
- **If Prax is ever pointed at a monorepo** (a user workspace with 100k+ files),
  tgrep is a drop-in: ripgrep-compatible flags and `--json` mean `source_grep`'s
  one `grep -rnI` call becomes one `tgrep` call. Nothing to prepare now.
- **Cheaper, and actually useful today:** the sandbox image has `grep` but no
  `ripgrep`; adding `rg` to the image is a one-line Dockerfile change that gives
  `sandbox_shell` users the fast scan without an index or a daemon. Optional; the
  repos are small enough that even this is a convenience, not a fix.

## Declined

- Installing tgrep in the sandbox or wiring it under `source_grep` — no workload
  here reaches the size where it wins, and it is the grep-and-read primitive the
  suite is moving away from.
- Running `tgrep serve` on the harness host — a second long-lived process with a
  writable index for a search that finishes in milliseconds without it.
