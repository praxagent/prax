# Switching embedding providers (and re-embedding memory)

[← Guides](README.md)

Prax stores each memory as a dense vector in Qdrant. Different embedding providers
produce **different-dimension** vectors, and a Qdrant collection is fixed-dimension,
so you **cannot** just change `EMBEDDING_PROVIDER` — existing memories would be the
wrong size and retrieval would break. Switching providers is a two-step operation:
**re-embed the stored memories** to the new dimension **and** point `.env` at the
new provider, together.

| Provider | `EMBEDDING_MODEL` | dim |
|---|---|---|
| `openai` (default) | `text-embedding-3-small` | 1536 |
| `ollama` (local, free) | `nomic-embed-text` | 768 |
| `local` (fastembed) | (bundled) | 384 |

## The migration — bidirectional

`scripts/reembed_memories.py` re-embeds every stored memory with the
**currently-configured** provider and recreates the Qdrant collection at its
dimension. It works in **either direction** (openai↔ollama↔local): set the target
provider in the environment, run the script.

It is **safe**: every point is read and re-embedded *into memory first*, then the
collection is recreated — a failure (e.g. the embedder is down) aborts before
anything is deleted. It also dumps the pre-migration points to
`$PRAX_EVAL_DIR/embedding-migration-backup/<collection>.json` before the recreate.
**No point is dropped** — empty-content stubs (e.g. empty daily consolidations) are
preserved with a placeholder vector rather than discarded.

```bash
# 1. Stop Prax so no write races the migration.
make shutdown        # or stop the app process

# 2. Dry-run (reports counts + dims, changes nothing).
EMBEDDING_PROVIDER=ollama EMBEDDING_MODEL=nomic-embed-text \
  FLASK_SECRET_KEY=$FLASK_SECRET_KEY uv run python scripts/reembed_memories.py --dry-run

# 3. Run it for real.
EMBEDDING_PROVIDER=ollama EMBEDDING_MODEL=nomic-embed-text \
  FLASK_SECRET_KEY=$FLASK_SECRET_KEY uv run python scripts/reembed_memories.py

# 4. Point .env at the SAME provider (this is what makes it permanent):
#      EMBEDDING_PROVIDER=ollama
#      EMBEDDING_MODEL=nomic-embed-text
# 5. Restart Prax.
```

**Both the migration and the `.env` change are required, together.** If the
collection is at 768 but `.env` still says `openai` (1536), memory retrieval breaks
on a dimension mismatch — and vice-versa.

## Ollama (local embeddings) prerequisites

`EMBEDDING_PROVIDER=ollama` needs a running Ollama with the model pulled:

```bash
curl -fsSL https://ollama.com/install.sh | sh   # installs + starts the service
ollama pull nomic-embed-text                     # ~275 MB, CPU-fine
```

Ollama serves at `http://localhost:11434` (`OLLAMA_BASE_URL`). No GPU required —
`nomic-embed-text` runs fast on CPU.

## Reverting

To go back, run the migration the other way and revert `.env`:

```bash
EMBEDDING_PROVIDER=openai EMBEDDING_MODEL=text-embedding-3-small \
  FLASK_SECRET_KEY=$FLASK_SECRET_KEY uv run python scripts/reembed_memories.py
# then set EMBEDDING_PROVIDER=openai / EMBEDDING_MODEL=text-embedding-3-small in .env
```

## Evals

`make eval … CHEAP=1` already forces `EMBEDDING_PROVIDER=ollama` for the eval run
only (so eval memory/knowledge paths use local embeddings and never leak to a
paid/dead OpenAI key), independent of your production `.env`.
