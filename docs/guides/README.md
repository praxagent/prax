# Guides

Practical guides for setting up, extending, and testing Prax.

## Contents

- [Setup](setup.md) — Prerequisites, installation, database, running the app
- [Extending](extending.md) — Plugin system, manual tool registration, workspace locking
- [Testing](testing.md) — Unit tests, e2e tests, integration tests, A/B testing
- [Channels](channels.md) — TeamWork, Discord, Twilio setup
- [Local models — vision and inference](local-vision.md) — Run Prax fully off-OpenAI: point both `analyze_image` and the chat LLM at a local llama.cpp / vLLM / Ollama server.  Includes a verified Qwen3.6-35B-A3B config for 16 GB GPUs.
- [Library](../library.md) — Hierarchical knowledge base (Project → Notebook → Note) with author provenance and the Karpathy-inspired raw/outputs split
- [Scheduler](scheduler.md) — Cron jobs, reminders, timezone, YAML format, TeamWork UI
- [Trajectory Export](trajectory-export.md) — Real-time training data export with outcome classification
- [Feedback Loop](feedback-loop.md) — Agent improvement loop: feedback, failure journal, eval runner
- [Authentication](authentication.md) — Tailscale, Google/GitHub OAuth, Authentik, multi-user routing
- [Troubleshooting](troubleshooting.md) — Common issues and fixes
