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

## Tier system (orchestrator)

| Mechanism | Status | Verified | Not verified / needs |
|---|---|---|---|
| Auto tier escalation (PR #62) | 🧪 | Ladder (low→medium→high, reset per turn, graceful stop at ceiling) unit-tested | A **live** recursion → escalate → recover-at-higher-tier has not been observed in production. The mechanism is proven; the real-world save is not yet witnessed. |
| Session `self_upgrade_tier` boost (PR #68) | 🧪 | In-memory floor, no config write, reset-on-restart all unit-tested | Live agent-initiated boost + next-turn effect not observed in production. |

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
