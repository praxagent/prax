"""Git repos attached to a Library space.

Three properties are the reason this module exists, so they are what gets
tested: a space cannot reach outside itself, write is off until a human says
otherwise, and each repo carries its own credential rather than an account-wide
key that would reach everything.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from prax.services import space_repos as sr


@pytest.fixture
def space(tmp_path, monkeypatch):
    """A library space on disk, with keys redirected out of the way."""
    user = "u1"
    root = tmp_path / "ws" / user / "library"
    slug = "my-space"
    (root / "spaces" / slug).mkdir(parents=True)
    (root / "spaces" / slug / ".space.yaml").write_text(
        yaml.safe_dump({"slug": slug, "name": "My Space"}), encoding="utf-8")

    monkeypatch.setattr(sr, "_library_root", lambda uid: root, raising=False)
    import prax.services.library_service as ls
    monkeypatch.setattr(ls, "_library_root", lambda uid: root, raising=False)
    monkeypatch.setattr(sr, "KEY_ROOT", tmp_path / "keys", raising=False)
    return {"user": user, "slug": slug, "root": root, "tmp": tmp_path}


def _origin(tmp_path: Path) -> str:
    """A real local repo to clone, so the git paths are genuinely exercised."""
    origin = tmp_path / "origin"
    origin.mkdir()
    def run(*a):
        subprocess.run(a, cwd=origin, capture_output=True, check=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (origin / "README.md").write_text("hello\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "first commit")
    return str(origin)


# ── Isolation: a space cannot reach outside itself ───────────────────────────

@pytest.mark.parametrize("bad", [
    "../escape", "../../etc", "a/b", "/abs", "", ".", "..",
    "x" * 65, "-startswithdash",
])
def test_traversal_and_junk_names_are_refused(space, bad):
    with pytest.raises(sr.RepoError):
        sr._resolved_repo_path(space["user"], space["slug"], bad)


def test_resolved_path_stays_inside_the_space(space):
    p = sr._resolved_repo_path(space["user"], space["slug"], "ok-name")
    assert "spaces/my-space/repos" in str(p)


def test_a_symlink_cannot_smuggle_the_checkout_out(space, tmp_path):
    # The name check alone would pass here; resolve-and-compare is what catches it.
    repos = space["root"] / "spaces" / space["slug"] / "repos"
    repos.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repos / "sneaky").symlink_to(outside)
    with pytest.raises(sr.RepoError):
        sr._resolved_repo_path(space["user"], space["slug"], "sneaky")


def test_two_spaces_do_not_share_checkouts(space):
    (space["root"] / "spaces" / "other").mkdir(parents=True)
    a = sr._resolved_repo_path(space["user"], space["slug"], "repo")
    b = sr._resolved_repo_path(space["user"], "other", "repo")
    assert a != b


# ── Write is off until someone turns it on ───────────────────────────────────

def test_attach_defaults_to_read_only(space, tmp_path):
    res = sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    assert res["repo"]["write"] is False


def test_commit_and_push_are_refused_while_read_only(space, tmp_path):
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    for call in (lambda: sr.commit(space["user"], space["slug"], "demo", "msg"),
                 lambda: sr.push(space["user"], space["slug"], "demo")):
        with pytest.raises(sr.RepoError, match="read-only"):
            call()


def test_write_can_be_toggled_on_and_back_off(space, tmp_path):
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    assert sr.set_write(space["user"], space["slug"], "demo", True)["write"] is True
    path = sr._resolved_repo_path(space["user"], space["slug"], "demo")
    (path / "new.txt").write_text("change\n")
    assert "nothing to commit" not in sr.commit(
        space["user"], space["slug"], "demo", "now allowed")

    sr.set_write(space["user"], space["slug"], "demo", False)
    with pytest.raises(sr.RepoError, match="read-only"):
        sr.commit(space["user"], space["slug"], "demo", "blocked again")


def test_write_is_per_repo_not_global(space, tmp_path):
    origin = _origin(tmp_path)
    sr.attach(space["user"], space["slug"], origin, "one")
    sr.attach(space["user"], space["slug"], origin, "two")
    sr.set_write(space["user"], space["slug"], "one", True)

    (sr._resolved_repo_path(space["user"], space["slug"], "one") / "f.txt").write_text("x\n")
    sr.commit(space["user"], space["slug"], "one", "fine")
    with pytest.raises(sr.RepoError, match="read-only"):
        sr.commit(space["user"], space["slug"], "two", "still blocked")


def test_committing_nothing_is_reported_not_raised(space, tmp_path):
    # An agent that made no net change should be told, not have to catch.
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    sr.set_write(space["user"], space["slug"], "demo", True)
    assert "nothing to commit" in sr.commit(space["user"], space["slug"], "demo", "noop")


def test_the_toggle_persists_to_disk(space, tmp_path):
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    sr.set_write(space["user"], space["slug"], "demo", True)
    meta = yaml.safe_load(
        (space["root"] / "spaces" / space["slug"] / ".space.yaml").read_text())
    assert meta["repos"][0]["write"] is True


# ── Credentials: per-repo, and never inside the workspace ────────────────────

def test_each_repo_gets_its_own_key(space, tmp_path):
    origin = _origin(tmp_path)
    a = sr.attach(space["user"], space["slug"], origin, "one")["public_key"]
    b = sr.attach(space["user"], space["slug"], origin, "two")["public_key"]
    assert a and b and a != b, "a shared key would reach both repositories"


def test_private_key_lives_outside_the_workspace(space, tmp_path):
    # The workspace is a git repo that commits everything not ignored — a key
    # stored there would end up in its history.
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    key = sr._key_path(space["user"], space["slug"], "demo")
    assert key.exists()
    assert str(space["root"]) not in str(key)


def test_private_key_is_not_world_readable(space, tmp_path):
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    mode = sr._key_path(space["user"], space["slug"], "demo").stat().st_mode
    assert mode & 0o077 == 0, "private key is readable by others"


def test_git_env_pins_identities_only(space, tmp_path):
    # Without IdentitiesOnly, ssh offers every key it has and a repo could be
    # reached with a credential never meant for it.
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    env = sr._git_env(space["user"], space["slug"], "demo")
    assert "IdentitiesOnly=yes" in env["GIT_SSH_COMMAND"]
    assert str(sr._key_path(space["user"], space["slug"], "demo")) in env["GIT_SSH_COMMAND"]


def test_detach_destroys_the_key(space, tmp_path):
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    key = sr._key_path(space["user"], space["slug"], "demo")
    assert sr.detach(space["user"], space["slug"], "demo") is True
    assert not key.exists() and not key.with_suffix(".pub").exists()


# ── Ordinary behaviour ───────────────────────────────────────────────────────

def test_clone_produces_a_working_checkout(space, tmp_path):
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    path = sr._resolved_repo_path(space["user"], space["slug"], "demo")
    assert (path / "README.md").exists()


def test_status_and_log_read_the_checkout(space, tmp_path):
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    st = sr.status(space["user"], space["slug"], "demo")
    assert st["branch"] == "main" and st["write"] is False
    commits = sr.log(space["user"], space["slug"], "demo")
    assert commits and commits[0]["subject"] == "first commit"


def test_status_notices_local_edits(space, tmp_path):
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    path = sr._resolved_repo_path(space["user"], space["slug"], "demo")
    (path / "README.md").write_text("changed\n")
    assert sr.status(space["user"], space["slug"], "demo")["dirty_files"] == 1


def test_listing_reports_attachment_and_clone_state(space, tmp_path):
    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    (entry,) = sr.list_repos(space["user"], space["slug"])
    assert entry["name"] == "demo" and entry["cloned"] is True and entry["write"] is False


def test_attaching_the_same_name_twice_is_refused(space, tmp_path):
    origin = _origin(tmp_path)
    sr.attach(space["user"], space["slug"], origin, "demo")
    with pytest.raises(sr.RepoError, match="already attached"):
        sr.attach(space["user"], space["slug"], origin, "demo")


def test_operations_on_an_unattached_repo_are_refused(space):
    with pytest.raises(sr.RepoError, match="not attached"):
        sr.set_write(space["user"], space["slug"], "ghost", True)


def test_unknown_space_is_refused(space):
    with pytest.raises(sr.RepoError, match="no such space"):
        sr.list_repos(space["user"], "does-not-exist")


# ── Agent tools: the wrappers must not soften the rules ──────────────────────

def _tool_env(space, monkeypatch):
    """Point the tools' user-context at the fixture's user."""
    import prax.agent.library_tools as lt
    monkeypatch.setattr(lt, "_uid", lambda: space["user"])
    return lt


def test_tool_attach_reports_the_deploy_key_to_register(space, tmp_path, monkeypatch):
    lt = _tool_env(space, monkeypatch)
    out = lt.space_repo_add.invoke(
        {"space_slug": space["slug"], "url": _origin(tmp_path), "name": "demo"})
    assert "read-only" in out
    assert "ssh-ed25519" in out, "the user cannot register a key they were not shown"


def test_tool_attach_never_enables_write(space, tmp_path, monkeypatch):
    lt = _tool_env(space, monkeypatch)
    lt.space_repo_add.invoke(
        {"space_slug": space["slug"], "url": _origin(tmp_path), "name": "demo"})
    (entry,) = sr.list_repos(space["user"], space["slug"])
    assert entry["write"] is False


def test_tool_commit_is_refused_while_read_only(space, tmp_path, monkeypatch):
    lt = _tool_env(space, monkeypatch)
    lt.space_repo_add.invoke(
        {"space_slug": space["slug"], "url": _origin(tmp_path), "name": "demo"})
    out = lt.space_repo_commit.invoke(
        {"space_slug": space["slug"], "name": "demo", "message": "nope"})
    assert "refused" in out.lower() and "read-only" in out


def test_tool_write_toggle_warns_about_the_repo_side_setting(space, tmp_path, monkeypatch):
    # Enabling write here is necessary but not sufficient — the deploy key also
    # needs write access on the repo, and not saying so wastes the user's time.
    lt = _tool_env(space, monkeypatch)
    lt.space_repo_add.invoke(
        {"space_slug": space["slug"], "url": _origin(tmp_path), "name": "demo"})
    out = lt.space_repo_set_write.invoke(
        {"space_slug": space["slug"], "name": "demo", "enabled": True})
    assert "ENABLED" in out and "Allow write access" in out


def test_tool_listing_shows_the_write_state(space, tmp_path, monkeypatch):
    lt = _tool_env(space, monkeypatch)
    lt.space_repo_add.invoke(
        {"space_slug": space["slug"], "url": _origin(tmp_path), "name": "demo"})
    assert "read-only" in lt.space_repos_list.invoke({"space_slug": space["slug"]})
    sr.set_write(space["user"], space["slug"], "demo", True)
    assert "(write)" in lt.space_repos_list.invoke({"space_slug": space["slug"]})


def test_tool_errors_are_returned_not_raised(space, monkeypatch):
    # A tool that raises breaks the agent turn; a tool that explains lets it recover.
    lt = _tool_env(space, monkeypatch)
    out = lt.space_repo_status.invoke({"space_slug": space["slug"], "name": "ghost"})
    assert "not attached" in out


def test_commit_works_on_a_host_with_no_git_identity(space, tmp_path, monkeypatch):
    """A fresh deployment has no global user.email, and must still commit.

    This is what CI caught: the tests passed on a dev box because git found an
    identity in ~/.gitconfig, and failed everywhere that had none — which is
    every container and every newly-provisioned server. Pointing
    GIT_CONFIG_GLOBAL at an empty file reproduces that machine here.
    """
    empty = tmp_path / "no-gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))

    sr.attach(space["user"], space["slug"], _origin(tmp_path), "demo")
    sr.set_write(space["user"], space["slug"], "demo", True)
    path = sr._resolved_repo_path(space["user"], space["slug"], "demo")
    (path / "new.txt").write_text("hi")

    out = sr.commit(space["user"], space["slug"], "demo", "add a file")
    assert "nothing to commit" not in out

    log = sr._run_git(["log", "-1", "--format=%an <%ae>"], cwd=path,
                      env=sr._git_env(space["user"], space["slug"], "demo"))
    assert log == "Prax <prax@localhost>", (
        "commits should say the agent made them, not whoever owns the box")


def test_authorship_is_applied_even_when_a_call_site_passes_no_env():
    """The identity must come from _run_git, not from remembering to pass env.

    `commit` forgot, which is exactly how the no-identity bug shipped. Reading
    the env off a call with env=None is the check that it cannot recur.
    """
    import subprocess

    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    orig = subprocess.run
    subprocess.run = fake_run
    try:
        sr._run_git(["status"], cwd=Path("."))
    finally:
        subprocess.run = orig

    assert seen["GIT_AUTHOR_EMAIL"] == "prax@localhost"
    assert seen["GIT_COMMITTER_NAME"] == "Prax"
