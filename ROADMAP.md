# Modernization Roadmap

This document captures the high-level work items needed to bring the project from its early-LLM architecture to a modern, agentic, multi-provider platform. Treat each checklist as an independently plannable effort.

## 1. Preserve And Refine Core Integrations
- [ ] Keep the Twilio blueprints as thin HTTP adapters, but refactor them to call well-scoped service classes rather than inlining logic in route handlers.
- [ ] Preserve the existing reader modules (NPR, arXiv, NYT, web summarizer) by wrapping each as a reusable "tool" interface with clear inputs/outputs.
- [ ] Keep helper dictionaries (language prompts, transcription mappings) but move static data into version-controlled JSON/YAML under `data/` for easier updates.

## 2. Rebuild Unsafe Foundations
- [ ] Replace `convo_states` with a durable conversation/session store (Redis, Postgres, or DynamoDB) that survives process restarts and supports sharding.
- [ ] Introduce a proper config layer (e.g., Pydantic `BaseSettings`) so runtime configuration is validated and typed.
- [ ] Swap thread-per-request background work for a task queue (Celery, RQ, Temporal, or AWS SQS + Lambda) with retry semantics and metrics.
- [ ] Harden the SQLite conversation memory by adding migrations, connection pooling, and a repository layer (or migrate to Postgres entirely).
- [ ] Implement structured logging + telemetry (OpenTelemetry, Prometheus) with request IDs and PII redaction.
- [ ] Expand automated testing: pytest + factories, contract tests for Twilio webhooks, linting (ruff) and typing (mypy) in CI.

## 3. Agentic Evolution
- [ ] Insert an intent classifier/router ahead of `askgpt` so each inbound request becomes a structured task (chat, search, media processing, reader access).
- [ ] Wrap legacy capabilities (news, NPR, web-to-MP3, background search) as callable tools with JSON schemas so an orchestrator can invoke them.
- [ ] Introduce a memory service that summarizes history, persists long-term facts, and feeds relevant snippets to the agent per turn.
- [ ] Move long-running actions (web conversion, audio merges) into queued workflows and send asynchronous updates via SMS/voice when results are ready.

## 4. Recommended Agentic Framework
- **Choice:** Adopt [LangChain](https://github.com/langchain-ai/langchain) with its LangGraph orchestration layer.
  - Works with OpenAI, Anthropic, Google, Azure, Azure AI Studio, and local LLMs (via HuggingFace, vLLM, Ollama, etc.).
  - Provides a first-class tool calling interface, memory components, and the ability to compose state machines/graphs that persist between steps.
  - Open-source and self-hostable, so swapping or mixing providers per capability is straightforward.
- **Integration Plan:**
  1. Start with LangChain runnable agents for SMS and voice flows that call existing tools synchronously.
  2. Migrate to LangGraph for multi-step plans (e.g., plan → tool invocation → summarization → response) while storing graph state in Redis.
  3. Add local-model backends by wiring Ollama/vLLM endpoints into LangChain `LLM` abstractions for on-prem deployments.

## 5. Execution Phases
1. **Stabilize:** Config layer, persistent session store, logging/metrics, test baseline.
2. **Modularize:** Extract service layer + tool wrappers, move readers/helpers into libraries, add task queue.
3. **Agentize:** Introduce router + LangChain agents, implement memory service, convert Twilio flows to agent-driven pipelines.
4. **Optimize:** Experiment with provider-specific models (OpenAI, Anthropic, Google, local) per task; add evaluation harnesses and continuous improvement loops.

Each checkbox can become a ticket/PR. Update this roadmap as milestones complete.
