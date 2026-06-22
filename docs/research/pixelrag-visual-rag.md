# PixelRAG — visual (screenshot) RAG

Reference note. **Verdict: adopt-candidate** (not merely reference) — unlike
[Lift](lift-document-extraction.md), PixelRAG is **Apache-2.0** and fills a real
gap in Prax's text-only retrieval, reusing render infrastructure we already have.

- Repo: <https://github.com/StarTrail-org/PixelRAG> (~1.4k★, Berkeley origin)
- Paper: *"PixelRAG: Web Screenshots Beat Text for Retrieval-Augmented Generation"*

## What it is

Retrieval over **page images instead of parsed text**. Pipeline: render a doc
(URL/PDF) to screenshot **tiles** via Playwright/CDP → embed each tile with a
LoRA-fine-tuned `Qwen/Qwen3-VL-Embedding-2B` → FAISS vector search over the
**image** embeddings → the reader answers directly from the retrieved tiles. The
visual structure that HTML→text parsing throws away — tables, charts, layout,
infographics — stays intact, so questions about visual content become answerable.

- **Stages:** render → chunk → embed → build-index → serve (FastAPI).
- **Inputs/outputs:** URLs/PDFs/local docs in; retrieved image tiles out.
- **Comes with:** a pre-indexed Wikipedia (8.28M articles), a hosted API
  (`api.pixelrag.ai`), and a Claude Code plugin.
- **Lineage:** same family as ColPali/ColQwen visual-document retrieval, productized
  into a screenshot-tile RAG with a tuned embedder + FAISS + reader.
- **License:** **Apache-2.0** (and the Qwen3-VL-Embedding base is permissive) →
  commercial-friendly and genuinely adoptable.

## Why it's relevant to Prax

- **Fills a real gap.** Prax's entire retrieval stack is text-only —
  `knowledge_search` (Qdrant), `url_reader` (Jina→markdown), Library notes, the
  Neo4j concept graph, `trace_search`. None can answer "what's the Q3 figure in
  that chart." PixelRAG is the visual counterpart.
- **We already own the expensive half.** "Render to screenshots via CDP/Playwright"
  is exactly what Prax's sandbox + browser spoke + CDP tools already do. Adoption =
  add an image-embedding index + a VL embedder, not a new browser stack.
- **Shares a backbone with the DIY extractor.** Built on the Qwen3-VL family — the
  same lineage as Phase 2 (vision) of
  [the build-our-own-extraction-model plan](diy-document-extraction-model.md), so
  one effort de-risks the other. PixelRAG (visual *retrieval*) + a Lift-style
  visual→schema *extractor* + `deep_dive` would be a strong multimodal doc stack.

## Caveats

- **GPU** needed for the VL embedder; it's a **second (image) index** alongside the
  text one, not a replacement — run it for visually-rich corpora (PDFs with
  tables/figures, dashboards), not prose.
- **Research-maturity**; the README cites the paper but lists no benchmark numbers.
- **Privacy:** do **not** use the hosted `api.pixelrag.ai` for private docs —
  self-host the embedder (egress/no-egress rule we apply elsewhere, cf.
  [ARD/SSRF note](agentic-resource-discovery.md)).

## If we adopted it (sketch — not built)

A flag-gated **visual-RAG mode** for knowledge ingestion of visually-rich docs:
reuse the existing CDP render path to produce tiles → embed with a self-hosted
Qwen3-VL embedder → a new image-embedding index (FAISS, or a Qdrant image
collection alongside the text one) → surface retrieved tiles to the reader. Keep it
opt-in (off by default), self-hosted (no egress), and **tracked in evals** with a
visual-document retrieval golden (per the [goldens pattern](../../prax/eval/goldens))
so its retrieval quality is measured against the text path.

## Bottom line

The strongest of the recent document-AI references for Prax: Apache-2.0, fills a
genuine retrieval gap, and reuses our CDP render path. Worth promoting from
"reference" to a real **adopt-candidate** for a self-hosted, flag-gated visual-RAG
mode. See also [Lift](lift-document-extraction.md) (extraction, non-commercial) and
[the DIY extraction model](diy-document-extraction-model.md) (shared Qwen3-VL backbone).
