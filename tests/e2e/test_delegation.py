"""E2E tests for sub-agent and spoke delegation workflows."""
from tests.e2e.conftest import ai, ai_tools, tc


def test_delegate_research(run_e2e):
    """Agent delegates a research task to the research sub-agent."""
    response, llm = run_e2e(
        "Research the latest advances in protein folding",
        [
            ai_tools(tc("delegate_task", {
                "task": "Research the latest advances in protein folding, including AlphaFold3 and newer approaches",
                "category": "research",
            })),
            ai(
                "Based on the research, the major advances in protein folding include:\n\n"
                "1. **AlphaFold3** — Now predicts protein-ligand interactions\n"
                "2. **ESMFold** — Faster inference using language model embeddings\n"
                "3. **RFdiffusion** — Generative design of novel protein structures"
            ),
        ],
        mocks={
            "prax.agent.subagent._run_subagent": (
                "Key findings on protein folding:\n"
                "- AlphaFold3 released by DeepMind, now handles protein-ligand complexes\n"
                "- ESMFold from Meta uses protein language models for faster prediction\n"
                "- RFdiffusion enables generative protein design\n"
                "Sources: Nature 2024, Science 2025"
            ),
        },
    )
    assert "AlphaFold" in response or "protein" in response.lower()
    assert llm.call_count == 2


def test_delegate_browser(run_e2e):
    """Agent delegates a browser task to the browser spoke."""
    response, llm = run_e2e(
        "Go to example.com and take a screenshot",
        [
            ai_tools(tc("delegate_browser", {
                "task": "Navigate to https://example.com and take a screenshot of the page",
            })),
            ai("I've navigated to example.com and captured a screenshot. The page shows a simple HTML document with a heading."),
        ],
        mocks={
            "prax.agent.spokes._runner.run_spoke": (
                "Navigated to https://example.com. The page shows 'Example Domain' "
                "heading with a paragraph explaining it's for illustrative examples. "
                "Screenshot saved."
            ),
        },
    )
    assert "example.com" in response.lower() or "screenshot" in response.lower()
    assert llm.call_count == 2


def test_delegate_content_editor(run_e2e):
    """Agent delegates blog post creation to the content editor spoke."""
    response, llm = run_e2e(
        "Write a blog post about quantum error correction",
        [
            ai_tools(tc("delegate_content_editor", {
                "topic": "Quantum Error Correction: Current State and Future Directions",
                "notes": "Focus on practical applications, target audience is senior engineers",
                "tags": "quantum,computing,engineering",
            })),
            ai(
                "I've published a blog post on quantum error correction. "
                "The content editor ran through research, writing, and review phases. "
                "You can read it at the published URL."
            ),
        ],
        mocks={
            "prax.agent.spokes.content.agent.run_content_pipeline": (
                "Blog post published and approved after 1 review pass(es).\n\n"
                "**Quantum Error Correction: Current State and Future Directions**\n"
                "https://notes.example.com/quantum-error-correction/\n\n"
                "Reviewer verdict: APPROVED\n\nExcellent technical depth."
            ),
        },
    )
    assert "quantum" in response.lower() or "blog" in response.lower()
    assert llm.call_count == 2


def test_delegate_parallel(run_e2e):
    """Agent runs multiple research tasks in parallel."""
    response, llm = run_e2e(
        "Compare React and Vue.js — research both frameworks",
        [
            ai_tools(tc("delegate_parallel", {
                "tasks": [
                    {"task": "Research React.js latest features and ecosystem in 2026", "category": "research"},
                    {"task": "Research Vue.js latest features and ecosystem in 2026", "category": "research"},
                ],
            })),
            ai(
                "Here's a comparison of React and Vue.js:\n\n"
                "**React**: Server Components are now mature, React 20 introduces...\n"
                "**Vue**: Vue 4 brings improved TypeScript support and Vapor mode...\n\n"
                "Both frameworks are excellent choices with different trade-offs."
            ),
        ],
        mocks={
            "prax.agent.subagent._run_subagent": (
                "Framework research complete. "
                "React 20 features: Server Components GA, improved streaming. "
                "Vue 4 features: Vapor mode, better TS integration."
            ),
        },
    )
    assert "React" in response or "Vue" in response
    assert llm.call_count == 2
