"""Tests for the Open Knowledge Format (OKF) bridge — pure FS round-trip + the
knowledge_graph export/import wiring."""
from __future__ import annotations

from prax.services.memory import knowledge_graph as kg
from prax.services.memory import okf_bridge


def _concepts():
    return [
        {"id": "a", "name": "transformer", "display_name": "Transformer",
         "description": "A seq model.", "importance": 0.8, "source": "paper.pdf",
         "source_type": "pdf", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"},
        {"id": "b", "name": "attention", "display_name": "Attention",
         "description": "Core mechanism.", "importance": 0.9, "source": "",
         "source_type": "concept", "created_at": "2026-01-03T00:00:00Z", "updated_at": ""},
    ]


def test_slugify():
    assert okf_bridge.slugify("Weekly Active Users!") == "weekly-active-users"
    assert okf_bridge.slugify("") == "concept"


def test_write_bundle_creates_files(tmp_path):
    rels = [{"from_id": "a", "to_id": "b", "type": "uses"}]
    summary = okf_bridge.write_bundle(_concepts(), rels, str(tmp_path), "papers")

    assert summary == {"namespace": "papers", "concepts": 2, "relations": 1, "path": str(tmp_path)}
    assert (tmp_path / "transformer.md").exists()
    assert (tmp_path / "attention.md").exists()
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "log.md").exists()

    t = (tmp_path / "transformer.md").read_text()
    assert t.startswith("---\n")
    assert "type: pdf" in t              # required OKF field
    assert "title: Transformer" in t
    assert "# Relations" in t
    assert "uses: [Attention](./attention.md)" in t


def test_parse_frontmatter_tolerant():
    fm, body = okf_bridge.parse_frontmatter("no frontmatter here")
    assert fm == {} and body == "no frontmatter here"
    fm, body = okf_bridge.parse_frontmatter("---\ntype: x\ntitle: Y\n---\nhello")
    assert fm == {"type": "x", "title": "Y"} and body.strip() == "hello"


def test_round_trip(tmp_path):
    rels = [{"from_id": "a", "to_id": "b", "type": "uses"}]
    okf_bridge.write_bundle(_concepts(), rels, str(tmp_path), "papers")
    records, edges = okf_bridge.read_bundle(str(tmp_path))

    names = sorted(r["name"] for r in records)
    assert names == ["Attention", "Transformer"]
    # index.md / log.md are not concepts.
    assert all(r["name"] not in ("papers", "papers — history") for r in records)
    # The relation survives the round-trip with its type.
    assert ("Transformer", "uses", "Attention") in edges


def test_read_bundle_skips_reserved(tmp_path):
    (tmp_path / "index.md").write_text("---\ntype: index\n---\n# x\n")
    (tmp_path / "log.md").write_text("# history\n")
    (tmp_path / "c.md").write_text("---\ntype: concept\ntitle: C\n---\n# C\n")
    records, _ = okf_bridge.read_bundle(str(tmp_path))
    assert [r["name"] for r in records] == ["C"]


# --------------------------------------------------------------------------- #
# knowledge_graph wiring
# --------------------------------------------------------------------------- #

def test_export_namespace_okf(tmp_path, monkeypatch):
    monkeypatch.setattr(kg, "list_concepts", lambda uid, ns: _concepts())
    monkeypatch.setattr(kg, "list_relations", lambda uid, ns: [{"from_id": "a", "to_id": "b", "type": "uses"}])
    summary = kg.export_namespace_okf("u1", "papers", str(tmp_path))
    assert summary["concepts"] == 2 and summary["relations"] == 1
    assert (tmp_path / "transformer.md").exists()


def test_import_okf_calls_graph(tmp_path, monkeypatch):
    # First produce a bundle on disk.
    okf_bridge.write_bundle(
        _concepts(), [{"from_id": "a", "to_id": "b", "type": "uses"}], str(tmp_path), "papers",
    )
    added: list[tuple] = []
    rels: list[tuple] = []
    monkeypatch.setattr(kg, "add_concept",
                        lambda **kw: added.append((kw["name"], kw["namespace"], kw["source_type"])) or "cid")
    monkeypatch.setattr(kg, "add_knowledge_relation",
                        lambda uid, ns, s, rt, t, **kw: rels.append((s, rt, t)) or True)

    result = kg.import_okf("u1", str(tmp_path), namespace="restored")
    assert result["concepts"] == 2
    assert {n for n, _, _ in added} == {"Transformer", "Attention"}
    assert all(ns == "restored" for _, ns, _ in added)
    assert ("Transformer", "uses", "Attention") in rels
    assert result["relations"] == 1
