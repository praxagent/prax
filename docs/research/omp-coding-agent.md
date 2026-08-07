# omp — a coding agent with the IDE wired in

**Source:** <https://omp.sh> (JS-rendered marketing site; copy extracted from the
bundle). Author **Can Bölük** (`can1357`) — security researcher / reverse
engineer, 3k GitHub followers. **Closed source** (`can1357/omp` is not a public
repo). Install: `curl -fsSL https://omp.sh/install | sh`, brew, bun.
**Assessed:** 2026-08-07. TJ's question: *"Are they better than us and if so what
can we do about it?"*

**Verdict: on coding-agent craft they are clearly, substantially better than
Prax — and they are not competing with Prax's product. Both are true; the first
matters more. Adopt TWO mechanisms, both of which land on weaknesses found the
same night.**

---

## The honest comparison

**Different products.** omp is a single-developer terminal coding agent, wired
into the IDE and the local machine. Prax is a governed multi-channel assistant
(Discord/SMS/TeamWork, spokes, risk tiers, approval gates, keyless secrets
proxy, per-user workspaces, scheduler). omp has no multi-channel surface, no
governance model, no multi-user story. Prax has no LSP, no debugger, no editor
embedding.

**But "different products" is the comfortable answer, and it is not the whole
one.** omp is ahead of us at the *harness craft* layer — the part that is
domain-independent — and pretending otherwise would be exactly the
self-congratulation this project's rules forbid.

## What they built (their numbers, their claims)

~103k lines of native Rust components, from their own table:

| component | loc | what |
|---|---:|---|
| `shell` | 31,130 | embedded bash, persistent sessions, process-tree control |
| `coreutils` | 28,910 | ls/find/grep/sort/tail/wc/fd **in-process — no fork, no PATH** |
| `minimizer` | 23,880 | trims command output *before the model reads it* |
| `walker` | 4,910 | one traversal fast path, mtime-keyed cache |
| `iso` | 3,420 | workspace isolation: apfs/btrfs/zfs reflink, overlayfs, projfs |
| `snapcompact` | 1,440 | compaction frames — **text in, pixel-font PNG out**, deterministic, no model call |

Capabilities worth naming:

- **LSP wired into every write.** A rename goes through
  `workspace/willRenameFiles`, so re-exports, barrel files and aliased imports
  update *before* the file moves.
- **Drives a real debugger.** lldb on a segfault, dlv on a hung Go service,
  debugpy on a wedged Python process. Their jab lands: *"Most agents are still
  sprinkling print statements."*
- **Dual persistent kernels with tool callback.** Python and Bun, either of
  which can call back into the agent's own tools (`read`, `search`, `task`)
  over a loopback bridge.
- **Hashline** — edits anchored by content hash rather than line numbers; a
  stale anchor rejects the patch instead of corrupting the file. Claimed 61%
  fewer output tokens on Grok 4 Fast.
- **GitHub as filesystem** — PRs are paths for `read`; no `gh_pr_view` tool
  zoo.
- **mnemopi** — local SQLite memory with vector embeddings + graph tools;
  `retain` / `recall` / `reflect` / `memory_edit`.
- **`/collab`** — live session on a relay, join by link or QR, read-write or
  read-only.
- **Discovery** — reads eight existing config formats natively (Cursor MDC,
  Cline `.clinerules`, `AGENTS.md`, Copilot `applyTo`) with no migration.

*Caveat: every number and claim here is theirs, from marketing copy. Nothing is
independently verified, there is no public repo to read, and no benchmark is
cited beyond the Grok token figure.*

## Adopt 1 — the ADVISOR pattern (a second model watching every turn)

> "Pair a reviewer model to the 'advisor' role and it reads every turn the main
> agent takes, injecting notes inline — a quiet aside, a concern, or a hard
> blocker. It runs on its own context and its own model, so it catches what the
> doer rushed past."

This is the structural version of what `claim_audit` gestures at and does not
achieve. Found the same night
([eval-scorer-audit](eval-scorer-audit-2026-08-07.md), and the trace work
before it): our auditor is **post-hoc and advisory** — it runs *after* the reply
is composed, posts to an internal channel, and by default changes nothing the
user sees. Three of its checks were not even wired into the metric.

The differences that matter:

| | `claim_audit` (ours) | advisor (theirs) |
|---|---|---|
| when | after the response exists | during, every turn |
| context | the finished text + tool results | its own, independent |
| model | none (regex) / same model | a **different** model |
| effect | a log line unless a flag is on | inline note the agent must handle |

"Its own context and its own model" is the load-bearing part, and it is the
[maker≠checker](lanyon-formal-verification.md) principle we already hold —
applied *live* rather than at grading time.

## Adopt 2 — TIME-TRAVELING STREAM RULES (a pre-emptive guard)

> "Your rules sit dormant until the model goes off-script. A regex match aborts
> the stream mid-token, injects the rule as a system reminder, and retries from
> the same point … Injections survive compaction, so the fix sticks."

**Every guard Prax has is post-hoc.** `claim_audit` grades a finished answer;
the trifecta guard fires on a completed tool chain; `task_done` verifies after
the claim. This aborts *at generation time* and resumes from the same position,
paying context cost only when the rule actually trips.

**This is a candidate answer to the stochastic-injection finding.** The same
night we measured `injection_ignore_instructions` producing `BREACHED` on one
run and a correct refusal on the next — same model, same prompt. Grading that
pass^k (shipped) measures the flakiness honestly; it does not reduce it. A
stream rule on `BREACHED` intervenes *while the token is being produced*, which
is the right layer — and "the bound was at the wrong layer" was the recurring
shape of every defect found that night.

Honest limits: aborting and resuming a stream is provider-dependent, costs a
partial generation on every trip, and a badly-written regex becomes a
self-inflicted loop. It needs a trip budget and a flag.

## Declined / not applicable

- **The native-Rust rewrite** (in-process ripgrep/coreutils, PTY, clipboard,
  BPE tables). Real engineering, and the wrong investment for a Python
  multi-channel assistant whose bottleneck is model latency, not fork-exec.
- **LSP / DAP / editor embedding.** Genuinely excellent and squarely
  coding-vertical; Prax delegates coding to the sandbox by design.
- **Pixel-font PNG compaction.** Fascinating, unexplained, and unmeasured
  publicly. Noted, not adopted.
- **Their closed-source, single-machine posture** — no governance layer, no
  multi-tenant story. Fine for their product; unavailable to ours.

## What we do about it

Nothing in Prax's roadmap should become "build a better coding agent" — that is
their game, they are winning it, and the sandbox already delegates coding.
What should change is that **two of our safety mechanisms run at the wrong
time**, and omp demonstrates both fixes working in a shipped product. Those are
the adopts; the rest is admiration.
