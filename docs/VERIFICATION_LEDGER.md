# Verification Ledger — implemented vs. verified-against-the-real-thing

This is a deliberately honest register of the gap between **"implemented and
unit-tested"** and **"verified end-to-end against the real external service."**

Unit tests prove our *logic* — request shaping, response parsing, error
handling, formatting. They do **not** prove that a third-party API still
returns the shape we coded to, that a credential path works, or that a
provider we've never held a key for behaves as documented. Those are different
claims, and conflating them would be exactly the "looks tested" dishonesty this
project refuses.

Prax is maintained by **one person**, so some external surfaces ship
implemented-to-spec and unit-tested but not yet exercised live — the key isn't
held, the account doesn't exist, or the manual run hasn't happened. That is a
*known, bounded* state, not a hidden one. This file names each such surface so
it can be picked up and verified — by the maintainer, eventually, or by a
contributor who *does* have the key/account. If you verify one, move its row up
and note how.

This ledger is the honest complement to
[`research/validation-campaign-2026-07-08.md`](research/validation-campaign-2026-07-08.md)
(what *is* measured and proven). Together they draw the real line.

> **This ledger is itself incomplete.** It captures the surfaces we know are
> unverified — chiefly this session's additions plus the ones the maintainer
> flagged. A full audit of all ~97 tools has **not** been done; absence from
> this list is not proof of verification.

## Status legend

| | Meaning |
|---|---|
| ✅ **Verified live** | Observed working against the real external service (how/when noted). |
| 🟡 **Partial** | One path/provider verified; siblings not. |
| 🧪 **Unit-tested only** | Logic proven with mocks; the real API/credential path has **not** been exercised end-to-end. |
| ⚪ **Unverified** | Implemented to spec; never run against the real service. |
| 🔍 **Needs audit** | Status not yet assessed — no one has checked. |

## Social / content fetch (`url_reader`)

| Surface | Status | Verified | Not verified / needs |
|---|---|---|---|
| **X / Twitter** thread fetch (API v2) | ✅ | `/2/tweets/search/recent` confirmed live on the maintainer's API tier; full self-thread fetch confirmed over TeamWork | — |
| **Bluesky** posts (public AppView) | ⚪ | — | Keyless by design (AppView needs no token), but never run against a live `bsky.app` post. Needs one real fetch to confirm the parse. |
| **Threads** (Meta Graph API) | ⚪ | — | `THREADS_API` is unset; threads.net links currently fall back to the web reader. Native path needs a token **and** an app with Advanced Access for `threads_basic`. Entirely un-exercised. |

## Search providers (`SEARCH_PROVIDER`, PR #71)

| Provider | Status | Verified | Not verified / needs |
|---|---|---|---|
| `legacy` / `ddgs` | ✅ | Used live in production (the failure traces that motivated the timeout work) | — (reliability is the *problem*, not the question) |
| `jina` | 🟡 | **Smoke-tested live 2026-07-08:** dispatch (`SEARCH_PROVIDER=jina` → `background_search`), bearer-auth wiring, and graceful degradation all confirmed against `s.jina.ai`. The test **caught a real bug**, now fixed: unlike the keyless Jina *reader*, the *search* endpoint **requires** `JINA_API_KEY` (401 keyless) — earlier code/docs wrongly said "keyless free tier works." | The 200 success path (real results parsing) is still unverified — no *valid* `JINA_API_KEY` is held (the maintainer runs the Jina reader keyless). Set a valid key and run one query to finish → ✅. |
| `brave` | ⚪ | Request shape + response parsing unit-tested against Brave's **documented** contract (mocked HTTP) | No `BRAVE_API_KEY` held — never called live. If a field name drifted from the docs it's a one-line fix in `_brave_search`. |
| `tavily` | ⚪ | Request/parse + answer-first behaviour unit-tested against the **documented** contract (mocked HTTP) | No `TAVILY_API_KEY` held — never called live. Same one-line-fix risk as Brave. |
| `serper` | ✅ | **Verified live 2026-07-16:** real `SERPER_DEV_API_KEY` set; `_serper_search` returned correct Google results + answer-box parsing over `google.serper.dev/search`. Request shape, key header, answer-box/knowledge-graph fallback also unit-tested (mocked HTTP). | — |

## Media generation (builtin tools)

| Tool | Status | Verified | Not verified / needs |
|---|---|---|---|
| `generate_image` (PR #67) | 🧪 | b64 + URL response paths, missing-key/empty-prompt guidance, non-image-model → dall-e-3 fallback all unit-tested (mocked OpenAI) | Never generated a *real* image against the OpenAI Images API — needs one paid call to confirm the model name (`IMAGE_MODEL`, default `gpt-image-1`) and byte handling. |
| `text_to_speech` | 🧪 | OpenAI path + ElevenLabs fallback, deliverable-file flow, actionable failures all unit-tested (mocked) | No real audio synthesised live (ironic given it was born from an audio-file failure). Both the OpenAI and `ELEVENLABS_API_KEY` paths want one live run each. |
| `analyze_image` (vision/OCR) | 🧪 | Bug fixes applied from the live failure trace (`max_tokens`→`max_completion_tokens`; model default off the image-gen model) | The corrected path has not been re-confirmed against a real image end-to-end this session. |

## Lean proof-check tool (`lean_check`, `LEAN_TOOLS_ENABLED`)

| Surface | Status | Verified | Not verified / needs |
|---|---|---|---|
| **`lean_check`** (compile + axiom-audit trust gate, in the sandbox) | ✅ | **Verified live 2026-07-14** against Lean 4.31.0 installed in the running sandbox container, driving the real tool through the sandbox client on 5 known-result theorems: `1+1=2` and `p∧q→q∧p` verify clean (no axioms); `1+1=3` fails with the correct type-mismatch diagnostic; a `sorry` hole compiles but the trust gate + axiom audit both catch it (`sorryAx`); an injected `axiom cheat` is flagged non-standard. | mathlib-dependent proofs (need a lake project + `lake exe cache get`) are out of scope — toolchain-only. The durable toolchain lives in the prax-sandbox image (Dockerfile `ENV ELAN_HOME=/opt/elan`); a from-clean **image rebuild** installing Lean has not been run yet (the live container was provisioned in place) — verify on the next sandbox rebuild. |

## Coding-agent benchmark (`terminal_bench`)

| Surface | Status | Verified | Not verified / needs |
|---|---|---|---|
| **`terminal_bench` adapter** (agent → bash solution → sandbox execution → hidden verify) | 🟡 | **Verified live 2026-07-23**: full path exercised end-to-end through the real orchestrator (deepseek-v4-flash via the keyless proxy) on all 5 seed tasks — **5/5 pass, 0 errors**, each graded by running the solution + a hidden verify in the sandbox (`__TB_OK__` / exit 0). Keyless CI proves every canonical solution passes its own verify, wrong solutions fail, extraction handles fenced/raw, sandbox-absent degrades cleanly. | **NOT the official Terminal-Bench number.** This is 5 hand-authored seed tasks (easy: create/count/sort/parse/script), n=5 (95% CI 56.6–100%), Prax-run + sandbox-scored — NOT the official tbench Docker-per-task harness on the real ~100+ task set. A leaderboard-comparable figure needs the real task set + per-task environments loaded into `PRAX_EVAL_DIR` (tracked in `docs/research/adopt-tracker.md`). Do not compare this to model leaderboard scores. |

## Secrets proxy (`prax-secrets-proxy`)

| Surface | Status | Verified | Not verified / needs |
|---|---|---|---|
| **Reverse proxy** (model keys via base-URL: `OPENAI_BASE_URL`/`ANTHROPIC_BASE_URL`) | ✅ | **Verified live 2026-07-22** — the container runs over TLS on `127.0.0.1:8785`, `/healthz` returns provider readiness, the token gate rejects un-tokened calls (401), and the combined CA bundle trusts both the self-signed proxy *and* real endpoints (system CAs preserved). | A full keyless round-trip *through Prax* to a real model completion wasn't run in CI (needs a real key in the proxy `.env`); the operator confirms it. |
| **Forward (MITM) proxy** (all REST egress via `HTTPS_PROXY`, registry-driven injection) | 🟡 | **Verified live 2026-07-22** through the running mitmproxy forward proxy (registry-generated map, keyless client, real key injected per host). **Return 200:** OpenAI, **Serper**, **Twilio** (basic auth), **Twitter** (bearer), **Hugging Face** (bearer) — and **Prax's own web search runs keyless through the proxy** (placeholder key → proxy strips + injects). Caught + fixed 3 real bugs doing it: the mitmproxy addon crash (`__init__` eager Flask/requests import), `.env` proxy vars never reaching `os.environ` (fixed in `app.py`, allow-list only, no secret leak), and the CA bundle missing the mitmproxy CA. Injector unit-tested (10) + forward-map never-drift test. | **Inject correctly but non-200 from the provider (key/account issue, NOT the proxy):** ElevenLabs 401 (held key stale → rotate); Google 403 (project lacks Custom Search access). **Wired but no key held to test:** Brave, Tavily, Threads, Jina. **Not proxyable (stays in Prax):** Discord (gateway websocket + `Bot ` prefix → reclassified LOCAL), Amadeus (OAuth), NYT (login), + the 9 local creds. To make it Prax's live default, set the REST keys to a **non-empty placeholder** (presence-guards) and point `HTTPS_PROXY` + CA bundle. |

## Multi-turn eval suite (`prax/eval/multiturn.py`, `make eval-multiturn`)

| Surface | Status | Verified | Not verified / needs |
|---|---|---|---|
| **Framework** (conversation loop, deterministic final-state grading, pass^k, YAML loading) | ✅ | Unit-tested keyless with injected agent/user-sim stubs (11 tests): alternation, done-signal early-exit, max-turns bound, content-on-final-turn vs tool/spoke-on-union grading, executor-error handling, flaky pass^k, suite aggregation. | — (pure logic; nothing external) |
| **Live executors** (`orchestrator_agent`, `bare_agent`, `llm_user_simulator`) | 🧪 | Wiring implemented (threads history through `agent.run(conversation=…)`; a cheap LLM plays the user). | **Never run live** — no `make eval-multiturn` pass against a real model yet. Needs one live run (real user-simulator + full orchestrator) to confirm the persona loop resolves and the seed cases grade sensibly. Verify with a cheap model, then move this to ✅. |

## Tier system (orchestrator)

| Mechanism | Status | Verified | Not verified / needs |
|---|---|---|---|
| Auto tier escalation (PR #62) | 🧪 | Ladder (low→medium→high, reset per turn, graceful stop at ceiling) unit-tested | A **live** recursion → escalate → recover-at-higher-tier has not been observed in production. The mechanism is proven; the real-world save is not yet witnessed. |
| Session `self_upgrade_tier` boost (PR #68) | 🧪 | In-memory floor, no config write, reset-on-restart all unit-tested | Live agent-initiated boost + next-turn effect not observed in production. |

## Git repositories attached to a space (`space_repos`, `SPACE_REPOS_ENABLED`)

| Surface | Status | Verified | Not verified / needs |
|---|---|---|---|
| Clone / pull / commit / push over SSH with a per-repo deploy key | 🧪 | Path-traversal guards, the per-repo write gate, key generation and permissions, and the no-global-git-identity case are unit-tested against real local git repositories (`file://` origins) — including a regression test that reproduces a host with no `~/.gitconfig`. | **No key has ever been installed on GitHub.** The whole point of a deploy key is what the *remote* does with it, and that half is untested: whether `IdentitiesOnly=yes` behaves as intended against `github.com`, whether a read-only key is refused on push with a legible error, and whether host-key acceptance works on first contact. Needs one real repo: attach, add the printed key, pull, enable write, push. |

## Keyless Responses API + retained reasoning (`OPENAI_BASE_URL_IS_OPENAI`, `OPENAI_RETAIN_REASONING`)

| Surface | Status | Verified | Not verified / needs |
|---|---|---|---|
| `/v1/responses` through the secrets proxy + `previous_response_id` chaining | ✅ | 2026-07-30, dev box: `build_llm(model="o4-mini")` with both flags against the live proxy (`:8785`, TLS) — two sequential calls returned reasoning blocks; `use_responses_api`/`use_previous_response_id` wiring confirmed live, plus 6 keyless unit tests for every flag combination. | The **behavioral win** (better multi-step task performance from retained reasoning) is OpenAI's measured claim, not ours — needs an eval-gate A/B before the flags are recommended in `.env-example`. Third-party-base-URL demotion path unchanged and covered by existing tests. |

## Terminal-Bench 2.0 harbor adapter (`prax/eval/tb_agent.py`)

| Surface | Status | Verified | Not verified / needs |
|---|---|---|---|
| PraxAgent end-to-end under harbor (container exec bridge, keyless model path, metadata population, hidden-verifier scoring) | ✅ | 2026-07-30, dev box: `harbor run -a prax.eval.tb_agent:PraxAgent -i break-filter-js-from-html` — 21 real terminal steps in the task container via the forward proxy (qwen3-coder-30b), trial completed 0 errors, verifier scored (reward 0.0 — an honest fail for a 30B), metadata (harness label/steps/error) landed in the trial record. | Re-verified after the accounting fix (2026-07-30, same task): 16 steps, **69,831 in / 3,895 out, $0.0059** recorded correctly, agent finished cleanly. **Full 89-task sweep run 2026-08-02**: 13.5% (12/89 attempted) / 15.6% (12/77 excluding 16 infra-timeout trials), $0.46 total, 9h30m — details in [the guide](guides/terminal-bench.md); not leaderboard-submitted, and the non-scoring trials are largely 2-core-box timeouts so the result is a range on this hardware, not a point. Note the honest result from the single-task verification: the agent self-declared success with a confident summary and the hidden verifier scored **0.0** — a textbook [CRUX](research/crux-shadow-evals.md) failure-mode-1 (no calibrated model of the bar), and the reason the score comes from the task's verifier and never from the agent. |

## TeamWork MCP server (`MCP_ENABLED`, `teamwork/src/teamwork/mcp_server.py`)

| Surface | Status | Verified | Not verified / needs |
|---|---|---|---|
| Authorisation (MCP grant, per-space scoping, capabilities, approval gates) | 🧪 | 41 tests, including that enabling the flag without granting a key changes nothing, that a scoped key naming no space is refused, and that a refused call never reaches Prax. | — (this is logic, and logic is what tests prove) |
| The protocol itself, against a real client | ⚪ | — | **No MCP client has ever connected.** Claude Code and Codex have their own view of what the handshake and tool schemas should look like, and conformance to a spec on paper is not the same as working with an implementation. Needs: `MCP_ENABLED=true`, a registry key, `claude mcp add --transport http`, then `list_spaces` returning exactly the granted space. |

## Flagged for audit (not assessed this pass)

| Surface | Status | Note |
|---|---|---|
| Sandbox **browser tools** (Chromium/CDP, browser spoke) | 🔍 | Named by the maintainer as unverified. Real navigate/extract/screenshot flows through the sandbox have not been confirmed end-to-end in this pass. |
| Sandbox **desktop / noVNC** | 🔍 | Not assessed. |
| **MCP server** (`prax/mcp/`, default-off) | 🔍 | Real external-agent-over-MCP usage not confirmed here (has unit tests). |

## Process — keep this honest

When you ship something that talks to an external service or credential you did
**not** exercise live, **add a row here** in the same PR. "Unit-tested only" is
a fine state to ship in — pretending it's verified is not. When you later run it
for real, update the row (status, how, date). The goal is that this file is
always the truthful answer to "what have we actually watched work?"
