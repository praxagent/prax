"""Concurrent board mutations must not lose data.

Reported from a live Discord session: an agent turn emitted 12 parallel
`library_task_add` calls, every one returned "Added task tsk-…", and the board
persisted **2 tasks**. The user asked "Why are there only two todos when there
are 18 suggestions!!!!???!!" — and the agent had not lied; every tool call
truthfully reported the success it observed. The storage layer lost the writes.

Cause: each mutator did read-whole-file → modify → write-whole-file with no
mutual exclusion, so concurrent writers clobbered each other (last write wins).
These tests fail without the per-board lock.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from prax.services import library_service, library_tasks


@pytest.fixture
def project(tmp_path, monkeypatch):
    uid = "usr_conc"
    ws = tmp_path / uid
    ws.mkdir()
    monkeypatch.setattr(library_service, "workspace_root", lambda _uid: str(ws))
    monkeypatch.setattr(library_tasks, "workspace_root", lambda _uid: str(ws),
                        raising=False)
    library_service.create_space(uid, "Board")
    return uid, "board"


def test_parallel_creates_all_persist(project):
    """The reported bug: N concurrent adds, N tasks on the board."""
    uid, proj = project
    n = 18
    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(
            lambda i: library_tasks.create_task(uid, proj, title=f"Task {i}"),
            range(n),
        ))

    assert all(r["status"] == "created" for r in results)
    persisted = library_tasks.list_tasks(uid, proj)
    assert len(persisted) == n, (
        f"{n} creates reported success but {len(persisted)} persisted — "
        "writes are being lost"
    )
    # Every reported id must actually be on the board: a tool that says
    # "Added task tsk-X" and leaves no tsk-X is lying to the agent.
    reported = {r["task"]["id"] for r in results}
    assert reported == {t["id"] for t in persisted}


def test_parallel_creates_have_unique_ids(project):
    uid, proj = project
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(
            lambda i: library_tasks.create_task(uid, proj, title=f"T{i}"),
            range(12),
        ))
    ids = [r["task"]["id"] for r in results]
    assert len(set(ids)) == len(ids)


def test_parallel_mixed_mutations_do_not_clobber(project):
    """Creates racing against moves and comments on existing tasks."""
    uid, proj = project
    seed = [library_tasks.create_task(uid, proj, title=f"seed {i}")["task"]["id"]
            for i in range(5)]

    def work(i):
        if i % 3 == 0:
            library_tasks.create_task(uid, proj, title=f"new {i}")
        elif i % 3 == 1:
            library_tasks.move_task(uid, proj, seed[i % len(seed)], "doing")
        else:
            library_tasks.add_comment(uid, proj, seed[i % len(seed)],
                                      f"note {i}", actor="human")

    with ThreadPoolExecutor(max_workers=9) as pool:
        list(pool.map(work, range(9)))

    tasks = library_tasks.list_tasks(uid, proj)
    # 5 seeds survive, plus the 3 creates (i = 0, 3, 6).
    assert len(tasks) == 8
    assert all(t["id"] for t in tasks)


def test_write_is_atomic_no_partial_file(project):
    """A reader must never observe a truncated board."""
    uid, proj = project
    for i in range(20):
        library_tasks.create_task(uid, proj, title=f"t{i}")

    errors = []

    def reader():
        for _ in range(60):
            try:
                library_tasks.list_tasks(uid, proj)
            except Exception as exc:  # noqa: BLE001 - recording, not handling
                errors.append(exc)

    def writer():
        for i in range(30):
            library_tasks.create_task(uid, proj, title=f"w{i}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda f: f(), [reader, writer, reader, writer]))

    assert not errors, f"readers saw a corrupt board: {errors[:3]}"


def test_no_temp_files_left_behind(project):
    """The atomic write must not litter .tasks.yaml.tmp* files."""
    uid, proj = project
    for i in range(5):
        library_tasks.create_task(uid, proj, title=f"t{i}")
    proj_dir = library_tasks._project_dir(uid, proj)
    assert not list(proj_dir.glob(".*tmp*"))
