"""Concise notes aren't rejected for lacking deep-dive depth."""
from __future__ import annotations

from prax.services import note_quality as nq


def test_concise_note_skips_deepdive_heuristics():
    # >20 body lines, headings, but no "explanatory transitions" — a clean
    # concise note. Deep-dive mode flags it; concise mode does not.
    body = "\n".join(f"- point {i} about the topic" for i in range(25))
    content = f"## Topic\n\n{body}\n\n## More\n\n{body}"
    assert nq.heuristic_check("T", content, deep_dive=True)   # flagged as deep dive
    assert nq.heuristic_check("T", content, deep_dive=False) == []  # fine when concise


def test_concise_still_rejects_raw_dumps():
    dumped = "Some text [Image]\n" * 6  # raw-dump artifact
    issues = nq.heuristic_check("T", dumped, deep_dive=False)
    assert issues  # cleanliness still enforced for concise notes


def test_llm_review_selects_concise_prompt(monkeypatch):
    captured = {}

    class _LLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            class _R: content = '{"approved": true, "issues": [], "verdict": "ok"}'
            return _R()

    monkeypatch.setattr("prax.agent.llm_factory.build_llm", lambda **k: _LLM())
    monkeypatch.setattr("prax.plugins.llm_config.get_component_config", lambda c: {})
    nq.llm_review("Gradient Descent", "A short clean note.", deep_dive=False)
    assert "CONCISE" in captured["prompt"]
    assert "Brevity is NOT a defect" in captured["prompt"]
    nq.llm_review("Gradient Descent", "A short clean note.", deep_dive=True)
    assert "deep dive with equations and toy examples" in captured["prompt"]
