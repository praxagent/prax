# Feature-flag audit — 2026-08-07

Prax carried **61 boolean settings flags** (48 default-off) alongside ~146
non-boolean settings when this audit started. That is enough that "is this
behaviour on?" had itself become hard to answer, which defeats the purpose of
having the switch. This is the audit of what should exist, what should merge,
and what should go. **This pass took it to 54**; the clusters below would take
it further.

**The rule this audit proposes**, to keep the count from creeping back:

> A flag earns its place only while someone could rationally choose either
> value. Once a value is *decided* — by evidence, by a shipped fix, or because
> the alternative is simply a bug — the flag is dead weight and the losing
> branch should be deleted along with it.

A flag is not a way to avoid committing to a decision. Two things follow: a
flag whose off-state is "the broken behaviour" was never a real choice, and a
flag whose experiment has concluded should be resolved, not left switched off
forever.

## Done in this pass

| change | from → to | why |
|---|---|---|
| `DELEGATION_PINNED_INPUTS_ENABLED` | flag → **always on** | delegation was dropping information the orchestrator already held; the off-state was the bug |
| `ARTIFACT_DELIVERY_HINT_ENABLED` | flag → **always on** | the hint is computed from the filesystem and says nothing when no artifact exists — there is no behaviour to gate, only a fact to report |
| `MEMORY_CONSISTENCY_ENABLED` + `MEMORY_CONSISTENCY_AUTO_SUPERSEDE` | 2 bools → **`MEMORY_CONSISTENCY_MODE`** (`off`/`log`/`enforce`) | the two booleans encoded three states, and one combination (`AUTO_SUPERSEDE=true` with `ENABLED=false`) was meaningless. A validated tri-state names the ladder instead |
| `ACTIVE_INFERENCE_SEMANTIC_GATE` settings field | **deleted** | dead config: `agent/semantic_entropy.py` reads the env var straight from `os.environ`, so the field was a second declaration nothing consulted. The env var still works |

### Decisions that were already made — resolved

| flag(s) | campaign verdict (2026-07-08) | what shipped |
|---|---|---|
| `INTENT_CLARIFICATION_ENABLED` | **NOT flipped** — 5/6 pass, **+11% cost**, no gain | **flag and code path deleted** (`_maybe_clarify` and its call site). Recoverable from git if ever wanted |
| `UNKNOWN_TOOL_HIGH_RISK` | **NOT flipped** — **4/6, a real regression**: deny-by-default blocked a needed tool and the agent bailed | **deleted.** Unknown tools are MEDIUM, and that is now a decision rather than a default awaiting a switch. Rebuilding it needs a design that *degrades* instead of stranding |
| `HIGH_RISK_SCOPED_CONFIRM` | tested *jointly* with the above | **KEPT** — see the confound note below |
| `AGENT_MIDDLEWARE_ENABLED`, `PROMPT_SELECTIVITY_ENABLED` | **FLIPPED** — recommended, set in `.env-example` | **code defaults flipped to `True`**, not deleted — see below |

**The confound worth recording.** The campaign A/B'd `UNKNOWN_TOOL_HIGH_RISK`
and `HIGH_RISK_SCOPED_CONFIRM` as one arm ("deny-by-default tool boundaries"),
so the 4/6 regression cannot be attributed to either alone. They are different
mechanisms: one *blocks* unrecognised tools (and is what stranded the agent);
the other only *narrows* what a confirmation unlocks, which is a security
tightening with no plausible path to that failure. Deleting both on joint
evidence would have removed a security feature the data never implicated — so
scoped-confirm stays, untested-independently, and that is now written down
rather than assumed.

**Why the two flipped flags kept their flags.** Making them unconditional would
have removed a capability in each case: middleware-off is a legitimate debugging
move, and `prax/eval/self_regen.py` deliberately switches selectivity **off**
while scoring so the scorer always sees the full prompt. The actual defect was
narrower than "these are flags" — it was that the *code default* disagreed with
the *measured recommendation*, so a deployment that set nothing behaved
differently from the documented advice. Aligning the defaults fixes that without
deleting anything real.

Net: **61 → 54** boolean flags (39 off, 15 on), with no capability lost.

## Recommended next — genuine clusters

| cluster | flags | proposal |
|---|---|---|
| Model tiers | `LOW_ENABLED`, `MEDIUM_ENABLED`, `HIGH_ENABLED`, `PRO_ENABLED` | one `ENABLED_TIERS=low,medium,high` list. Four booleans expressing membership in a set is the classic shape that should have been a set (`agent/model_tiers.py:56-71`) |
| Retrieval | `RETRIEVAL_RERANK`, `RETRIEVAL_QUERY_EXPANSION`, `KNOWLEDGE_HYBRID_ENABLED` | both retrieval flags are *deferred pending a purpose-built retrieval eval* — same gate, same evidence, flipped together or not at all. One `RETRIEVAL_MODE=basic\|hybrid\|enhanced` ladder |
| Browser | `BROWSER_SANDBOX_ONLY`, `BROWSER_VNC_ENABLED`, `BROWSER_HEADLESS` | genuinely independent (isolation / remote view / display) — **keep as three**. Listed here to record that it was checked, not overlooked |
| TeamWork | `TEAMWORK_ENABLED` + `TEAMWORK_URL` | **two gates for one thing.** `teamwork_service.enabled` is `bool(base_url)`, while `app.py:157` also requires the boolean. `CLAUDE.md` already documents "TEAMWORK_URL empty → silently skips" as a trap — that trap *is* this redundancy. Make the URL the single source of truth |

## Keep — and why

Not everything default-off is clutter. These earn their gate:

- **Cost or blast radius**: `SELF_REGEN_ENABLED`, `FINETUNE_ENABLED`,
  `TASK_RUNNER_ENABLED`, `MCP_SERVER_ENABLED`, `SANDBOX_ENABLED`,
  `SPACE_REPOS_ENABLED` — each turns on real spend, a background loop, or an
  external surface. Off-by-default is a safety property, not indecision.
- **Deployment shape, not preference**: `RUNNING_IN_DOCKER`,
  `OBSERVABILITY_ENABLED`, `PUBLIC_URL_AUTODETECT`, `BROWSER_HEADLESS`,
  `OPENAI_BASE_URL_IS_OPENAI`. Different sites genuinely differ.
- **Awaiting evidence**: `MEMORY_CONSISTENCY_MODE`,
  `CHECKPOINT_RESUME_ENABLED`, `OPENAI_RETAIN_REASONING`,
  `CLAIM_AUDIT_ATTENDED_QUARANTINE`, `VERIFY_DISCIPLINE_ENABLED`,
  `LLM_FALLBACK_ENABLED`. These are real open questions with a defined path to
  resolution — the honest use of a flag.

The last group carries an obligation: **an "awaiting evidence" flag that never
gets its evidence becomes clutter by default.** Each should either be A/B'd or
be given a documented reason it can't be, and `CLAIM_AUDIT_ATTENDED_QUARANTINE`
is the overdue one — its A/B was killed incomplete by a dead search backend, and
nothing has rerun it since search was fixed.

## How to check this later

```bash
# every boolean flag, its default, and how many places actually read it
grep -E "^\s+[a-z_]+: bool = Field" -A2 prax/settings.py
```

A flag with **one** usage site outside `settings.py` is not automatically wrong
— that is the normal shape for a clean gate — but a flag with **zero** is dead,
and that is how the semantic-entropy field above was found.
