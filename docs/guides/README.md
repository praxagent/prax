# Guides

Practical guides for setting up, extending, and testing Prax.

## Contents

- [Setup](setup.md) — Prerequisites, installation, database, running the app
- [Extending](extending.md) — Plugin system, manual tool registration, workspace locking
- [Testing](testing.md) — Unit tests, e2e tests, integration tests, A/B testing
- [Channels](../security/configuration.md#channel-setup) — TeamWork, Discord, Twilio setup (in the configuration reference)
- [Social posts (X, Bluesky, Threads) fetch](social-posts-fetch.md) — post links are fetched via each platform's API instead of the locked-down web reader: **X** (`TWITTER_API` bearer), **Bluesky** (open public AppView, no key), **Threads** (`THREADS_API`, limited by Meta's Advanced-Access rules). Transparent — hooks `url_reader.fetch_markdown`, so `fetch_url_content`, note-from-URL, and auto-capture all get it; each path fails safe to the reader.
- [Local models — vision and inference](local-vision.md) — Run Prax fully off-OpenAI: point both `analyze_image` and the chat LLM at a local llama.cpp / vLLM / Ollama server.  Includes a verified Qwen3.6-35B-A3B config for 16 GB GPUs, co-hosting two models on one GPU, and pointers to cloud GPU + fine-tuning.
- [GPU access — local, cloud, least-privilege power control](cloud-gpu.md) — Plug-and-play GPU for Prax: a decision flow (local → cloud → hosted fallback), a top-~10 cloud-GPU-provider table (provision + start/stop + scopable creds + $), and a **secure "on/off only"** design (ARN-pinned AWS IAM + GCP custom role + a provider-agnostic power-broker + threat model) so Prax can launch a GPU, serve a model, and power it off — and *nothing else*. No model hard-wired; recurring recipes become workspace plugins.
- [Big models without a rented GPU — CPU · Mac · DGX Spark](local-cpu-inference.md) — Serve a big (MoE) model on memory you already own for **overnight/multi-day evals**: a hardware sizing table, **llama.cpp** for CPU-only Linux and **ds4 (DeepSeek V4)** for Mac/Spark, Prax wiring (`VLLM_BASE_URL`), and how to run the resumable eval suites with no per-task timeout.
- [The eval matrix — full scorecard & historical record](eval-matrix.md) — One command (`make eval-matrix`) to run **every benchmark on its real dataset** through the full harness; prereqs (prepaid key, Ollama embeddings, `fetch_eval_datasets.py`, gated GPQA), cost/time, and **the committed, aggregates-only public results record** (`docs/eval-results/`) that tracks progress over time.
- [Auditing the LLM judges](judge-audit.md) — Stability (same input, same verdict) and bias (the verdict tracks the graded thing, not e.g. length) for every place Prax grades itself with a model, with keyless probes in `prax/eval/judge_bias.py`.
- [Running Terminal-Bench 2.0](terminal-bench.md) — Prax as a harbor agent (`prax/eval/tb_agent.py`), how to run the 89-task suite, and the honest reading of the result.
- [Grading the trace, not just the answer](trace-grading.md) — Scoring the *process* (commitment, verification, efficiency) alongside the final answer.
- [Running evals with cost controls](cheap-evals.md) — Configure provider spend enforcement, review prepaid and auto-recharge behavior, bound evaluation scope, and distinguish token-cost estimates from billed usage.
- [Running Prax programmatically](programmatic-usage.md) — Ask Prax **one prompt through the full harness** from the CLI (`scripts/ask_prax.py`) or a script (`orchestrator_executor`), in an isolated throwaway workspace — the answer plus which tools/spokes it used and its token cost. Full env-var reference (model/provider, tools, timeouts, self-rate-limiting) and a worked probe example.
- [Switching embedding providers (+ re-embedding memory)](embeddings-migration.md) — Providers embed at different **dimensions** (openai 1536 / ollama 768 / local 384), so switching needs a **bidirectional** re-embed migration (`scripts/reembed_memories.py`) *and* an `.env` change, together. Safe (read-first, backup, no point dropped). Includes the Ollama local-embeddings setup.
- [Library](../library.md) — Hierarchical knowledge base (Project → Notebook → Note) with author provenance and the Karpathy-inspired raw/outputs split
- [Scheduler](scheduler.md) — Cron jobs, reminders, timezone, YAML format, TeamWork UI
- [Trajectory Export](trajectory-export.md) — Real-time training data export with outcome classification
- [Feedback Loop](feedback-loop.md) — Agent improvement loop: feedback, failure journal, eval runner
- [Authentication](authentication.md) — Tailscale, Google/GitHub OAuth, Authentik, multi-user routing
- [Git hygiene](git-hygiene.md) — Keep the repo clean of data & secrets: what never gets committed, the gitignore-glob gotcha (exact `conversations.db` misses `conversations.db.legacy-backup`), a pre-commit scan (never blanket `git add -A`), and the `git filter-repo` surgery to purge a file from all history — with the residual-exposure caveats (forks, old SHAs, GitHub Support) for public repos.
- [Troubleshooting](troubleshooting.md) — Common issues and fixes
- [Flag audit](flag-audit.md) — the 61-flag inventory, what merged, what should be deleted, and the rule for when a flag has earned its place
