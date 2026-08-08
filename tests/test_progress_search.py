"""The complete session record must be reachable without knowing the date.

`.progress/` detail files are the COMPLETE record; the progress summary is a
lossy abstraction over them. Until now they were addressable only by date, so
the record was reachable only by someone who already knew when the thing
happened — precisely the case in which you do not need to search.

This is the "keep the complete log and grep it with code" half of
docs/research/prolong-programmatic-memory.md: compact the CONTEXT, never the
RECORD.

Deterministic and keyless by design — no model, no embeddings — so it works in
a lite deployment and cannot hallucinate a hit.
"""

import pytest

from prax.services import progress_service


@pytest.fixture()
def space(tmp_path, monkeypatch):
    """A space with three session detail files."""
    monkeypatch.setattr(progress_service, "_space_exists", lambda u, s: True)
    details = tmp_path / "spaces" / "acme" / ".progress"
    details.mkdir(parents=True)
    (details / "2026-08-01-a1b2.md").write_text(
        "# Session\n- Swapped the coolant pump seal on the press\n"
        "- Ordered two spare gaskets\n", encoding="utf-8")
    (details / "2026-08-03-c3d4.md").write_text(
        "# Session\n- Traced the vibration to a loose mount\n"
        "- Coolant levels normal\n", encoding="utf-8")
    (details / "2026-08-05-e5f6.md").write_text(
        "# Session\n- Rebuilt the gearbox\n", encoding="utf-8")
    monkeypatch.setattr(progress_service, "_detail_dir", lambda u, s: details)
    return details


def search(query, **kw):
    return progress_service.search_session_details("u1", "acme", query, **kw)


class TestFindsWithoutKnowingTheDate:

    def test_finds_a_line_by_keyword(self, space):
        out = search("gearbox")
        assert "Rebuilt the gearbox" in out

    def test_all_terms_must_appear_on_one_line(self, space):
        """AND semantics: 'coolant seal' must not match a line containing only
        one of them, or the search silently becomes OR and floods."""
        out = search("coolant seal")
        assert "coolant pump seal" in out.lower()
        assert "Coolant levels normal" not in out

    def test_case_insensitive(self, space):
        assert "gearbox" in search("GEARBOX").lower()

    def test_newest_sessions_first(self, space):
        """A cap spent on the oldest sessions is a cap wasted."""
        out = search("the")
        first = out.index("e5f6")
        assert first < out.index("a1b2")


class TestDereferenceInvariant:
    def test_every_hit_carries_its_session_ref(self, space):
        """A hit without its {date}-{short_id} ref reintroduces, from the search
        side, exactly the defect _preserve_refs exists to prevent: an
        abstraction in context that cannot be traced to its evidence."""
        out = search("coolant")
        assert "[2026-08-01-a1b2]" in out or "[2026-08-03-c3d4]" in out

    def test_points_at_the_follow_up_tool(self, space):
        assert "progress_detail" in search("gearbox")


class TestHonestEmptyResult:
    def test_no_match_says_the_record_is_complete(self, space):
        """The record is complete, so a miss is evidence of ABSENCE, not a
        retrieval failure — and the answer must say which it is."""
        out = search("helicopter")
        assert "No session detail" in out
        assert "absence" in out.lower()

    def test_empty_query_is_refused(self, space):
        assert "at least one search term" in search("   ")

    def test_missing_space_is_reported(self, monkeypatch):
        monkeypatch.setattr(progress_service, "_space_exists", lambda u, s: False)
        assert "does not exist" in search("anything")

    def test_no_details_yet(self, tmp_path, monkeypatch):
        monkeypatch.setattr(progress_service, "_space_exists", lambda u, s: True)
        monkeypatch.setattr(progress_service, "_detail_dir",
                            lambda u, s: tmp_path / "nope")
        assert "No session details recorded" in search("anything")


class TestBounded:
    def test_results_are_capped_and_say_so(self, space):
        out = search("session", limit=2)
        assert out.count("- [") <= 2
        assert "capped" in out

    def test_long_lines_are_truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(progress_service, "_space_exists", lambda u, s: True)
        d = tmp_path / ".progress"
        d.mkdir()
        (d / "2026-08-01-aaaa.md").write_text("- " + ("x" * 5000) + " needle\n",
                                              encoding="utf-8")
        monkeypatch.setattr(progress_service, "_detail_dir", lambda u, s: d)
        out = search("needle", context_chars=100)
        assert len(out) < 1000, "an unbounded snippet would defeat the point"


def test_classified_as_a_private_data_reader():
    """progress_search reads the same files as progress_detail. A reader
    classified differently from the store it reads is a provenance hole — the
    2026-08-07 laundering defect, from the other end."""
    from prax.agent.trifecta import _PRIVATE_NAMES

    assert "progress_search" in _PRIVATE_NAMES
    assert "progress_detail" in _PRIVATE_NAMES
