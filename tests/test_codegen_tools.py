"""Tests for codegen_tools LangChain wrappers."""
import importlib


def test_self_improve_start(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.codegen_tools"))
    svc = importlib.import_module("prax.services.codegen_service")

    monkeypatch.setattr(
        svc, "start_branch",
        lambda name, desc="": {"status": "created", "branch": f"self-improve/{name}", "worktree_path": "/tmp/wt", "description": desc},
    )

    result = module.self_improve_start.invoke({"branch_name": "fix-bug", "description": "Fix a bug"})
    assert "self-improve/fix-bug" in result
    assert "created" in result.lower()


def test_self_improve_read(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.codegen_tools"))
    svc = importlib.import_module("prax.services.codegen_service")

    monkeypatch.setattr(
        svc, "read_file",
        lambda branch, path: {"filepath": path, "content": "print('hello')"},
    )

    result = module.self_improve_read.invoke({"branch_name": "fix-bug", "filepath": "main.py"})
    assert "hello" in result


def test_self_improve_write(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.codegen_tools"))
    svc = importlib.import_module("prax.services.codegen_service")

    monkeypatch.setattr(
        svc, "write_file",
        lambda branch, path, content: {"status": "written", "filepath": path, "size": len(content)},
    )

    result = module.self_improve_write.invoke({"branch_name": "fix-bug", "filepath": "main.py", "content": "code"})
    assert "written" in result.lower()


def test_self_improve_verify(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.codegen_tools"))
    svc = importlib.import_module("prax.services.codegen_service")

    monkeypatch.setattr(svc, "run_tests", lambda b: {"status": "passed"})
    monkeypatch.setattr(svc, "run_lint", lambda b: {"status": "clean"})
    monkeypatch.setattr(svc, "verify_startup", lambda b: {"status": "passed"})

    result = module.self_improve_verify.invoke({"branch_name": "fix-bug"})
    assert "ALL PASSED" in result


def test_self_improve_verify_failure(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.codegen_tools"))
    svc = importlib.import_module("prax.services.codegen_service")

    monkeypatch.setattr(svc, "run_tests", lambda b: {"status": "failed", "stdout": "FAIL", "stderr": ""})
    monkeypatch.setattr(svc, "run_lint", lambda b: {"status": "clean"})
    monkeypatch.setattr(svc, "verify_startup", lambda b: {"status": "passed"})

    result = module.self_improve_verify.invoke({"branch_name": "fix-bug"})
    assert "ISSUES FOUND" in result
    assert "FAIL" in result


def test_self_improve_deploy(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.codegen_tools"))
    svc = importlib.import_module("prax.services.codegen_service")

    monkeypatch.setattr(
        svc, "verify_and_deploy",
        lambda branch, msg="": {
            "status": "deployed", "branch": f"self-improve/{branch}",
            "files_changed": ["app.py"], "files_deleted": [],
            "message": "Deployed 1 file(s). Werkzeug reloader will restart the app.",
        },
    )

    result = module.self_improve_deploy.invoke({"branch_name": "fix-bug", "commit_message": "Fix bug"})
    assert "deployed" in result.lower()
    assert "app.py" in result


def test_self_improve_deploy_error(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.codegen_tools"))
    svc = importlib.import_module("prax.services.codegen_service")

    monkeypatch.setattr(
        svc, "verify_and_deploy",
        lambda branch, msg="": {
            "error": "Verification failed: tests did not pass",
            "stage": "tests",
            "details": {"status": "failed", "stdout": "2 failed", "stderr": ""},
        },
    )

    result = module.self_improve_deploy.invoke({"branch_name": "fix-bug"})
    assert "error" in result.lower()
    assert "tests" in result.lower()


def test_self_improve_submit_blocked():
    module = importlib.reload(importlib.import_module("prax.agent.codegen_tools"))

    result = module.self_improve_submit.invoke({"branch_name": "fix-bug", "title": "Fix bug", "body": "Details"})
    assert "disabled" in result.lower()
    assert "self_improve_deploy" in result


def test_self_improve_list(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.codegen_tools"))
    svc = importlib.import_module("prax.services.codegen_service")

    monkeypatch.setattr(
        svc, "list_branches",
        lambda: {"active_worktrees": [{"branch": "self-improve/test", "worktree_path": "/tmp/wt"}], "open_prs": []},
    )

    result = module.self_improve_list.invoke({})
    assert "self-improve/test" in result
