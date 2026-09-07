"""Library path containment — space / notebook / note slugs are ONE path component.

Review 2026-09-05: ``library_service._space_path`` joined the raw project
string, so ``delete_space(user, "..")`` resolved to ``<workspace>/library`` and
rmtree'd the entire library; ``"../../usr_other/library/spaces/x"`` reached
another user's data; ``library_tasks._tasks_path`` and
``progress_service._progress_path`` wrote a board / progress file into any
existing directory. These tests reproduce those PoCs against a scratch
workspace dir with two users side by side, so every traversal has a real
target that must survive.
"""
from __future__ import annotations

import os

import pytest

from prax.services import library_service, library_tasks, progress_service

ME = "usr_me"
OTHER = "usr_other"
CROSS = f"../../{OTHER}/library/spaces/theirs"  # from usr_me/library/spaces/


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    for uid in (ME, OTHER):
        (root / uid).mkdir(parents=True)
    monkeypatch.setattr(library_service, "workspace_root", lambda uid: str(root / uid))
    assert library_service.create_space(ME, "Mine")["status"] == "created"
    assert library_service.create_space(OTHER, "Theirs")["status"] == "created"
    assert library_service.create_notebook(ME, project="mine", name="Notes")["status"] == "created"
    assert library_service.create_note(
        ME, "Hello", "body", project="mine", notebook="notes",
    )["status"] == "created"
    return root


# --------------------------------------------------------------------------- #
# The validator
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad", [
    ".", "..", "", "a/b", "a\\b", "../x", "x/..", "-leading", ".hidden",
    " space", "x y", "ünïcode", "a" * 129, CROSS,
])
def test_require_slug_rejects_non_components(bad):
    with pytest.raises(ValueError):
        library_service._require_slug(bad)


@pytest.mark.parametrize("ok", ["a", "mine", "q2-marketing", "a.b_c-D", "1", "x" * 128])
def test_require_slug_accepts_single_components(ok):
    assert library_service._require_slug(ok) == ok


def test_path_helpers_refuse_traversal(ws):
    for fn, args in (
        (library_service._space_path, (ME, "..")),
        (library_service._space_path, (ME, CROSS)),
        (library_service._notebook_path, (ME, "mine", "..")),
        (library_service._notebook_path, (ME, "mine", "a/b")),
        (library_service._note_path, (ME, "mine", "notes", "../.notebook")),
    ):
        with pytest.raises(ValueError):
            fn(*args)


# --------------------------------------------------------------------------- #
# The review's PoCs — destructive ops
# --------------------------------------------------------------------------- #

def test_delete_space_dotdot_does_not_rmtree_the_library(ws):
    """The exact finding: project='..' resolved to <ws>/library and rmtree'd it."""
    library = ws / ME / "library"
    result = library_service.delete_space(ME, "..")
    assert "error" in result
    assert library.is_dir()
    assert (library / "spaces" / "mine" / ".space.yaml").is_file() or (
        library / "spaces" / "mine"
    ).is_dir()


def test_delete_space_cross_user_refused(ws):
    theirs = ws / OTHER / "library" / "spaces" / "theirs"
    assert theirs.is_dir()
    result = library_service.delete_space(ME, CROSS)
    assert "error" in result
    assert theirs.is_dir()


def test_delete_space_with_separator_refused(ws):
    result = library_service.delete_space(ME, "a/b")
    assert "error" in result
    assert (ws / ME / "library" / "spaces" / "mine").is_dir()


def test_delete_notebook_dotdot_does_not_rmtree_the_space(ws):
    """notebooks/.. is the space dir, which has no *.md at top level — the old
    'still has notes' guard let rmtree take the whole space."""
    space = ws / ME / "library" / "spaces" / "mine"
    result = library_service.delete_notebook(ME, "mine", "..")
    assert "error" in result
    assert space.is_dir()
    assert (space / "notebooks" / "notes").is_dir()


def test_delete_note_traversal_refused(ws):
    planted = ws / OTHER / "library" / "spaces" / "theirs" / "secret.md"
    planted.write_text("---\nauthor: human\n---\nnot yours\n", encoding="utf-8")
    result = library_service.delete_note(ME, CROSS, "..", "secret")
    assert "error" in result
    assert planted.is_file()


# --------------------------------------------------------------------------- #
# Readers / writers on the same paths
# --------------------------------------------------------------------------- #

def test_get_note_and_update_note_cross_user_refused(ws):
    planted = ws / OTHER / "library" / "spaces" / "theirs" / "secret.md"
    planted.write_text("---\nauthor: prax\n---\nnot yours\n", encoding="utf-8")
    assert library_service.get_note(ME, CROSS, "..", "secret") is None
    result = library_service.update_note(ME, CROSS, "..", "secret", content="pwned")
    assert "error" in result
    assert "not yours" in planted.read_text(encoding="utf-8")


def test_space_file_cross_user_refused(ws):
    files = ws / OTHER / "library" / "spaces" / "theirs" / "files"
    files.mkdir()
    doc = files / "doc.txt"
    doc.write_text("theirs", encoding="utf-8")
    assert library_service.get_space_file(ME, CROSS, "doc.txt") is None
    assert library_service.delete_space_file(ME, CROSS, "doc.txt") is False
    assert doc.is_file()


def test_symlink_out_of_library_refused(ws, tmp_path):
    """Belt and braces: even a well-formed slug must not follow a symlink out
    of the library root."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("---\nauthor: prax\n---\nhost file\n", encoding="utf-8")
    link = ws / ME / "library" / "spaces" / "mine" / "notebooks" / "evil"
    os.symlink(outside, link, target_is_directory=True)
    assert library_service.get_note(ME, "mine", "evil", "leak") is None
    assert "error" in library_service.delete_notebook(ME, "mine", "evil")
    assert (outside / "leak.md").is_file()


def test_well_formed_paths_still_work(ws):
    note = library_service.get_note(ME, "mine", "notes", "hello")
    assert note is not None and note["content"].strip() == "body"
    assert library_service.update_note(ME, "mine", "notes", "hello", content="edited")["status"] == "updated"
    assert library_service.delete_note(ME, "mine", "notes", "hello")["status"] == "deleted"
    assert library_service.delete_notebook(ME, "mine", "notes")["status"] == "deleted"
    assert library_service.delete_space(ME, "mine")["status"] == "deleted"


# --------------------------------------------------------------------------- #
# library_tasks and progress_service — used to write into ANY existing dir
# --------------------------------------------------------------------------- #

def test_tasks_traversal_does_not_seed_a_board_elsewhere(ws):
    """``spaces/../../usr_other`` exists, so the old _read() happily wrote a
    default .tasks.yaml into the other user's workspace root."""
    target = ws / OTHER / ".tasks.yaml"
    out = library_tasks.list_tasks(ME, f"../../{OTHER}")
    assert isinstance(out, dict) and "error" in out
    assert not target.exists()
    with pytest.raises(ValueError):
        library_tasks._tasks_path(ME, "..")


def test_tasks_well_formed_project_still_works(ws):
    created = library_tasks.create_task(ME, "mine", title="Do it")
    assert "error" not in created
    assert (ws / ME / "library" / "spaces" / "mine" / ".tasks.yaml").is_file()


def test_progress_traversal_does_not_write_elsewhere(ws):
    target = ws / OTHER / ".progress.md"
    slug = f"../../{OTHER}"
    assert "does not exist" in progress_service.append_progress(ME, slug, "pwned")
    assert not target.exists()
    assert "does not exist" in progress_service.read_progress(ME, slug)
    assert "does not exist" in progress_service.read_session_detail(ME, slug, "2026-01-01-abc")
    assert "does not exist" in progress_service.search_session_details(ME, slug, "x")
    with pytest.raises(ValueError):
        progress_service._progress_path(ME, "..")
    with pytest.raises(ValueError):
        progress_service._detail_dir(ME, "a/b")


def test_progress_well_formed_slug_still_works(ws):
    assert "Appended" in progress_service.append_progress(ME, "mine", "did a thing")
    assert "did a thing" in progress_service.read_progress(ME, "mine")


# --------------------------------------------------------------------------- #
# The flat tiers — raw / outputs / archive — and list_notebooks
# --------------------------------------------------------------------------- #
#
# These joined the caller's slug raw even after the helpers above were fixed.
# From ``<lib>/raw/`` a wiki note is ``../spaces/mine/notebooks/notes/hello``
# and another user's capture is ``../../../usr_other/library/raw/<x>``.

WIKI_NOTE_VIA_TIER = "../spaces/mine/notebooks/notes/hello"
# From <ws>/usr_me/library/spaces/ it takes THREE ``..`` to reach <ws>/ — this
# one resolves to a directory that really exists (CROSS above does not).
CROSS_SPACE = f"../../../{OTHER}/library/spaces/theirs"


def _plant(ws, uid, tier, slug, body="theirs"):
    d = ws / uid / "library" / tier
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{slug}.md"
    f.write_text(f"---\nslug: {slug}\ntitle: t\n---\n{body}\n", encoding="utf-8")
    return f


def test_promote_raw_traversal_does_not_move_a_wiki_note(ws):
    """The exact finding: ``promote_raw`` read the note through raw/, copied it
    into the target notebook, then unlinked the ORIGINAL."""
    assert library_service.create_notebook(ME, project="mine", name="Other")["status"] == "created"
    note = ws / ME / "library" / "spaces" / "mine" / "notebooks" / "notes" / "hello.md"
    assert note.is_file()
    result = library_service.promote_raw(ME, WIKI_NOTE_VIA_TIER, "mine", "other")
    assert "error" in result
    assert note.is_file()  # old code: unlinked
    other = ws / ME / "library" / "spaces" / "mine" / "notebooks" / "other"
    assert not list(other.glob("*.md"))  # old code: a copy landed here


def test_promote_raw_cross_user_refused(ws):
    """``../../../usr_other/library/raw/<x>`` read AND deleted the other user's capture."""
    slug = "20260101-000000-secret"
    planted = _plant(ws, OTHER, "raw", slug, "not yours")
    cross = f"../../../{OTHER}/library/raw/{slug}"
    assert library_service.get_raw(ME, cross) is None
    assert "error" in library_service.promote_raw(ME, cross, "mine", "notes")
    assert "error" in library_service.delete_raw(ME, cross)
    assert planted.is_file()
    assert "not yours" in planted.read_text(encoding="utf-8")
    assert not (ws / ME / "library" / "spaces" / "mine" / "notebooks" / "notes" / "secret.md").exists()


@pytest.mark.parametrize("tier,getter,deleter", [
    ("raw", library_service.get_raw, library_service.delete_raw),
    ("outputs", library_service.get_output, library_service.delete_output),
    ("archive", library_service.get_archive, library_service.delete_archive),
])
def test_flat_tier_get_and_delete_cross_user_refused(ws, tier, getter, deleter):
    slug = "20260101-000000-item"
    planted = _plant(ws, OTHER, tier, slug)
    cross = f"../../../{OTHER}/library/{tier}/{slug}"
    assert getter(ME, cross) is None
    result = deleter(ME, cross)
    assert "error" in result
    assert planted.is_file()


@pytest.mark.parametrize("getter,deleter", [
    (library_service.get_raw, library_service.delete_raw),
    (library_service.get_output, library_service.delete_output),
    (library_service.get_archive, library_service.delete_archive),
])
def test_flat_tier_cannot_reach_a_wiki_note(ws, getter, deleter):
    """Every flat tier sits beside spaces/, so one ``..`` reaches any note."""
    note = ws / ME / "library" / "spaces" / "mine" / "notebooks" / "notes" / "hello.md"
    assert getter(ME, WIKI_NOTE_VIA_TIER) is None
    assert "error" in deleter(ME, WIKI_NOTE_VIA_TIER)
    assert note.is_file()


def test_flat_tier_absolute_and_dotdot_slugs_refused(ws, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: host\n---\nhost file\n", encoding="utf-8")
    for bad in ("..", str(outside.with_suffix("")), "a/b", "%2e%2e/x"):
        assert library_service.get_raw(ME, bad) is None
        assert "error" in library_service.delete_raw(ME, bad)
        assert "error" in library_service.promote_raw(ME, bad, "mine", "notes")
    assert outside.is_file()


def test_list_notebooks_cross_user_refused(ws):
    """``list_notebooks(uid, "../../usr_other/library/spaces/theirs")`` enumerated
    the other user's notebook names and descriptions."""
    assert library_service.create_notebook(
        OTHER, project="theirs", name="Private", description="do not leak",
    )["status"] == "created"
    assert library_service.list_notebooks(ME, CROSS_SPACE) == []
    assert library_service.list_notebooks(ME, CROSS) == []
    assert library_service.list_notebooks(ME, "..") == []
    assert library_service.list_notebooks(ME, "a/b") == []
    # The other user still sees their own.
    assert [nb["slug"] for nb in library_service.list_notebooks(OTHER, "theirs")] == ["private"]


def test_flat_tiers_and_list_notebooks_still_work_with_generated_slugs(ws):
    """The slugs these tiers issue (``YYYYMMDD-HHMMSS-<slugified>``) must all
    pass the validator — nothing legitimate is refused."""
    assert [nb["slug"] for nb in library_service.list_notebooks(ME, "mine")] == ["notes"]
    assert [nb["slug"] for nb in library_service.list_notebooks(ME)] == ["notes"]

    raw = library_service.raw_capture(ME, "A Capture: with punctuation!", "raw body")["raw"]
    assert library_service.get_raw(ME, raw["slug"])["content"].strip() == "raw body"
    promoted = library_service.promote_raw(ME, raw["slug"], "mine", "notes")
    assert promoted["status"] == "promoted"
    assert not (ws / ME / "library" / "raw" / f"{raw['slug']}.md").exists()
    assert library_service.get_note(ME, "mine", "notes", promoted["note"]["slug"]) is not None

    raw2 = library_service.raw_capture(ME, "Drop me", "x")["raw"]
    assert library_service.delete_raw(ME, raw2["slug"])["status"] == "deleted"

    out = library_service.write_output(ME, "Daily brief", "brief body")["output"]
    assert library_service.get_output(ME, out["slug"])["content"].strip() == "brief body"
    assert library_service.delete_output(ME, out["slug"])["status"] == "deleted"

    arc = library_service.archive_capture(ME, "Keeper", "kept body")["archive"]
    assert library_service.get_archive(ME, arc["slug"])["content"].strip() == "kept body"
    assert library_service.delete_archive(ME, arc["slug"])["status"] == "deleted"
