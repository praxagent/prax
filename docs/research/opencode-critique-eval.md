# "Stop using OpenCode" — does the critique apply to Prax?

*Assessment of [wren.wtf/shower-thoughts/stop-using-opencode](https://wren.wtf/shower-thoughts/stop-using-opencode/)
(2026-07-20). Prax's sandbox drives OpenCode as its coding agent, so a critique of
OpenCode is worth taking seriously — but most of it targets things Prax doesn't
use or already does differently. The real, actionable finding is **prompt
caching**, where the author's instinct (and TJ's) is correct.*

**Verdict: document. Most of the critique does not apply to Prax's architecture;
several points Prax already handles *better*. Adopt the caching fixes — they are
genuine, and one (Anthropic) is a clear gap.**

## Point by point

| OpenCode criticism | Applies to Prax? | Why |
|---|---|---|
| **TUI**: 1 GB for text, broken shift-enter, ^C kills session, markdown O(n²) | **No** | Prax drives OpenCode **headless** (`opencode serve`, HTTP `:4096`) — none of the TUI is in the loop. |
| **Bash-filter security** (tree-sitter AST, base64 / `env git` / redirection evasion, "always" persistence) | **No (by design)** | Prax does **not** rely on OpenCode's bash filtering. The sandbox is an *isolated, unauthenticated, loopback-only container whose whole job is to run arbitrary code*; the **container is the boundary**, and Prax's own tools go through `governed_tool.py` (risk tiers + trifecta guard), not OpenCode's permission prompts. The author's "Docker-as-security is insufficient" is a philosophy clash: Prax's threat model treats sandbox code-exec as untrusted-by-assumption, not as something to filter. |
| **CVE-2026-22812** (OpenCode HTTP server RCE via permissive-CORS localhost) | **Mitigated** | `:4096` is bound **`127.0.0.1`-only** locally, and in remote mode is **not published at all** (only the daemon's TLS+bearer `:8843` is exposed — `docker-compose.remote.yml`). The attack needs a browser *on the sandbox host*; the only one there is the sandbox's own Chromium **in the same container**, where RCE is not an escalation (that container already runs arbitrary code). |
| **Remote-first default / first keystroke uploads** | **No** | Prax's sandbox is local-first (in-process client or a loopback container); nothing auto-uploads. |
| **`todo` tool exists but agents forget to check it** | **Prax is better** | Prax's `agent_plan` is **auto-injected into the system prompt every turn** (`workspace_tools.py`), not a tool the model must remember to call — structurally un-forgettable. See the "wall" between agent_plan and the Library Kanban. |
| **`edit` tool global-search "shitshow"** | **Prax is better** | `workspace_save`/`workspace_patch` run a **language-aware syntax check** (AST/JSON/YAML) and reject broken writes *before* they hit disk (`_validate_syntax`). |
| **Auto-compaction forces full re-eval (~10 min)** | **Prax is better** | Prax favors **explicit on-disk handoffs**: per-space `progress_read`/`progress_append` that survive the context boundary, plus `checkpoint.md`. The author's own recommendation (editable on-disk artifacts over auto-compaction) is roughly what Prax already does. |
| **WebFetch prompt contradiction** ("NEVER generate URLs … you may use URLs") | **No** | No such contradiction in Prax's fetch/url_reader prompts (grepped). |
| **Context/cache: rereads files, embeds the date, aggressive pruning** | **PARTIALLY — the real finding** | See below. |

## The real finding: prompt caching

The author's sharpest technical point — *volatile content in the prompt destroys
the cache* — does land on Prax, in two concrete ways.

### 1. Anthropic path gets **zero** prompt caching
`llm_factory.build_llm` constructs `ChatAnthropic(...)` with no `cache_control`
anywhere. Anthropic prompt caching is **opt-in** (a `cache_control` breakpoint on
the content you want cached); without it, **every** Claude call reprocesses the
full ~28–42k-token system prompt at full price — and the orchestrator's ReAct
loop makes many calls per turn. (`ChatAnthropic.cache` is LangChain's *response*
cache, a different thing.) OpenAI, by contrast, caches stable prefixes
automatically, so the default (all-OpenAI) deployment is partly protected — but
any Claude tier (e.g. the escalated counselor's HIGH tier) pays full freight.

### 2. `PROMPT_SELECTIVITY_ENABLED=true` likely defeats *cross-turn* OpenAI caching
`.env-example` ships prompt-selectivity **on** (flag-campaign recommendation). It
**re-selects the base system prompt per user input** (`select_sections`), so the
big cacheable prefix **changes every turn** — which defeats OpenAI's automatic
cross-turn prefix caching of the system prompt. The optimization that saves
per-call tokens plausibly *costs* cache reuse, and **the flag campaign measured
per-call tokens, not cache-hit rate** — so this trade was never actually weighed.
(Within a single turn, the ReAct steps still share a stable growing prefix, so
in-turn caching is fine; the loss is turn-to-turn.)

### 3. Section ordering leaves stable content outside the cached prefix
`full_prompt = base + temporal(timestamp, HH:MM) + workspace + memory + hints`.
The **stable** hints (`tool_economy` / `budget_aware` / `verify_discipline`) sit
*after* the **volatile** temporal/workspace/memory blocks, so they fall outside
the auto-cacheable prefix. Stable-first ordering would extend the cached prefix.
The minute-granularity timestamp is minor (it's after the base prompt) but adds
churn.

## Recommended fixes (all need eval/live validation before default-on)

1. **Anthropic `cache_control` on the system prompt** — the clear gap. The clean
   seam is `orchestrator.py:1191` (`SystemMessage(content=full_prompt)`): on the
   Anthropic path, structure the content as a cached block. **Implementation
   caveat (why it isn't a one-liner):** the system content then becomes a *list*,
   and it flows into `context_manager.prepare_context` (token counting +
   compaction), which assumes *string* content — so this must apply **after**
   context prep (or teach the context manager list content), and be verified with
   a **live** Anthropic run confirming actual cache hits (a unit test only proves
   the transform). Flag-gated default-off.
2. **Measure cache-hit rate, then reconsider `PROMPT_SELECTIVITY` cross-turn.**
   The honest move is to instrument the hit rate and A/B selectivity on *total*
   cost (per-call savings vs cache-reuse loss), not per-call tokens alone.
3. **Order the system prompt stable-first, volatile-last** (base + hints, then
   temporal/workspace/memory) so the cached prefix is as long as possible.
   Behavior-adjacent (recency) → flag-gate + eval.
4. Coarsen the temporal timestamp granularity (drop `:MM`) — trivial, minor.

## Bottom line

The article is a security-and-craft critique of a *TUI coding tool*; Prax uses
OpenCode as a *headless engine* behind its own governance and container
boundary, so the security and UX complaints mostly miss, and several (todo,
edit-safety, handoffs) Prax already answers better. The one that sticks is
caching: **Prax genuinely under-caches — no Anthropic caching at all, and
prompt-selectivity probably eating cross-turn OpenAI caching.** Worth fixing, but
with measurement, not a rushed hot-path change.
