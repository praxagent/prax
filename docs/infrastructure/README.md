# Infrastructure

Prax runs on a stack of containerized services: a Flask app, an always-on Docker sandbox, a Playwright browser, and an optional observability pipeline.

## Contents

- [Sandbox](sandbox.md) — Docker + OpenCode code execution, solution reuse, budget control
- [Desktop](desktop.md) — VNC desktop environment, computer-use tools (xdotool + scrot), VS Code, browser unification
- [Browser](browser.md) — Playwright automation, persistent profiles, VNC login, CDP vs Playwright
- [Content Publishing](content-publishing.md) — Notes, courses, news — Hugo vs TeamWork API, editing, versioning
- [Observability](observability.md) — Traces, metrics, Grafana LGTM stack, execution graphs
- [Memory](memory.md) — Two-layer memory system (STM + LTM), vector store, knowledge graph, hybrid retrieval, decay, embedding providers
- [Context Management](context-management.md) — Token budgeting, tool result clearing, LLM compaction, hard truncation
- [Health Monitoring](health-monitoring.md) — Telemetry events, anomaly detection, self-repair advisories, health API
- [Docker](docker.md) — Docker Compose setup, dev mode, standalone deployment
