# Infrastructure

Prax runs on a stack of containerized services: a Flask app, an optional Docker sandbox, a Playwright browser, and an optional observability pipeline.

## Contents

- [Sandbox](sandbox.md) — how to provide Prax a sandbox (local or remote), and how Prax uses it
- [MCP Server](mcp-server.md) — expose a curated, bearer-gated subset of Prax tools to other agents over the Model Context Protocol
- [Content Publishing](content-publishing.md) — Notes, courses, news — Hugo vs TeamWork API, editing, versioning
- [Observability](observability.md) — Traces, metrics, Grafana LGTM stack, execution graphs
- [Memory](memory.md) — Two-layer memory system (STM + LTM), vector store, knowledge graph, hybrid retrieval, decay, embedding providers
- [Context Management](context-management.md) — Token budgeting, tool result clearing, LLM compaction, hard truncation
- [Health Monitoring](health-monitoring.md) — Telemetry events, anomaly detection, self-repair advisories, health API
- [Docker](docker.md) — Docker Compose setup, dev mode, standalone deployment
