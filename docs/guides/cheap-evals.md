# Running evals cheaply (and without bill-shock)

[← Guides](README.md)

Prax's `make eval` makes real LLM calls (agent replay + judge), so it costs money.
This guide is how to run evals for pennies **and make a surprise bill structurally
impossible.**

## The one idea: prepaid providers can't overspend

The strongest guarantee isn't a spending cap you have to remember to set — it's a
provider you **prepay**. Load $5–10 of credit; when it's gone, calls just stop.
There is no postpaid invoice to be surprised by.

Two good OpenAI-compatible prepaid options:

- **[OpenRouter](https://openrouter.ai)** — one prepaid key fronts hundreds of
  models (many free-tier), plus it doubles as cross-provider failover. Small
  markup. Base URL: `https://openrouter.ai/api/v1`.
- **[DeepSeek](https://api.deepseek.com)** — the cheapest *quality* per token
  (V3-class ≈ $0.14/$0.28 per 1M in/out), also prepaid. Base URL:
  `https://api.deepseek.com`.

## The easy path: OpenRouter + `make eval CHEAP=1`

Put your OpenRouter key in `.env`:

```dotenv
OPENROUTER_API_KEY=sk-or-xxxx
```

Then run **any** eval target with `CHEAP=1`:

```bash
make eval CHEAP=1              # regression replay + goldens
make eval-capability CHEAP=1  # the 7-case capability suite
make eval-benchmark BENCH=ifeval CHEAP=1
```

`CHEAP=1` switches the provider to `openrouter` and points **every tier** at one
cheap model — `deepseek/deepseek-v4-flash` by default — **for that make invocation
only.** Production (`make run-local-*`, `restart-prax`) is untouched: the key's
mere presence never redirects the live server. Pick a different model with
`OPENROUTER_EVAL_MODEL=<slug>` (browse slugs at
[openrouter.ai/models](https://openrouter.ai/models)).

A full pass is **~$0.20–0.35**; a prepaid balance is your hard ceiling.

**Caveat:** vision cases (`analyze_image`, some GAIA tasks) still use
`VISION_PROVIDER`/`VISION_MODEL` — point those at OpenRouter too, or run
text-only suites, if you want a pure-OpenRouter run.

## Manual passthrough (any provider)

For a provider without the `CHEAP=1` shortcut, set the OpenAI-compatible client
directly:

```dotenv
OPENAI_BASE_URL=https://api.deepseek.com   # or another OpenAI-compatible endpoint
OPENAI_KEY=<your provider key>
```

`LLM_PROVIDER` stays `openai`; Prax auto-disables OpenAI-proprietary features
(Responses API + `logprobs`) that third parties don't implement. Set the tier
models to that provider's slugs. Leave `OPENAI_BASE_URL` unset for OpenAI (default).

## The zero-code alternative: OpenAI nano + a hard cap

If you'd rather not switch providers, OpenAI is already cheap on the nano tier —
a *full* flag-eval campaign (7 arms + benchmarks, ~2.3M tokens) cost **under $2**.
Set a **hard monthly usage limit** in the OpenAI billing dashboard (e.g. $10);
it's postpaid but stops at the ceiling.

## The guards that actually stop a runaway bill (already in Prax)

Regardless of provider, these are what prevent a loop from spending real money:

- **`make ci` is keyless** — the ~2,450 logic tests make **zero** API calls.
  Only `make eval` (live replay + judge) costs anything.
- **Goldens list for free.** `run_golden_suite` only scores when
  `PRAX_EVAL_GOLDENS=1`; otherwise it just lists tracked targets.
- **`PRAX_EVAL_MAX_CASES`** caps how many recorded cases replay (default 20 — set
  `3`–`5` for a cheap smoke).
- **Keep the orchestrator on the nano/low tier** for eval runs; the judge is low
  tier already.
- **Cost is measured**, not guessed — the HAL axis (`pass_per_1k_tokens`,
  `avg_full_tokens`) is reported so you see spend per run.

## Recommendation

For "cheap **and** can't-get-a-huge-bill," use **OpenRouter or DeepSeek with a
prepaid balance** — the prepaid model is the guarantee. Keep `PRAX_EVAL_MAX_CASES`
low and let `make ci` stay keyless. If you don't want to touch providers, run on
OpenAI nano with a $10 hard cap.
