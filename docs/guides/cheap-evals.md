# Running evals with cost controls

[← Guides](README.md)

Prax's live evaluations make provider calls for agent replay and judging. Limit
the number of cases, choose models deliberately, and configure provider-side
spending controls before running them. No client-side setting here guarantees an
exact dollar ceiling across every tool and provider.

## Provider billing controls

With a prepaid provider, start with a small balance and review auto-recharge and
other billing settings. OpenRouter supports both manual credit purchases and
auto top-up; turn auto top-up off if a fixed purchased balance is your intended
constraint. Its activity view reports usage by model, provider, and API key.
See [OpenRouter's billing documentation](https://openrouter.ai/docs/faq#credit-and-billing-systems).

For another OpenAI-compatible provider, verify its current prices, payment terms,
concurrent-request behavior, and exhaustion policy before relying on a balance as
a limit. Prepaid billing is not a universal guarantee that overage is impossible.

## The easy path: OpenRouter + `make eval CHEAP=1`

Set `OPENROUTER_API_KEY` in private configuration, or configure the corresponding
proxy route. Then run a small evaluation:

```bash
PRAX_EVAL_MAX_CASES=3 make eval CHEAP=1
make eval-capability CHEAP=1
make eval-benchmark BENCH=ifeval CHEAP=1
```

`CHEAP=1` selects OpenRouter and points the tier models at
`deepseek/deepseek-v4-flash` for that Make invocation. Override it with
`OPENROUTER_EVAL_MODEL=<slug>` after checking the current
[model catalog and prices](https://openrouter.ai/models). It does not redirect a
separately running production process.

The Makefile also selects Ollama embeddings with `nomic-embed-text`. Run Ollama
and pull that model before evaluation, using the host or container address
appropriate to the evaluation process. Missing local embeddings can cause failures.

Vision, search, speech, and other external tools have separate configuration and
may incur additional charges. Inspect the suite and its enabled tools; routing
the language-model tiers through one provider does not route every paid request.
Cost depends on case count, prompts, tokens, retries, judges, models, and tools;
previous campaign totals are not a quote for a new run.

## Manual passthrough (any provider)

For an OpenAI-compatible provider without the shortcut, configure:

```env
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://your-provider.example/v1
OPENAI_KEY=<provider key or configured proxy credential>
```

Replace the example URL and choose model identifiers supported by that provider.
Prax disables its OpenAI-specific Responses/logprobs path for non-OpenAI base
URLs. This does not establish complete compatibility with every third-party API.
Leave `OPENAI_BASE_URL` unset for the direct OpenAI endpoint.

## OpenAI: enable enforcement, not only alerts

As of September 4, 2026, OpenAI supports organization and project hard spend
limits. In the API Platform settings, open the intended organization's or
project's Limits page, set the monthly amount, enable **Enforce a hard limit**,
and save. Spend alerts alone notify you; they do not stop traffic.

When tracked spend reaches the applicable hard limit, affected requests return
`429` with `organization_spend_limit_exceeded` or `project_spend_limit_exceeded`.
Enforcement propagates asynchronously, so a small amount of additional usage can
occur and recorded spend may slightly exceed the configured amount. Organization
limits cover all its projects; project limits cover traffic billed to that
project. See [OpenAI's spend-limit documentation](https://developers.openai.com/api/docs/guides/spend-limits).

Use a separate evaluation project where practical and confirm the evaluation's
credential bills to it. Limits on one provider do not cover charges from another.

## Prax controls and reporting

- `make ci` runs the automated logic suite with live/integration cases excluded;
  live evaluation targets are separate. Keep provider credentials out of CI.
- `run_golden_suite` scores only with `PRAX_EVAL_GOLDENS=1`; otherwise it lists
  tracked targets.
- `PRAX_EVAL_MAX_CASES` limits recorded-case replay (default 20). It is not a
  universal cap on every benchmark target or every API call within a case.
- Loop, round, and failure limits reduce runaway work. They are not dollar caps.
- Evaluation output reports token usage and USD estimates where supported.
  `prax/eval/pricing.py` is an estimate table, not the provider's billing ledger.
  Use `EVAL_COST_INPUT_PER_M` and `EVAL_COST_OUTPUT_PER_M` to supply current rates;
  unknown models report `n/a`.

Run a small sample, compare its usage with the provider dashboard, then increase
the scope. Keep alerts and enforcement configured while monitoring the run.
