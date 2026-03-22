"""Tests for sandbox_service — all Docker and HTTP interactions are mocked."""
import importlib
import os

import pytest


class FakeContainer:
    def __init__(self, name="sandbox-test"):
        self.id = "container-abc123"
        self.name = name
        self.stopped = False
        self.removed = False

    def stop(self, timeout=5):
        self.stopped = True

    def remove(self, force=False):
        self.removed = True


class FakeContainers:
    def __init__(self):
        self.runs = []
        self._containers = {}

    def run(self, image, **kwargs):
        c = FakeContainer(kwargs.get("name", "sandbox-test"))
        self.runs.append({"image": image, **kwargs})
        self._containers[c.id] = c
        return c

    def get(self, container_id):
        return self._containers.get(container_id, FakeContainer())

    def list(self, all=False, filters=None):
        return list(self._containers.values())


class FakeDockerClient:
    def __init__(self):
        self.containers = FakeContainers()


@pytest.fixture()
def sandbox_mod(monkeypatch, tmp_path):
    """Reload sandbox_service with mocked Docker and settings."""
    # Set workspace dir to tmp
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))

    module = importlib.reload(importlib.import_module("prax.services.sandbox_service"))

    # Reset module state
    module._sessions.clear()
    module._user_sessions.clear()
    module._allocated_ports.clear()
    module._port_counter = 0

    fake_client = FakeDockerClient()
    monkeypatch.setattr(module, "_docker_client", None)
    monkeypatch.setattr(module, "_get_docker_client", lambda: fake_client)
    module._fake_client = fake_client  # expose for assertions

    # Mock settings
    import prax.settings as settings_mod
    importlib.reload(settings_mod)
    # Force ephemeral mode so the test exercises the container-creation path,
    # even when running inside Docker where running_in_docker=True.
    monkeypatch.setattr(module.settings, "running_in_docker", False)
    monkeypatch.setattr(module.settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(module.settings, "anthropic_key", "sk-ant-test")
    monkeypatch.setattr(module.settings, "openai_key", "sk-openai-test")
    monkeypatch.setattr(module.settings, "sandbox_image", "test-sandbox:latest")
    monkeypatch.setattr(module.settings, "sandbox_timeout", 60)
    monkeypatch.setattr(module.settings, "sandbox_max_concurrent", 3)
    monkeypatch.setattr(module.settings, "sandbox_default_model", "anthropic/claude-test")
    monkeypatch.setattr(module.settings, "sandbox_mem_limit", "512m")
    monkeypatch.setattr(module.settings, "sandbox_cpu_limit", 1_000_000_000)

    return module


@pytest.fixture()
def mock_opencode(sandbox_mod, monkeypatch):
    """Mock all OpenCode HTTP API calls."""
    monkeypatch.setattr(sandbox_mod, "_wait_for_ready", lambda s, timeout=30: True)
    monkeypatch.setattr(
        sandbox_mod, "_create_opencode_session",
        lambda s, task: "oc-session-001",
    )
    monkeypatch.setattr(
        sandbox_mod, "_send_opencode_message",
        lambda s, msg, model=None: {"content": "Done!", "model": model},
    )
    monkeypatch.setattr(
        sandbox_mod, "_get_opencode_session",
        lambda s: {"status": "active", "messages": []},
    )
    monkeypatch.setattr(
        sandbox_mod, "_export_opencode_session",
        lambda s: {"messages": [{"role": "assistant", "content": "built it"}]},
    )
    return sandbox_mod


class TestStartSession:
    def test_creates_container(self, mock_opencode):
        mod = mock_opencode
        result = mod.start_session("+10000000000", "Build a calculator")

        assert result["status"] == "running"
        assert "session_id" in result
        assert result["model"] == "anthropic/claude-test"
        assert len(mod._fake_client.containers.runs) == 1
        run = mod._fake_client.containers.runs[0]
        assert run["image"] == "test-sandbox:latest"
        assert "ANTHROPIC_API_KEY" in run["environment"]

    def test_rejects_second_session(self, mock_opencode):
        mod = mock_opencode
        mod.start_session("+10000000000", "Task 1")
        result = mod.start_session("+10000000000", "Task 2")
        assert "error" in result
        assert "already have" in result["error"]

    def test_different_users_can_have_sessions(self, mock_opencode):
        mod = mock_opencode
        r1 = mod.start_session("+10000000000", "Task 1")
        r2 = mod.start_session("+10000000001", "Task 2")
        assert r1["status"] == "running"
        assert r2["status"] == "running"

    def test_custom_model(self, mock_opencode):
        mod = mock_opencode
        result = mod.start_session("+10000000000", "Task", model="openai/gpt-5")
        assert result["model"] == "openai/gpt-5"

    def test_max_concurrent_enforcement(self, mock_opencode):
        mod = mock_opencode
        for i in range(3):
            mod.start_session(f"+1000000000{i}", f"Task {i}")
        result = mod.start_session("+10000000003", "Task 3")
        assert "error" in result
        assert "Maximum" in result["error"]

    def test_port_allocation(self, mock_opencode):
        mod = mock_opencode
        mod.start_session("+10000000000", "Task 1")

        # Finish first session, start another — port should be released
        mod.abort_session("+10000000000")
        r2 = mod.start_session("+10000000000", "Task 2")
        s2 = mod._sessions[r2["session_id"]]

        # Different ports while both alive not tested (first is aborted)
        assert s2.host_port is not None

    def test_container_env_has_api_keys(self, mock_opencode):
        mod = mock_opencode
        env = mod._build_container_env()
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-test"
        assert env["OPENAI_API_KEY"] == "sk-openai-test"


class TestSendMessage:
    def test_sends_message(self, mock_opencode):
        mod = mock_opencode
        mod.start_session("+10000000000", "Task")
        result = mod.send_message("+10000000000", "Add error handling")
        assert "response" in result
        assert result["response"]["content"] == "Done!"

    def test_model_override(self, mock_opencode):
        mod = mock_opencode
        mod.start_session("+10000000000", "Task")
        result = mod.send_message("+10000000000", "Try again", model="openai/gpt-5")
        assert result["model"] == "openai/gpt-5"

    def test_no_active_session(self, mock_opencode):
        mod = mock_opencode
        result = mod.send_message("+10000000000", "Hello")
        assert "error" in result

    def test_round_limit_enforced(self, mock_opencode):
        mod = mock_opencode
        # Set max_rounds low for testing
        mod.settings.sandbox_max_rounds = 3

        mod.start_session("+10000000000", "Task")
        for i in range(3):
            result = mod.send_message("+10000000000", f"Message {i}")
            assert "response" in result
            assert result["rounds_used"] == i + 1

        # 4th message should be blocked
        result = mod.send_message("+10000000000", "One more")
        assert "error" in result
        assert "maximum" in result["error"].lower()

    def test_rounds_remaining_in_response(self, mock_opencode):
        mod = mock_opencode
        mod.start_session("+10000000000", "Task")
        result = mod.send_message("+10000000000", "First message")
        assert "rounds_remaining" in result
        assert result["rounds_remaining"] == mod.settings.sandbox_max_rounds - 1


class TestReviewSession:
    def test_review_returns_status(self, mock_opencode, tmp_path):
        mod = mock_opencode
        r = mod.start_session("+10000000000", "Task")

        # Create a file in the session workspace
        session_dir = os.path.join(str(tmp_path), "10000000000", "active", "sessions", r["session_id"])
        os.makedirs(session_dir, exist_ok=True)
        with open(os.path.join(session_dir, "main.py"), "w") as f:
            f.write("print('hello')")

        result = mod.review_session("+10000000000")
        assert result["status"] == "running"
        assert "main.py" in result["files"]
        assert result["elapsed_seconds"] >= 0

    def test_no_session(self, mock_opencode):
        result = mock_opencode.review_session("+10000000000")
        assert "error" in result


class TestFinishSession:
    def test_archives_and_cleans_up(self, mock_opencode, tmp_path):
        mod = mock_opencode
        r = mod.start_session("+10000000000", "Task")
        session_id = r["session_id"]

        # Create a file in the session workspace
        session_dir = os.path.join(str(tmp_path), "10000000000", "active", "sessions", session_id)
        os.makedirs(session_dir, exist_ok=True)
        with open(os.path.join(session_dir, "main.py"), "w") as f:
            f.write("print('hello')")

        result = mod.finish_session("+10000000000", summary="Built a calculator")
        assert result["status"] == "finished"
        assert session_id not in mod._sessions
        assert "+10000000000" not in mod._user_sessions

    def test_no_session(self, mock_opencode):
        result = mock_opencode.finish_session("+10000000000")
        assert "error" in result


class TestAbortSession:
    def test_aborts_and_cleans_up(self, mock_opencode):
        mod = mock_opencode
        r = mod.start_session("+10000000000", "Task")
        session_id = r["session_id"]

        result = mod.abort_session("+10000000000")
        assert result["status"] == "aborted"
        assert session_id not in mod._sessions
        assert "+10000000000" not in mod._user_sessions

    def test_no_session(self, mock_opencode):
        result = mock_opencode.abort_session("+10000000000")
        assert "error" in result


class TestSearchSolutions:
    def test_finds_matching_solutions(self, mock_opencode, tmp_path):
        mod = mock_opencode
        # Create a fake archived solution
        solution_dir = os.path.join(str(tmp_path), "10000000000", "archive", "code", "abc123")
        os.makedirs(solution_dir, exist_ok=True)
        with open(os.path.join(solution_dir, "SOLUTION.md"), "w") as f:
            f.write("## Solution: abc123\nBuilt a beamer presentation from PDF\n")

        results = mod.search_solutions("+10000000000", "beamer")
        assert len(results) >= 1
        assert "beamer" in results[0]["snippet"].lower()

    def test_no_results(self, mock_opencode):
        results = mock_opencode.search_solutions("+10000000000", "nonexistent")
        assert results == []


class TestExecuteSolution:
    def test_starts_new_session_from_archive(self, mock_opencode, tmp_path):
        mod = mock_opencode
        solution_dir = os.path.join(str(tmp_path), "10000000000", "archive", "code", "abc123")
        os.makedirs(solution_dir, exist_ok=True)
        with open(os.path.join(solution_dir, "SOLUTION.md"), "w") as f:
            f.write("## Solution\nRun: python main.py\n")

        result = mod.execute_solution("+10000000000", "abc123")
        assert result["status"] == "running"

    def test_not_found(self, mock_opencode):
        result = mock_opencode.execute_solution("+10000000000", "nonexistent")
        assert "error" in result


class TestCleanupStale:
    def test_removes_orphaned_containers(self, mock_opencode):
        mod = mock_opencode
        # Start and "crash" (don't clean up properly)
        mod.start_session("+10000000000", "Task")
        # Simulate cleanup finding the container
        count = mod.cleanup_stale_sessions()
        assert count >= 1


class TestOpenCodeConfig:
    def test_build_config_anthropic(self, mock_opencode):
        mod = mock_opencode
        config = mod._build_opencode_config("anthropic/claude-sonnet-4-5")
        assert config["model"] == "anthropic/claude-sonnet-4-5"
        assert "anthropic" in config["provider"]
        assert "openai" in config["provider"]

    def test_build_config_openai(self, mock_opencode):
        mod = mock_opencode
        config = mod._build_opencode_config("openai/gpt-5")
        assert config["model"] == "openai/gpt-5"
