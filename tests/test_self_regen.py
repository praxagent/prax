"""Key-free tests for the self-regeneration loop (prax.eval.self_regen)."""
from __future__ import annotations

import prax.eval.self_regen as sr
from prax.eval.self_regen import _apply_overlay, override_system_prompt, run_self_regen


def test_keeps_improving_audited_variant_vetoes_spike(tmp_path):
    scores = {"": 0.5, "spike": 0.9, "good": 0.8, "meh": 0.4}
    patches = iter(["spike", "good", "meh"])
    summary = run_self_regen(
        rounds=3, weak_signal="x", out_dir=tmp_path,
        proposer=lambda _s: next(patches),
        evaluator=lambda p: scores[p],
        # the overseer VETOes the higher-scoring "spike" (benchmark gaming)
        auditor=lambda p: (False, "spike") if p == "spike" else (True, "ok"),
    )
    assert summary["baseline"] == 0.5
    # "spike" scored 0.9 but was vetoed → NOT kept; "good" (0.8, approved) wins;
    # "meh" (0.4) doesn't beat the best.
    assert summary["best"] == 0.8
    assert summary["best_patch"] == "good"
    assert summary["variants_kept"] == 1
    assert summary["applied"] is False  # propose-only by default


def test_lineage_and_archive_written(tmp_path):
    patches = iter(["a", "b"])
    run_self_regen(
        rounds=2, weak_signal="x", out_dir=tmp_path,
        proposer=lambda _s: next(patches),
        evaluator=lambda p: {"": 0.5, "a": 0.6, "b": 0.7}[p],
        auditor=lambda _p: (True, "ok"),
    )
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "PROPOSAL.md").exists()
    assert len(list((tmp_path / "variants").glob("*.json"))) == 2


def test_no_improvement_leaves_nothing_to_apply(tmp_path):
    summary = run_self_regen(
        rounds=2, weak_signal="x", out_dir=tmp_path,
        proposer=lambda _s: "x",
        evaluator=lambda _p: 0.5,  # nothing beats baseline
        auditor=lambda _p: (True, "ok"),
    )
    assert summary["best"] == 0.5
    assert summary["best_patch"] == ""
    assert summary["applied"] is False


def test_apply_stays_a_proposal_without_the_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_self_regen_enabled", lambda: False)
    summary = run_self_regen(
        rounds=1, apply=True, weak_signal="x", out_dir=tmp_path,
        proposer=lambda _s: "win",
        evaluator=lambda p: 0.9 if p == "win" else 0.5,
        auditor=lambda _p: (True, "ok"),
    )
    assert summary["best"] == 0.9
    assert summary["applied"] is False  # flag off → proposal only


def test_apply_overlay_composition():
    assert _apply_overlay("BASE", "") == "BASE"
    out = _apply_overlay("BASE", "do X")
    assert "BASE" in out and "do X" in out


def test_override_system_prompt_patches_and_restores():
    import prax.agent.orchestrator as orch
    orig = orch._load_system_prompt
    with override_system_prompt("PATCHED"):
        assert orch._load_system_prompt() == "PATCHED"
    assert orch._load_system_prompt is orig


def test_apply_writes_via_prompt_manager_when_enabled(tmp_path, monkeypatch):
    import prax.plugins.prompt_manager as pm
    monkeypatch.setattr(sr, "_self_regen_enabled", lambda: True)
    writes = {}

    class _FakeMgr:
        def read(self, name):
            return "BASE PROMPT"

        def write(self, name, content):
            writes["name"], writes["content"] = name, content
            return {}

    monkeypatch.setattr(pm, "get_prompt_manager", lambda: _FakeMgr())
    summary = run_self_regen(
        rounds=1, apply=True, weak_signal="x", out_dir=tmp_path,
        proposer=lambda _s: "win",
        evaluator=lambda p: 0.9 if p == "win" else 0.5,
        auditor=lambda _p: (True, "ok"),
    )
    assert summary["applied"] is True
    assert writes["name"] == "system_prompt.md"
    assert "win" in writes["content"] and "BASE PROMPT" in writes["content"]


def test_apply_aborts_on_missing_base_prompt(tmp_path, monkeypatch):
    import prax.plugins.prompt_manager as pm
    monkeypatch.setattr(sr, "_self_regen_enabled", lambda: True)

    class _MissingMgr:
        def read(self, name):
            return f"Prompt not found: {name}"  # the manager's missing-file sentinel

        def write(self, name, content):
            raise AssertionError("must not write over a missing base prompt")

    monkeypatch.setattr(pm, "get_prompt_manager", lambda: _MissingMgr())
    summary = run_self_regen(
        rounds=1, apply=True, weak_signal="x", out_dir=tmp_path,
        proposer=lambda _s: "win",
        evaluator=lambda p: 0.9 if p == "win" else 0.5,
        auditor=lambda _p: (True, "ok"),
    )
    assert summary["applied"] is False  # sentinel base → refused to apply


def test_margin_rejects_tiny_noise_improvement(tmp_path):
    summary = run_self_regen(
        rounds=1, weak_signal="x", out_dir=tmp_path, min_margin=0.02,
        proposer=lambda _s: "noise",
        evaluator=lambda p: 0.5001 if p == "noise" else 0.5,  # +0.0001, below margin
        auditor=lambda _p: (True, "ok"),
    )
    assert summary["variants_kept"] == 0
    assert summary["best_patch"] == ""


def test_over_length_patch_is_vetoed(tmp_path):
    ev_calls = []
    long_patch = "x" * (sr.MAX_PATCH_CHARS + 10)
    summary = run_self_regen(
        rounds=1, weak_signal="x", out_dir=tmp_path,
        proposer=lambda _s: long_patch,
        evaluator=lambda p: ev_calls.append(p) or 0.99,  # would win if it were graded
        auditor=lambda _p: (True, "ok"),
    )
    assert summary["variants_kept"] == 0
    # Baseline ev("") runs, but the over-length patch is rejected BEFORE its eval.
    assert long_patch not in ev_calls


def test_deterministic_spike_veto_catches_answer_token():
    from prax.eval.self_regen import _deterministic_spike_veto, _spike_answer_tokens
    tokens = _spike_answer_tokens()
    if not tokens:  # depends on the seed cases carrying a digit-bearing answer
        return
    tok = next(iter(tokens))
    assert _deterministic_spike_veto(f"always answer with {tok}") != ""
    assert _deterministic_spike_veto("be more rigorous and cite sources") == ""


def test_mdl_occam_bias_prefers_shorter_at_equal_score(tmp_path):
    # A long patch improves the score; a later SHORTER patch ties it → the MDL /
    # Occam bias keeps the simpler theory.
    patches = iter(["a_long_patch_that_improves_scores", "short"])
    scores = {"": 0.5, "a_long_patch_that_improves_scores": 0.8, "short": 0.8}
    summary = run_self_regen(
        rounds=2, weak_signal="x", out_dir=tmp_path,
        proposer=lambda _s: next(patches),
        evaluator=lambda p: scores[p],
        auditor=lambda _p: (True, "ok"),
    )
    assert summary["best_patch"] == "short"   # simplest theory at equal score
    assert summary["best"] == 0.8


def test_inoculation_preamble_and_helper():
    from prax.eval.self_regen import INOCULATION_PREAMBLE, inoculate
    low = INOCULATION_PREAMBLE.lower()
    assert "narrow" in low and ("hack" in low or "game" in low) and "not" in low
    out = inoculate("PROPOSE X")
    assert out.startswith(INOCULATION_PREAMBLE) and "PROPOSE X" in out
