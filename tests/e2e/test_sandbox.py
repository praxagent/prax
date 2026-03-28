"""E2E tests for sandbox code-execution workflows."""
from tests.e2e.conftest import ai, ai_tools, tc


def test_sandbox_start_message_finish(run_e2e):
    """Full sandbox lifecycle: start → message → finish."""
    response, llm = run_e2e(
        "Write a Python script to process CSV data",
        [
            # Step 1: Agent starts sandbox
            ai_tools(tc("sandbox_start", {
                "task_description": "Write a Python script to process CSV data",
            })),
            # Step 2: Agent sends a follow-up message
            ai_tools(tc("sandbox_message", {
                "message": "Create csv_processor.py that reads a CSV, filters rows where amount > 100, and writes the result",
            })),
            # Step 3: Agent finishes the session
            ai_tools(tc("sandbox_finish", {
                "summary": "Created csv_processor.py with filtering and output functionality",
            })),
            # Step 4: Final response
            ai(
                "I've created a CSV processor script in the sandbox. It reads a CSV file, "
                "filters rows where the amount exceeds 100, and writes the filtered results "
                "to a new file. The solution has been archived to your workspace."
            ),
        ],
        mocks={
            "prax.services.sandbox_service.start_session": {
                "session_id": "abc12345-1234-1234-1234-123456789abc",
                "status": "running",
                "model": "anthropic/claude-sonnet-4-5",
            },
            "prax.services.sandbox_service.send_message": {
                "session_id": "abc12345-1234-1234-1234-123456789abc",
                "model": "anthropic/claude-sonnet-4-5",
                "response": {"response": "Done! Created csv_processor.py with CSV filtering logic."},
                "rounds_used": 1,
                "rounds_remaining": 9,
            },
            "prax.services.sandbox_service.finish_session": {
                "session_id": "abc12345-1234-1234-1234-123456789abc",
                "status": "finished",
                "archived_path": "/workspaces/10000000000/archive/code/abc12345",
            },
        },
    )
    assert "CSV" in response or "csv" in response.lower()
    assert llm.call_count == 4


def test_sandbox_review(run_e2e):
    """Agent checks sandbox progress before sending more messages."""
    response, llm = run_e2e(
        "How is my sandbox session going?",
        [
            # Agent reviews the session
            ai_tools(tc("sandbox_review", {})),
            ai("Your sandbox session is running with 2 out of 10 rounds used. No files have been created yet."),
        ],
        mocks={
            "prax.services.sandbox_service.review_session": {
                "session_id": "abc12345-1234-1234-1234-123456789abc",
                "status": "running",
                "model": "anthropic/claude-sonnet-4-5",
                "elapsed_seconds": 120,
                "timeout_seconds": 1800,
                "rounds_used": 2,
                "rounds_remaining": 8,
                "files": [],
                "opencode_state": {},
            },
        },
    )
    assert "running" in response.lower() or "2" in response
    assert llm.call_count == 2


def test_sandbox_abort(run_e2e):
    """Agent aborts a stuck sandbox session."""
    response, llm = run_e2e(
        "The sandbox seems stuck, please abort it",
        [
            ai_tools(tc("sandbox_abort", {})),
            ai("I've aborted the sandbox session. It ran for about 5 minutes and used 3 rounds. Would you like to start a new session?"),
        ],
        mocks={
            "prax.services.sandbox_service.abort_session": {
                "session_id": "abc12345-1234-1234-1234-123456789abc",
                "status": "aborted",
                "elapsed_seconds": 300,
                "rounds_used": 3,
            },
        },
    )
    assert "abort" in response.lower()
    assert llm.call_count == 2


def test_sandbox_with_model_switch(run_e2e):
    """Agent switches models mid-session for better results."""
    response, llm = run_e2e(
        "The current model isn't working well, try Claude instead",
        [
            ai_tools(tc("sandbox_message", {
                "message": "Try a different approach using pandas",
                "model": "anthropic/claude-sonnet-4-5",
            })),
            ai("I've switched to Claude and asked it to try a pandas-based approach. The response looks more promising."),
        ],
        mocks={
            "prax.services.sandbox_service.send_message": {
                "session_id": "abc12345-1234-1234-1234-123456789abc",
                "model": "anthropic/claude-sonnet-4-5",
                "response": {"response": "Rewritten using pandas. Much cleaner now."},
                "rounds_used": 4,
                "rounds_remaining": 6,
            },
        },
    )
    assert "Claude" in response or "pandas" in response
    assert llm.call_count == 2


def test_sandbox_timeout_suggests_abort(run_e2e):
    """When sandbox times out, the agent gets clear guidance to abort."""
    response, llm = run_e2e(
        "Continue working on the code",
        [
            ai_tools(tc("sandbox_message", {
                "message": "Keep working on the implementation",
            })),
            ai(
                "The sandbox coding agent timed out — it appears to be stuck. "
                "I recommend aborting this session and starting fresh."
            ),
        ],
        mocks={
            "prax.services.sandbox_service.send_message": {
                "session_id": "abc12345-1234-1234-1234-123456789abc",
                "model": "anthropic/claude-sonnet-4-5",
                "response": {"error": "Sandbox timed out waiting for response (5 min)"},
                "rounds_used": 2,
                "rounds_remaining": 8,
            },
        },
    )
    assert "abort" in response.lower() or "stuck" in response.lower()
    assert llm.call_count == 2


def test_sandbox_auto_abort_on_consecutive_failures(run_e2e):
    """Sandbox auto-aborts after 3 consecutive failures instead of looping forever."""
    response, llm = run_e2e(
        "Send another message to the sandbox",
        [
            ai_tools(tc("sandbox_message", {
                "message": "Try again",
            })),
            ai(
                "The sandbox session was automatically aborted after multiple "
                "consecutive failures. Let me start a new session."
            ),
        ],
        mocks={
            "prax.services.sandbox_service.send_message": {
                "error": (
                    "Sandbox session auto-aborted after 3 consecutive failures. "
                    "The coding agent appears stuck. "
                    "Last error: Sandbox timed out waiting for response (5 min)"
                ),
                "auto_aborted": True,
            },
        },
    )
    assert "auto" in response.lower() or "abort" in response.lower() or "new session" in response.lower()
    assert llm.call_count == 2
