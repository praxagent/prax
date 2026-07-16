"""Key-free test for the bidirectional embedding migration's pure transform."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "reembed_memories",
    Path(__file__).resolve().parent.parent / "scripts" / "reembed_memories.py")
reembed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reembed)


def _pt(pid, content, sparse=None):
    return SimpleNamespace(
        id=pid,
        payload={"content": content, "user_id": "u", "source": "conversation"} if content else {},
        vector={"dense": [0.0] * 1536, **({"sparse": sparse} if sparse else {})},
    )


def test_reembed_preserves_id_sparse_payload_and_swaps_dense():
    sparse = {"indices": [1, 5], "values": [0.3, 0.7]}
    pts = [_pt("a", "gradient descent", sparse), _pt("b", "learning rate")]
    # fake embedder → 768-dim (the target), deterministic
    out = reembed._reembed_points(pts, lambda texts: [[0.5] * 768 for _ in texts])
    assert len(out) == 2
    assert out[0]["id"] == "a"
    assert len(out[0]["dense"]) == 768                 # new dimension
    assert out[0]["sparse"] == sparse                  # sparse preserved verbatim
    assert out[0]["payload"]["content"] == "gradient descent"  # payload preserved


def test_reembed_preserves_empty_content_points():
    # No data loss: an empty-content stub survives with a valid-dimension vector.
    pts = [_pt("a", "has content"), _pt("b", "")]
    seen = {}

    def _embed(texts):
        seen["texts"] = texts
        return [[0.1] * 768 for _ in texts]

    out = reembed._reembed_points(pts, _embed)
    assert [p["id"] for p in out] == ["a", "b"]     # BOTH survive
    assert len(out[1]["dense"]) == 768
    assert seen["texts"][1] == " "                  # empty → single-space placeholder


def test_reembed_empty_is_empty():
    assert reembed._reembed_points([], lambda texts: []) == []
