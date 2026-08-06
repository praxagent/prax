"""The self-regen loop's two structural invariants.

Both halves of this loop existed for weeks with zero consumers: `run_self_regen`
proposed and graded, `accept_change` decided — and nothing connected them, so
the loop selected on the very score it optimized against. These tests cover the
connection and the guard around it.

- **Scorer read-only** (Weng/AHE): if the files that decide the score change
  mid-run, every measurement taken with them is void.
- **Select on the private holdout** (AIDE² / `accept_change`): the capability
  suite is the training signal; adoption is decided on held-out goldens, and is
  fail-closed when there are none.
"""

import pytest

from prax.eval import self_regen


@pytest.fixture
def out_dir(tmp_path):
    return tmp_path / "run"


def _loop(out_dir, *, patch="Prefer verified sources.", score=0.9, gate_fn=None,
          **kw):
    """Run the loop fully injected — no keys, no model, no suite."""
    return self_regen.run_self_regen(
        rounds=1,
        proposer=lambda _ws: patch,
        evaluator=lambda p: score if p else 0.5,
        auditor=lambda _p: (True, "ok"),
        out_dir=out_dir,
        gate_fn=gate_fn or (lambda _p: {"accept": True, "reason": "test-accept"}),
        **kw,
    )


class TestPrivateHoldoutGate:
    def test_accepted_patch_survives_to_the_proposal(self, out_dir):
        r = _loop(out_dir)
        assert r["best_patch"] == "Prefer verified sources."
        assert r["gate"]["accept"] is True
        assert "Prefer verified sources." in (out_dir / "PROPOSAL.md").read_text()

    def test_rejected_patch_is_dropped_even_though_it_scored_higher(self, out_dir):
        """The whole point: a patch can win the optimized score and still be
        refused on the held-out one."""
        r = _loop(out_dir, gate_fn=lambda _p: {"accept": False,
                                               "reason": "private went down"})
        assert r["best_patch"] == ""
        assert r["gate"]["accept"] is False
        assert r["applied"] is False

    def test_a_gate_that_crashes_rejects_rather_than_adopts(self, out_dir):
        def boom(_p):
            raise RuntimeError("suite unavailable")

        r = _loop(out_dir, gate_fn=boom)
        assert r["best_patch"] == ""
        assert r["gate"]["accept"] is False
        assert "gate failed to run" in r["gate"]["reason"]

    def test_gate_can_be_disabled_explicitly(self, out_dir):
        """Off is allowed — but only by an explicit argument, never by default."""
        r = _loop(out_dir, gate=False)
        assert r["gate"] is None
        assert r["best_patch"] == "Prefer verified sources."

    def test_gate_is_on_by_default(self):
        import inspect
        sig = inspect.signature(self_regen.run_self_regen)
        assert sig.parameters["gate"].default is True


class TestScorerReadOnly:
    def test_fingerprint_is_stable_and_covers_the_suite(self):
        a = self_regen._scorer_fingerprint()
        assert a == self_regen._scorer_fingerprint()
        assert len(a) == 64

    def test_a_changed_scorer_voids_the_whole_run(self, out_dir, monkeypatch):
        """A patch that edits the verifier must not be able to keep its score —
        the DGM failure mode, structurally refused."""
        seq = iter(["before-hash", "AFTER-DIFFERENT-HASH"])
        monkeypatch.setattr(self_regen, "_scorer_fingerprint", lambda: next(seq))

        r = _loop(out_dir)
        assert r["scorer_tampered"] is True
        assert r["best_patch"] == ""
        assert r["applied"] is False

    def test_untampered_run_records_the_fingerprint(self, out_dir):
        r = _loop(out_dir)
        assert r["scorer_tampered"] is False
        assert len(r["scorer_fingerprint"]) == 64

    def test_tamper_check_runs_before_the_gate(self, out_dir, monkeypatch):
        """A tampered run must not be rescued by a passing gate."""
        seq = iter(["x", "y"])
        monkeypatch.setattr(self_regen, "_scorer_fingerprint", lambda: next(seq))
        r = _loop(out_dir, gate_fn=lambda _p: {"accept": True, "reason": "would pass"})
        assert r["scorer_tampered"] is True
        assert r["best_patch"] == ""


class TestGateWiringIsReal:
    def test_the_default_gate_calls_accept_change(self, monkeypatch):
        """Guard against the gate quietly becoming a stub again."""
        called = {}

        def fake_suite(**kw):
            called.setdefault("suites", 0)
            called["suites"] += 1
            return {"avg_private": 0.8, "avg_public": 0.8}

        def fake_accept(baseline, candidate, **kw):
            called["accept_change"] = True
            return {"accept": True, "reason": "ok"}

        import prax.eval.goldens as g
        monkeypatch.setattr(g, "run_golden_suite", fake_suite)
        monkeypatch.setattr(g, "accept_change", fake_accept)
        monkeypatch.setattr(self_regen, "_base_system_prompt", lambda: "BASE")

        out = self_regen._gate_on_private_holdout("p", tier="low")
        assert out["accept"] is True
        assert called["accept_change"] is True
        # baseline and candidate — the comparison must actually be run twice.
        assert called["suites"] == 2
