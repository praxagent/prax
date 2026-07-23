# IBM Think — "What are AI agents?" — assessment

**Source:** [ibm.com/think/topics/ai-agents](https://www.ibm.com/think/topics/ai-agents)
(Anna Gutowska, IBM Think topic hub). An **introductory education page** — a ~2,000-word
101 article wrapped in a large left-nav of deeper sub-topics (ReAct, ReWOO, MCP, A2A,
crewAI, LangGraph, governance…).
**Verdict:** **Document-don't-adopt.** For a harness at Prax's maturity this is
introductory/navigational material — everything in the body Prax already does, usually
more rigorously. No adopt candidate rises to "build X." The only external value is
*corroboration* of one risk Prax already mitigates, plus two governance primary sources
worth harvesting from its citations (not from the IBM page itself).

## What it covers (and why none of it is new here)

- **Definition + 3-stage loop** (goal/plan → reason-with-tools/self-correct → learn/
  reflect) and the textbook **Russell-Norvig agent types** (simple-reflex → model-based →
  goal-based → utility-based → learning). Standard; Prax's orchestrator + self-regen loop
  already sit at the "learning agent" end.
- **Reasoning paradigms:** ReAct (think-act-observe) and ReWOO (plan upfront, decouple
  from tool outputs — pitched for plan-confirmation-before-execution + token savings +
  reduced blast radius of a bad intermediate tool call). Prax runs a ReAct loop; ReWOO's
  "confirm the plan first" is the same instinct as Prax's plan-visibility work.
- **Best practices:** activity logs, interruptibility, unique agent identifiers, human
  approval before high-impact actions. Each maps directly onto something Prax already
  has — audit log; governed-tool risk tiers + lethal-trifecta guard; MCP per-caller
  identity; HITL approval. Nothing to build.

## The two things worth a sentence (corroboration, not novelty)

1. **Correlated failure from a shared foundation model.** IBM's sharpest point: a hub +
   many sub-agents all on *one* model share blind spots and adversarial vulnerabilities →
   "system-wide failure." That's external corroboration of Prax's **cross-provider LLM
   failover** flag and the heterogeneity argument already in the
   [AIDE²](aide2-recursive-self-improvement.md) / [reliable-agentic-systems](reliable-agentic-systems-bayer.md)
   threads — an argument *for* a design choice Prax has already made, not a new idea.
2. **Developer / deployer / user accountability triad + unique agent identifiers.** A
   cleaner *attribution* vocabulary for cross-agent calls than "trust tiers," relevant to
   Prax's MCP per-caller identity + any A2A future. But it traces to two **primary
   sources** worth reading directly and better than the IBM page: OpenAI's *"Practices for
   Governing Agentic AI Systems"* (an OpenAI publication, Dec 2023) and Chan et al.
   *"Visibility into AI Agents"* ([arXiv 2401.13138](https://arxiv.org/abs/2401.13138)).
   Harvest the citations; skip the page.

Minor: IBM's **"interruptibility with judgment"** nuance ("some terminations can cause
more harm than good" — don't blind-kill a faulty agent mid-emergency) is a subtlety a
naive kill-switch design can miss. Worth remembering when Prax builds any hard-stop.

## Bottom line

A competent 101 hub, not a source of features for a mature harness. **Document-don't-
adopt:** the only external value is corroboration of the shared-foundation-model
correlated-failure risk (→ cross-provider failover) and the developer/deployer/user
attribution framing — both better sourced from its citations (OpenAI's governance
practices; Chan et al.'s *Visibility into AI Agents*), which go on the governance backlog.
Sits alongside [capy](capy-swe-agent-platform.md) and [matrix.build](matrix-autonomous-company.md)
as a "validates the shape, nothing to build" reference; the substantive companion in this
same drop is [CoALA](coala-cognitive-architectures.md), which *does* earn an adopt-as-lens.
