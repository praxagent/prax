"""Every trace line the pointer passes must reach the extractor.

Before 2026-09 a consolidation batch was "the next 50 lines" — up to ~250 KB,
since a trace line holds up to 5 000 characters — while the extractor and the
daily summary read `text[:4000]`.  The pointer then advanced past the whole
batch.  Anything beyond the first 4 000 characters was never extracted and
never would be.

The batch is now bounded by `EXTRACTION_CHAR_BUDGET`, the same cap the
extractor enforces, and the pointer advances only past what was batched.  A
single line longer than the budget cannot be split without changing what a
"line" means, so it is sent truncated and the dropped tail is reported as
`bytes_skipped` instead of vanishing.

Keyless: the LLM call is patched; batching and the pointer are real.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from prax.services.memory import consolidation
from prax.services.memory.consolidation import EXTRACTION_CHAR_BUDGET

EMPTY_EXTRACTION = {
    "entities": [], "relations": [], "facts": [], "temporal_events": [], "causal_links": [],
}


@pytest.fixture
def workspace(tmp_path):
    def _root(user_id: str) -> str:
        path = os.path.join(str(tmp_path), user_id)
        os.makedirs(path, exist_ok=True)
        return path

    with patch("prax.services.workspace_service.workspace_root", side_effect=_root):
        yield tmp_path


@contextmanager
def _pipeline(extractor_inputs: list[str], summary_inputs: list[str] | None = None):
    def _extract(text: str) -> dict:
        extractor_inputs.append(text)
        return dict(EMPTY_EXTRACTION)

    def _summary(text: str) -> str:
        if summary_inputs is not None:
            summary_inputs.append(text)
        return ""

    with patch.object(consolidation, "_extract_entities_relations", side_effect=_extract), \
         patch.object(consolidation, "_build_daily_summary", side_effect=_summary), \
         patch("prax.services.memory.embedder.embed_text", return_value=[0.1] * 8), \
         patch("prax.services.memory.embedder.sparse_encode", return_value={1: 0.5}), \
         patch("prax.services.memory.vector_store.upsert_memory", return_value="mem-1"), \
         patch("prax.services.memory.vector_store.decay_memories", return_value=0), \
         patch("prax.services.memory.graph_store.decay_graph", return_value=0):
        yield


def _drain(uid: str, extractor_inputs: list[str], max_runs: int = 20):
    """Consolidate until a run sends nothing; return the per-run results."""
    results = []
    for _ in range(max_runs):
        before = len(extractor_inputs)
        results.append(consolidation.consolidate_user(uid))
        if len(extractor_inputs) == before:
            return results
    raise AssertionError("consolidation never drained the trace")


def test_six_kb_trace_is_fully_extracted_across_runs(workspace):
    """40 lines x ~150 chars = ~6 KB.  Old code: ONE run, ONE 6 KB blob, and
    the extractor saw the first 4 000 characters of it."""
    uid = "sixkb"
    lines = [f"[USER] {i:03d} " + "x" * 140 for i in range(40)]
    total_chars = sum(len(ln) for ln in lines)
    assert 5_900 <= total_chars <= 6_200
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("\n".join(lines) + "\n")

    sent: list[str] = []
    with _pipeline(sent):
        results = _drain(uid, sent)

    for blob in sent:
        assert len(blob) <= EXTRACTION_CHAR_BUDGET, (
            f"a {len(blob)}-char batch exceeds the {EXTRACTION_CHAR_BUDGET}-char cap the "
            "extractor enforces — the excess would be silently dropped"
        )
    assert len(sent) >= 2, "a 6 KB trace cannot fit one 4 KB extraction call"

    flattened = [ln for blob in sent for ln in blob.split("\n")]
    assert flattened == lines, "every line must be extracted, once, in order"

    assert sum(r.bytes_seen for r in results) == sum(len(b.encode()) for b in sent)
    assert all(r.bytes_skipped == 0 for r in results)


def test_batch_never_splits_a_line_and_fills_up_to_the_budget(workspace):
    uid = "fill"
    lines = ["[USER] " + "y" * 993 for _ in range(9)]  # 1 000 chars each
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("\n".join(lines) + "\n")

    sent: list[str] = []
    with _pipeline(sent):
        _drain(uid, sent)

    # 4 lines + 3 joiners = 4 003 > 4 000, so exactly 3 lines fit per batch.
    assert [blob.count("\n") + 1 for blob in sent] == [3, 3, 3]
    assert all(blob.split("\n") == lines[i * 3:(i + 1) * 3] for i, blob in enumerate(sent))


def test_oversized_single_line_is_truncated_and_accounted(workspace):
    """A line longer than the budget is sent alone; the tail is reported, not lost silently."""
    uid = "oversized"
    big = "[TOOL] " + "z" * 9_993          # 10 000 chars
    after = "[USER] the next entry"
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text(big + "\n" + after + "\n")

    sent: list[str] = []
    with _pipeline(sent):
        first = consolidation.consolidate_user(uid)
        second = consolidation.consolidate_user(uid)

    assert sent[0] == big[:EXTRACTION_CHAR_BUDGET]
    # One run drains several batches (MAX_BATCHES_PER_RUN), so the first run's
    # bytes_seen covers the truncated big line AND the entry after it.
    assert first.bytes_seen == EXTRACTION_CHAR_BUDGET + len(after)
    assert first.bytes_skipped == len(big) - EXTRACTION_CHAR_BUDGET
    # The big line was a batch of its own, so the next entry is not lost.
    assert sent[1] == after
    assert second.bytes_skipped == 0


def test_daily_summary_sees_the_same_bounded_batch(workspace):
    """The summary read `text[:4000]` of the same over-long blob; it now gets the batch."""
    uid = "summary"
    lines = [f"[USER] {i:03d} " + "s" * 140 for i in range(40)]
    (workspace / uid).mkdir(exist_ok=True)
    (workspace / uid / "trace.log").write_text("\n".join(lines) + "\n")

    extractor_in: list[str] = []
    summary_in: list[str] = []
    with _pipeline(extractor_in, summary_in):
        consolidation.consolidate_user(uid)

    assert len(summary_in) == 1
    assert summary_in[0] == extractor_in[0]
    assert len(summary_in[0]) <= EXTRACTION_CHAR_BUDGET


def test_extractor_prompt_carries_the_whole_batch_when_within_budget():
    """Equivalence at the LLM boundary: a budget-sized blob is not cut."""
    captured: list[str] = []

    class _LLM:
        def invoke(self, msgs):
            captured.append(msgs[-1].content)
            return type("R", (), {"content": '{"entities": [], "relations": [], "facts": []}'})()

    blob = "\n".join("[USER] " + "q" * 92 for _ in range(40))   # 3 999 chars
    assert len(blob) <= EXTRACTION_CHAR_BUDGET
    with patch("prax.agent.llm_factory.build_llm", return_value=_LLM()):
        consolidation._extract_entities_relations(blob)

    assert captured[0].endswith(blob)
