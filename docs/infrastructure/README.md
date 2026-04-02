# Infrastructure

Prax runs on a stack of containerized services: a Flask app, an always-on Docker sandbox, a Playwright browser, and an optional observability pipeline.

## Contents

- [Sandbox](sandbox.md) — Docker + OpenCode code execution, solution reuse, budget control
- [Browser](browser.md) — Playwright automation, persistent profiles, VNC login, CDP vs Playwright
- [Content Publishing](content-publishing.md) — Notes, courses, news — Hugo vs TeamWork API, editing, versioning
- [Observability](observability.md) — Traces, metrics, Grafana LGTM stack, execution graphs
- [Docker](docker.md) — Docker Compose setup, dev mode, standalone deployment
