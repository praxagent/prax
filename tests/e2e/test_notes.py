"""E2E tests for note creation and user notes workflows."""

from tests.e2e.conftest import ai, ai_tools, tc


def test_create_note(run_e2e):
    """Agent creates a note and returns the published URL."""
    response, llm = run_e2e(
        "Create a note about our discussion on machine learning",
        [
            ai_tools(tc("note_create", {
                "title": "Machine Learning Discussion",
                "content": "# Machine Learning\n\nKey points from our discussion...",
                "tags": "ml,ai",
            })),
            ai("I've created the note **Machine Learning Discussion** and published it. You can view it at the URL above."),
        ],
        mocks={
            "prax.services.note_service.save_and_publish": {
                "title": "Machine Learning Discussion",
                "slug": "machine-learning-discussion",
                "url": "https://notes.example.com/machine-learning-discussion/",
            },
        },
    )
    assert "Machine Learning" in response
    assert llm.call_count == 2


def test_update_user_notes(run_e2e, tmp_path):
    """Agent updates user notes (preferences/memory)."""
    response, llm = run_e2e(
        "Remember that my timezone is America/Chicago and I prefer concise answers",
        [
            ai_tools(tc("user_notes_update", {
                "content": "- Timezone: America/Chicago\n- Prefers concise answers",
            })),
            ai("Got it! I've noted your timezone (America/Chicago) and preference for concise answers."),
        ],
    )
    assert "Chicago" in response or "concise" in response
    assert llm.call_count == 2


def test_workspace_save(run_e2e):
    """Agent saves a file to the workspace."""
    response, llm = run_e2e(
        "Save this Python snippet to my workspace",
        [
            ai_tools(tc("workspace_save", {
                "filename": "snippet.py",
                "content": "def hello():\n    print('Hello, world!')\n",
            })),
            ai("I've saved the Python snippet as `snippet.py` in your workspace."),
        ],
    )
    assert "snippet.py" in response
    assert llm.call_count == 2
