"""Re-embed stored memories with the CURRENTLY-configured embedding provider.

**Bidirectional.** Embedding providers produce vectors of different dimension
(openai/text-embedding-3-small = 1536, ollama/nomic-embed-text = 768, local = 384),
and Qdrant collections are fixed-dimension. So switching ``EMBEDDING_PROVIDER``
requires re-embedding every stored memory to the new provider's dimension and
recreating the collection at that size — otherwise retrieval breaks on a dim
mismatch. This migration does exactly that, in EITHER direction:

    # openai -> ollama
    EMBEDDING_PROVIDER=ollama EMBEDDING_MODEL=nomic-embed-text \\
        FLASK_SECRET_KEY=... uv run python scripts/reembed_memories.py

    # ollama -> openai (the reverse)
    EMBEDDING_PROVIDER=openai EMBEDDING_MODEL=text-embedding-3-small \\
        FLASK_SECRET_KEY=... uv run python scripts/reembed_memories.py

    # inspect only, change nothing
    ... uv run python scripts/reembed_memories.py --dry-run

**Safe by construction:** every point is read and RE-EMBEDDED INTO MEMORY first;
only then is the collection recreated and the points re-inserted. A failure during
re-embedding (e.g. the embedder is down) aborts before anything is deleted, so the
old collection is never lost. The sparse (TF-IDF) vectors and all payloads are
preserved verbatim — only the dense vector changes.

Point the ``.env`` ``EMBEDDING_PROVIDER``/``EMBEDDING_MODEL`` at the new provider
AND run this migration together; run it while Prax is stopped so no writes race it.
See docs/guides/embeddings-migration.md.
"""
from __future__ import annotations

import argparse
import sys


def _reembed_points(points: list, embed_fn) -> list:
    """Re-embed each point's ``content``, preserving id/sparse/payload.

    Pure transform (embedder injected) so it's unit-tested without Qdrant. EVERY
    point survives — no data is dropped. Empty-content points (e.g. empty
    consolidation stubs) are embedded with a single-space placeholder so they keep
    a valid-dimension vector rather than a degenerate zero vector. Returns dicts
    ready to rebuild a PointStruct: ``{id, dense, sparse, payload}``.
    """
    if not points:
        return []
    texts = [((p.payload or {}).get("content") or " ") for p in points]
    dense_vecs = embed_fn(texts)
    out = []
    for p, dense in zip(points, dense_vecs, strict=True):
        vec = p.vector or {}
        sparse = vec.get("sparse") if isinstance(vec, dict) else None
        out.append({"id": p.id, "dense": dense, "sparse": sparse, "payload": p.payload or {}})
    return out


def migrate(collection: str, *, dry_run: bool = False) -> dict:
    from qdrant_client.models import (
        Distance,
        PointStruct,
        SparseIndexParams,
        SparseVector,
        SparseVectorParams,
        VectorParams,
    )

    from prax.services.memory.embedder import embed_texts
    from prax.services.memory.vector_store import _dense_dim, _get_client

    client = _get_client()
    new_dim = _dense_dim()

    existing = {c.name for c in client.get_collections().collections}
    if collection not in existing:
        return {"collection": collection, "skipped": "does not exist", "count": 0}

    # Read EVERYTHING (payload + vectors) before touching anything.
    points, offset = [], None
    while True:
        batch, offset = client.scroll(collection_name=collection, with_payload=True,
                                      with_vectors=True, limit=256, offset=offset)
        points.extend(batch)
        if offset is None:
            break

    cur_dim = None
    cfg = client.get_collection(collection).config.params.vectors
    if isinstance(cfg, dict):  # named vectors
        d = cfg.get("dense")
        cur_dim = getattr(d, "size", None)

    if dry_run:
        with_content = sum(1 for p in points if (p.payload or {}).get("content"))
        return {"collection": collection, "points_preserved": len(points),
                "with_content": with_content, "empty_stubs": len(points) - with_content,
                "current_dim": cur_dim, "target_dim": new_dim}

    # Re-embed into memory FIRST — abort before any destructive op if this fails.
    rebuilt = _reembed_points(points, embed_texts)

    # Cheap safety net: dump the ORIGINAL points (payload + vectors) to a backup
    # file before the destructive recreate, so the pre-migration state is
    # recoverable if create/upsert fails after the delete.
    import json
    from pathlib import Path

    from prax.eval import PRAX_EVAL_DIR
    backup_dir = Path(PRAX_EVAL_DIR) / "embedding-migration-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = [{"id": p.id, "payload": p.payload,
               "vector": p.vector if isinstance(p.vector, dict) else None} for p in points]
    (backup_dir / f"{collection}.json").write_text(json.dumps(backup, default=str))

    # Recreate the collection at the new dimension (mirrors _ensure_collection).
    client.delete_collection(collection)
    client.create_collection(
        collection_name=collection,
        vectors_config={"dense": VectorParams(size=new_dim, distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams(index=SparseIndexParams())},
    )
    # Recreate the payload indexes so the collection matches a fresh one (fast
    # user_id/source/tags/created_at filtering) — mirrors _ensure_collection.
    from qdrant_client.models import PayloadSchemaType
    for field, schema in (("user_id", PayloadSchemaType.KEYWORD),
                          ("source", PayloadSchemaType.KEYWORD),
                          ("tags", PayloadSchemaType.KEYWORD),
                          ("created_at", PayloadSchemaType.DATETIME)):
        try:
            client.create_payload_index(collection, field, schema)
        except Exception:  # noqa: BLE001 - a collection may lack a field; non-fatal
            pass
    # Re-insert with new dense + preserved sparse + preserved payload + same id.
    structs = []
    for r in rebuilt:
        vectors: dict = {"dense": r["dense"]}
        sp = r["sparse"]
        if sp is not None:
            if isinstance(sp, SparseVector):
                vectors["sparse"] = sp
            elif isinstance(sp, dict) and "indices" in sp:
                vectors["sparse"] = SparseVector(indices=sp["indices"], values=sp["values"])
        structs.append(PointStruct(id=r["id"], vector=vectors, payload=r["payload"]))
    if structs:
        client.upsert(collection_name=collection, points=structs)
    return {"collection": collection, "reembedded": len(structs),
            "total_points": len(points), "current_dim": cur_dim, "target_dim": new_dim}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    ap.add_argument("--collections", nargs="*",
                    default=["prax_memories", "prax_knowledge_concepts"],
                    help="collections to migrate (default: the two memory stores)")
    args = ap.parse_args()

    from prax.services.memory.vector_store import _dense_dim
    from prax.settings import settings
    print(f"Embedding provider: {getattr(settings, 'embedding_provider', 'openai')} "
          f"/ model {getattr(settings, 'embedding_model', '?')} → dim {_dense_dim()}")
    for coll in args.collections:
        try:
            print(migrate(coll, dry_run=args.dry_run))
        except Exception as exc:  # noqa: BLE001
            print(f"  {coll}: ERROR {type(exc).__name__}: {exc}")
            return 1
    print("Done." if not args.dry_run else "Dry run — nothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
