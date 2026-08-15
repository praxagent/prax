# Auto-research with an agent — beam search, and the precondition #29 lacks

**Source:** [Auto-research with Codex: GPU Mode QR Decomposition](https://sankalp.bearblog.dev/autoresearch/)
— Sankalp (@dejavucoder), 8 July 2026. A first-person log of driving a coding
agent through **~1,500 submissions over 14 days** to optimise a batched
Householder QR kernel: **232× speedup** (419,000 µs → 1,805 µs geometric mean),
**12th of 183** entrants.

**Verdict: document + adopt one mechanism for [#29](../IDEAS_BACKLOG.md) — and
bank the precondition, which is the more important half and is uncomfortable.**

Better evidence than most blog posts: the numbers come from a public
leaderboard rather than the author's own scoring, and the limitations section
is unusually candid (missed distribution-specific optimisations, over-used
library fallbacks, never exploited `tcgen05` on the B200). Still n=1, one
domain, and the author says plainly he was not a GPU-kernel professional.

## Adopt: beam search over candidate *families*, not single-incumbent hill-climbing

The author's own stated regret is that **beam search was introduced late rather
than from the start** — keeping 3–5 candidate families alive instead of
hill-climbing a single incumbent.

That is exactly #29's current shape: propose a patch, accept or reject, repeat
from the survivor. Single-incumbent search on a rugged landscape gets stuck in
whatever local optimum the first accepted change created, and every subsequent
proposal is conditioned on it.

**Third sighting of the same idea**, which is what raises it above one person's
hindsight:

- [Harness Generalization](harness-generalization.md) — online *prune-and-
  reallocate* beats fixed allocation at equal budget.
- [Anthropic's multiagent work](multiagent-failure-modes.md) — coordinated
  swarms and independent agents found **266 and 21 vulnerabilities with only 12
  overlapping**; diversity changed *what* was found, not just how much.
- This — a practitioner losing time by not diversifying early.

For #29 the change is small and cheap: keep N proposals alive, evaluate each on
held-out cases, prune the worst, reallocate budget to the survivors — rather
than accepting one and moving on. It composes directly with the
[HDA](harness-delta-attribution.md) accept-gate, which already demands held-out
and compute-matched evaluation; beam search just means running that gate over a
population instead of a single candidate.

## Bank the precondition: this worked because the fitness function was cheap, objective and high-resolution

The uncomfortable part. This loop ran 1,500 iterations in 14 days because
kernel latency is:

- **objective** — a number, not a judgement;
- **cheap** — microseconds to measure, so a bad idea costs almost nothing;
- **high-resolution** — it distinguishes a 3% improvement, so hill-climbing has
  a gradient to climb.

**Prax's fitness function has none of those properties.** The capability suite
is n=30 with a 95% CI of roughly ±16pp, each run costs minutes and real money,
and two configurations within ~5 cases are indistinguishable
([model ladder](harness-beats-model-choice.md) measured exactly that). A search
loop cannot hill-climb a signal it cannot resolve.

That is not a reason to abandon #29. It is the reason #29 is hard, stated
precisely, and it implies an ordering: **improving the fitness signal is
upstream of improving the search.** Growing the suite 7→30 was that work;
[HDA](harness-delta-attribution.md)'s compute-matched control is more of it.
Beam search over a signal that cannot separate candidates just burns budget
more elegantly.

Worth noting the mirror image: the author's domain had a *perfect* fitness
function and he still finished 12th, losing to teams who wrote custom kernels
where he used library fallbacks. Good search does not substitute for domain
knowledge either.

## Also worth noting: steering a long run without derailing it

He used `/goal` directives to redirect a long-running optimisation loop, and
`/btw` `/side` for mid-loop questions that should *not* redirect it.

That is a human-supplied version of what Prax's `SteadyingCounsel` middleware
does automatically — inject guidance mid-run without ending the run. The
distinction he draws is the interesting part: **a mid-loop query and a mid-loop
goal change are different speech acts**, and conflating them makes an agent
either ignore steering or thrash on every remark. Prax has no equivalent
distinction; a message during a long turn is just a message.

Filed as an observation rather than an adopt — Prax turns are minutes, not
days, so the need is weaker. Revisit if long autonomous runs become normal.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Beam search over 3–5 candidate families in #29**, replacing single-incumbent accept/reject; composes with the HDA held-out + compute-matched gate | 📋 queued — **3rd sighting** (harness-generalization, multiagent diversity, this) |
| **Fitness-signal quality is upstream of search quality** — #29 cannot hill-climb a signal with a ±16pp interval | ✅ recorded as the standing constraint on #29 |
| `/goal` vs `/btw` — distinguishing a mid-run goal change from a mid-run query | ⏸ parked — Prax turns are minutes, not days |
