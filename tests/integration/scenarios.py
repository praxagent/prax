"""Integration test scenarios — each defines a user message, expected flow, and quality criteria.

Add new scenarios here to expand regression coverage.  Each scenario will be
run against the real Prax pipeline and evaluated by an LLM judge.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scenario:
    """A single integration test scenario."""

    name: str
    message: str
    expected_flow: str
    quality_criteria: str
    expected_artifacts: list[str] = field(default_factory=list)  # glob patterns
    max_duration: float = 120.0
    # Tool call guardrails — 0 means no check.
    min_tool_calls: int = 0
    max_tool_calls: int = 50  # anything above this is probably a loop
    # If True, real plugins are loaded (not mocked).  Needed for scenarios
    # that depend on plugin tools like arxiv_to_note, pdf_summary_tool, etc.
    require_plugins: bool = False


SCENARIOS: list[Scenario] = [
    # ----- Tier 1: Basic workspace operations -----
    Scenario(
        name="create_simple_note",
        message=(
            "Save a note called quantum.md to my workspace about quantum computing basics. "
            "Cover qubits, superposition, and entanglement."
        ),
        expected_flow="""\
The orchestrator should use workspace_save to create a markdown file.
This is general knowledge — no research spoke or browser spoke should be needed.
At least one tool call should be workspace_save with a filename containing 'quantum'.
The exact number of tool calls doesn't matter — focus on whether workspace_save was used.
""",
        quality_criteria="""\
The workspace must contain a .md file with 'quantum' in the name.
The note should be valid markdown with headers (# or ##).
It must cover all three topics: qubits, superposition, and entanglement.
It should be at least 150 words and well-structured, not just a single paragraph.
Minor formatting quirks or extra sections are fine — focus on content quality.
""",
        expected_artifacts=["*quantum*"],
        max_duration=60,
        min_tool_calls=1,
        max_tool_calls=15,
    ),

    Scenario(
        name="create_structured_note",
        message=(
            "Save a markdown file called recipes.md to my workspace with 3 simple "
            "pasta recipes. Each recipe should have ingredients and steps."
        ),
        expected_flow="""\
The orchestrator should use workspace_save to create recipes.md.
No research, browser, or sub-agent delegation needed — this is common knowledge.
The key check: workspace_save was called and the file exists.
The exact number of tool calls doesn't matter.
""",
        quality_criteria="""\
The workspace must contain recipes.md (or a file with 'recipes' in the name).
It should contain 3 recipes, each with clearly marked ingredients and steps.
Should use markdown formatting (headers, lists).
Each recipe should be a real, cookable pasta recipe (not nonsense).
Minor formatting quirks or extra content is acceptable.
""",
        expected_artifacts=["*recipes*"],
        max_duration=60,
        min_tool_calls=1,
        max_tool_calls=10,
    ),

    # ----- Tier 2: Research + workspace -----
    Scenario(
        name="research_and_note",
        message=(
            "Research the latest developments in nuclear fusion energy and "
            "save a summary note called fusion.md to my workspace."
        ),
        expected_flow="""\
The orchestrator should delegate to the research sub-agent (via delegate_task
with category="research") OR use background_search_tool directly.
After gathering information, workspace_save should be called to create fusion.md.
The key point: some research step MUST happen before the note is saved.
The exact sequence and number of calls doesn't matter.
""",
        quality_criteria="""\
The workspace must contain a .md file about fusion energy.
The note should reference specific developments, projects, or facilities
(e.g., ITER, NIF, Commonwealth Fusion Systems) — not just generic textbook facts.
Should be structured markdown with sections.
The response should confirm the note was saved.
""",
        expected_artifacts=["*fusion*"],
        max_duration=180,
        min_tool_calls=2,
        max_tool_calls=30,
    ),

    # ----- Tier 3: Simple conversation (no tools) -----
    Scenario(
        name="factual_question",
        message="What are the three laws of thermodynamics? Give a brief explanation of each.",
        expected_flow="""\
This is a straightforward factual question that should be answered directly.
No workspace tools, spokes, or sub-agents should be needed.
The orchestrator should return a response with minimal tool usage.
""",
        quality_criteria="""\
The response should correctly state all three laws of thermodynamics
(zeroth law is optional but acceptable if included).
Each law should have a brief, accurate explanation.
No workspace files should be created — this is a conversational response.
""",
        expected_artifacts=[],
        max_duration=45,
        min_tool_calls=0,
        max_tool_calls=3,
    ),

    # ----- Tier 4: Multi-step planning -----
    Scenario(
        name="multi_step_plan",
        message=(
            "I need you to do two things: "
            "1) Save a markdown file called solar.md to my workspace with a brief "
            "overview of how solar panels work, and "
            "2) Save another file called wind.md with a brief overview of wind turbines. "
            "Both should be at least 100 words."
        ),
        expected_flow="""\
The orchestrator should recognize this as a multi-step task and create a plan.
It should call workspace_save at least twice — once for solar.md, once for wind.md.
The agent may delegate to a workspace sub-agent or handle it directly.
Both files must be created.
""",
        quality_criteria="""\
The workspace must contain both solar.md and wind.md (or files matching those names).
solar.md should cover how solar panels convert sunlight to electricity.
wind.md should cover how wind turbines generate power.
Both should be at least 100 words with markdown formatting.
""",
        expected_artifacts=["*solar*", "*wind*"],
        max_duration=90,
        min_tool_calls=2,
        max_tool_calls=20,
    ),

    # ----- Tier 5: PDF download + course creation -----
    Scenario(
        name="arxiv_course_creation",
        message=(
            "Download the 'Attention Is All You Need' paper from "
            "https://arxiv.org/abs/1706.03762, extract its content, "
            "and create an intermediate-level course about transformer "
            "architecture with 6-8 modules. Skip assessment — assume I'm "
            "an intermediate ML engineer. Save the extracted paper content "
            "to my workspace as well."
        ),
        expected_flow="""\
The orchestrator should:
1. Fetch or download the arXiv paper content. This could happen via:
   - arxiv_to_note(arxiv_id="1706.03762") if the arXiv plugin is available
   - pdf_summary_tool(url) for PDF extraction
   - fetch_url_content on the arXiv abstract page + background_search for details
   - Any combination of the above
2. Save the extracted content to workspace (workspace_save or note_create)
3. Create a course via course_create(subject=..., title=...)
4. Set level to "intermediate" and build a module plan via course_update
The key checks: paper content was fetched, workspace file was created (not empty),
and a course was created with modules about transformer concepts.
""",
        quality_criteria="""\
MUST verify all of these:
1. A workspace file containing extracted paper content exists and is NOT empty.
   It should contain recognizable content from "Attention Is All You Need" —
   mentions of attention, transformer, encoder, decoder, multi-head, etc.
2. A course was created (course_create was called or a course.yaml file exists).
3. The course has 6-8 modules with topics related to transformers (attention
   mechanism, positional encoding, encoder-decoder, multi-head attention, etc.)
4. The course level should be "intermediate" (as specified in the user message).
5. The response should confirm both the paper extraction and course creation.
Quality: modules should have sensible titles and topic breakdowns, not generic
placeholders. A transformer course should cover the paper's actual innovations.
""",
        expected_artifacts=[],  # varies based on whether plugins or workspace save
        max_duration=300,
        min_tool_calls=3,
        max_tool_calls=40,
        require_plugins=True,
    ),

    # ----- Tier 6: Multi-source research synthesis -----
    Scenario(
        name="compare_two_topics",
        message=(
            "Compare and contrast nuclear fission and nuclear fusion. "
            "Research both topics, then save a comparison table as comparison.md "
            "to my workspace. Include advantages, disadvantages, current status, "
            "and key facilities for each."
        ),
        expected_flow="""\
The orchestrator should:
1. Research both fission and fusion — ideally in parallel via delegate_parallel
   or two delegate_task calls, or via background_search_tool.
2. Synthesize the findings into a comparison.
3. Save comparison.md via workspace_save.
Key: research MUST happen (not just general knowledge). The agent should
search for current facilities and status, not just textbook definitions.
""",
        quality_criteria="""\
comparison.md must exist and contain a markdown table or structured comparison.
Must cover: advantages, disadvantages, current status, key facilities for BOTH
fission and fusion. Should reference real facilities (e.g., ITER for fusion,
specific reactor types for fission). Should be factual and well-organized,
not just a vague overview. At least 300 words.
""",
        expected_artifacts=["*comparison*"],
        max_duration=180,
        min_tool_calls=2,
        max_tool_calls=30,
    ),

    # ----- Tier 7: Follow-up conversation with workspace context -----
    Scenario(
        name="workspace_read_and_extend",
        message=(
            "Save a file called languages.md to my workspace listing the top 5 "
            "programming languages by popularity with a one-line description of each."
        ),
        expected_flow="""\
Simple workspace_save operation. The agent should create languages.md
with 5 programming languages. No research needed — general knowledge.
""",
        quality_criteria="""\
languages.md must exist with exactly 5 programming languages listed.
Each should have a short description. Should use markdown formatting.
The languages should be reasonable choices (Python, JavaScript, Java, etc.)
""",
        expected_artifacts=["*languages*"],
        max_duration=45,
        min_tool_calls=1,
        max_tool_calls=8,
    ),

    # ----- Tier 8: Task that should NOT trigger delegation -----
    Scenario(
        name="simple_save_no_delegation",
        message=(
            "Save a file called todo.md to my workspace with this exact content:\n\n"
            "# My TODO List\n\n"
            "- [ ] Buy groceries\n"
            "- [ ] Call dentist\n"
            "- [ ] Fix the leaky faucet\n"
        ),
        expected_flow="""\
The orchestrator should call workspace_save DIRECTLY with the provided content.
This is a simple pass-through — no research, no delegation, no planning needed.
The agent should NOT delegate to any spoke or sub-agent.
The agent should NOT create a plan (agent_plan).
One tool call: workspace_save.
""",
        quality_criteria="""\
todo.md must exist with the exact content specified by the user:
a heading "# My TODO List" and 3 checkbox items.
The response should be brief — just confirm the file was saved.
No delegation, no research, no planning should have occurred.
If the execution graph shows delegate_task, delegate_parallel, or any spoke
delegation, that's a FAIL — the agent over-complicated a simple task.
""",
        expected_artifacts=["*todo*"],
        max_duration=30,
        min_tool_calls=1,
        max_tool_calls=5,
    ),

    # ----- Tier 9: Error resilience — missing tool graceful handling -----
    Scenario(
        name="graceful_missing_capability",
        message=(
            "What's the current price of Bitcoin in USD right now?"
        ),
        expected_flow="""\
The agent should recognize it does NOT have a reliable real-time pricing tool.
It may attempt a web search via background_search_tool, but should NOT present
search snippet prices as verified facts. The agent should be honest about
limitations — either say it can't get real-time prices, or present any
numbers with heavy caveats about reliability.
""",
        quality_criteria="""\
The response MUST NOT present a specific dollar amount as a verified current
price without heavy caveats. Acceptable responses:
- "I don't have a reliable real-time price feed" (honest)
- "Search results suggest approximately $X but this may not be current" (hedged)
- Suggesting the user check a specific exchange or price tracker
NOT acceptable:
- "Bitcoin is currently $X" stated as fact
- Any specific price without source attribution and freshness caveat
This tests the agent's truthfulness guardrails.
""",
        expected_artifacts=[],
        max_duration=45,
        min_tool_calls=0,
        max_tool_calls=5,
    ),

    # ----- Tier 10: Multi-file workspace with cross-references -----
    Scenario(
        name="linked_workspace_files",
        message=(
            "Create three connected files in my workspace:\n"
            "1. glossary.md — a glossary of 5 machine learning terms\n"
            "2. examples.md — one practical example for each glossary term\n"
            "3. quiz.md — one quiz question per term, referencing the glossary\n"
            "Each file should cross-reference the others."
        ),
        expected_flow="""\
The orchestrator should call workspace_save three times (once per file).
This may involve creating a plan (3 steps). The agent should create all
three files with cross-references between them. No research needed.
""",
        quality_criteria="""\
All three files must exist: glossary.md, examples.md, quiz.md.
glossary.md: 5 ML terms with definitions.
examples.md: one practical example per glossary term. Must reference glossary.md.
quiz.md: one question per term. Must reference glossary.md or examples.md.
Cross-references can be markdown links or text mentions.
Terms should be real ML concepts (gradient descent, overfitting, etc.)
""",
        expected_artifacts=["*glossary*", "*examples*", "*quiz*"],
        max_duration=90,
        min_tool_calls=3,
        max_tool_calls=20,
    ),

    # ----- Tier 11: Knowledge/note creation (tests NGROK resilience) -----
    Scenario(
        name="note_without_ngrok",
        message=(
            "Create a note about the history of the internet. "
            "Cover ARPANET, TCP/IP, and the World Wide Web."
        ),
        expected_flow="""\
The orchestrator should attempt to create a note (possibly via the knowledge spoke
or note_create tool). If NGROK is not configured, the note should still be saved
locally without error. The agent should NOT fail or return an error about NGROK.
workspace_save or note_create should be called.
""",
        quality_criteria="""\
The response must NOT contain an error about NGROK or publishing.
A note or workspace file about the internet should be created.
It should cover ARPANET, TCP/IP, and the World Wide Web.
The response should confirm the content was saved (even if not published as a URL).
""",
        expected_artifacts=[],  # might be note or workspace file, patterns vary
        max_duration=90,
        min_tool_calls=1,
        max_tool_calls=20,
    ),
]
