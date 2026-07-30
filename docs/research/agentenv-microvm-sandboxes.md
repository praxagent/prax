# AgentENV — Firecracker microVM sandbox orchestration (kvcache-ai)

**github.com/kvcache-ai/AgentEnv** (the KTransformers/Mooncake group; built to
serve Kimi K3's agentic-RL training). Rust; Firecracker microVMs; overlaybd
lazy image loading; incremental memory+filesystem snapshots to S3; **boot/resume
< 50 ms, pause < 100 ms**; fork for parallel rollouts; memory ballooning; an
E2B-compatible HTTP API. MIT, ~1.9k stars, active, decent docs. Explicitly
ships **no authentication** — trusted networks only.

**Verdict: document as the standing infrastructure candidate for TWO open
punch-list items — sandbox freeze/resume and per-tenant isolation — blocked
today on hardware we do not have.** Not adoptable now; exactly the right shape
later.

---

## Why this matters to Prax specifically

When the sandbox lifecycle question came up ("can we freeze/resume like a VM?
24/7 idle is wasteful on k8s"), the honest answer was: `docker pause` doesn't
release memory, CRIU is fragile against X11+Chromium, so lazy-start the desktop
stack and scale to zero. AgentENV is what the *real* freeze/resume answer looks
like when someone builds it properly:

| Punch-list item | What AgentENV provides |
|---|---|
| Freeze/resume | VM snapshot + sub-50ms resume; idle costs ~nothing; no CRIU fragility because the whole guest kernel is snapshotted |
| Per-tenant isolation | microVM per user is a *stronger* boundary than our shared Docker container with one workspace bind-mount — the gap flagged since the benchmark-pollution incident |
| Parallel eval runs | fork() of a warm environment — the per-task isolation the Terminal-Bench runner wants, without per-task image builds |

The E2B-compatible API matters architecturally: `prax_sandbox_client` could
grow an AgentENV backend without the harness knowing, the same seam that
already switches local/remote daemon.

## The blocker, measured not assumed

Firecracker needs `/dev/kvm` (kernel ≥ 6.8 is satisfied — both boxes run 6.17).
**Neither box has it**: standard AWS EC2/Lightsail instances expose no nested
virtualisation. Same wall that ruled out the Android emulator. Running this
requires bare-metal instances (`*.metal`) or another provider with nested virt.

So the adoption condition is written down rather than pretended away: **when a
multi-tenant or k8s deployment is real enough to buy metal, evaluate AgentENV
first** — before building scale-to-zero by hand. Its no-auth posture slots into
our existing pattern (the sandbox is already loopback-only-unauthenticated;
the daemon adds the auth layer).

## Provenance note

Built for RL-training rollouts (massive parallel env farms) — a heavier duty
cycle than Prax's. That's a reason it's credible (it runs at scale for Kimi),
not a reason it fits; the fit claim rests on the E2B seam and the snapshot
semantics, which are duty-cycle-agnostic. Not exercised by us; README-level
knowledge only.

## Related

- The sandbox-lifecycle discussion (lazy-start + scale-to-zero as the
  no-new-hardware answer; this note is the with-hardware answer).
- [capy-swe-agent-platform.md](capy-swe-agent-platform.md) — Scrapybara VMs are
  the closed-source commercial version of this layer.
