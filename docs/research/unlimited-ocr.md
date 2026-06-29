# Unlimited-OCR — long-horizon document OCR (Baidu)

Reference note on **[baidu/Unlimited-OCR](https://github.com/baidu/Unlimited-OCR)**
([HF weights](https://huggingface.co/baidu/Unlimited-OCR)).

**Verdict: adopt-candidate** (not just reference) — a flag-gated, self-hosted
**OCR/parse front-end** for the *hard documents* Prax's text-extraction read stack
loses structure on. The unlock is the **MIT license**: it can be a first-class,
commercial-clean path where [Lift](lift-document-extraction.md) could not.

- Repo: [github.com/baidu/Unlimited-OCR](https://github.com/baidu/Unlimited-OCR) · weights `baidu/Unlimited-OCR` (HF + ModelScope), **MIT**.
- Lineage: a vision-language model building on **DeepSeek-OCR**; 32k context; `Gundam` (crop) / `Base` modes.

## What it is

A VLM for **"one-shot long-horizon parsing"**: it takes a whole multi-page
PDF / image set as **unified input** and emits faithful structured text — tables,
layout, multi-column, cross-page context — instead of transcribing page-by-page
and losing the structure between pages. Input: images/PDF. Output: parsed,
structured document text. Runs locally via HF Transformers, or behind an **SGLang
server that's OpenAI-compatible** (so it rides the same `base_url` rails Prax
already uses for vLLM backends), plus a batch `infer.py` with concurrency.

## The gap it fills in Prax's read/convert stack [verified]

Prax's document reading today is **text-extraction-based**:

- `prax/services/url_reader.py` — Jina Reader (URL → clean markdown; great for
  web/clean docs).
- `prax/services/pdf_service.py` — opendataloader PDF parsing.

Both degrade on **scanned / handwritten / table- and formula-heavy / multi-column /
multi-page** documents — exactly the "HTML→text parsing destroys tables/charts/
layout" failure the [PixelRAG note](pixelrag-visual-rag.md) flagged. Prax has **no
VLM-based faithful-OCR path** for those. Unlimited-OCR is precisely that stage.

## Why MIT is the load-bearing detail

The [Lift assessment](lift-document-extraction.md) concluded Lift stays an opt-in,
non-commercial path **only because its weights are OpenRAIL-M restricted** — that
license was the documented blocker to Lift ever being a Prax dependency.
Unlimited-OCR is **MIT**, so the same capability class becomes adoptable as a
flag-gated, self-hostable default — no commercial restriction, no egress.

## How it composes (complements, doesn't overlap)

- **It is OCR/parsing, not typed extraction.** Output is faithful structured *text*,
  not schema-constrained typed JSON. So it's the **OCR front-end *into*** the DIY
  schema-extraction plan ([#14](../IDEAS_BACKLOG.md) / [diy-document-extraction-model.md](diy-document-extraction-model.md)
  Phase 0 constrained-decode), not a replacement: `PDF/image → (Unlimited-OCR) →
  faithful text/layout → (constrained decode) → typed JSON`.
- **Distinct from [PixelRAG](pixelrag-visual-rag.md) (#15)** — that's *retrieval*
  over page images; this is *transcription*. Different stage; they can stack.
- **Everyday read path**: a self-hosted upgrade over Jina for hard PDFs/scans in
  `fetch_url_content` / auto-capture / notes, behind a flag (Jina stays the default
  for clean web).

## How it'd wire (sketch — not built)

Reuse the existing rails: serve it via SGLang's OpenAI-compatible endpoint and feed
images through the existing base64 vision path (`vision_tools.py`) / a
`pdf_service` branch; gate behind a flag (default off → Jina/opendataloader), local
or on the GPU sandbox ([cloud-gpu.md](../guides/cloud-gpu.md)). No core change to
the inference path — same pattern as the open-backend work.

## Honest caveats

- **Another self-hosted model to run** (GPU + ops). Justified only for the hard-doc
  classes Jina/opendataloader actually fail on — not the common case.
- **No benchmark numbers** in the repo README; quality on Prax's real document mix
  is unmeasured. Validate against current Jina/opendataloader output before making
  it a default for any class (a small comparison golden, same discipline as #20).
- DeepSeek-OCR lineage means VLM-typical failure modes (hallucinated text on
  low-quality scans) — the constrained-decode stage (#14) and a verify pass matter.

## Bottom line

The MIT license turns "faithful OCR of hard documents" from a Lift-style
non-starter into a real **adopt-candidate**: a flag-gated, self-hosted OCR
front-end that fills a genuine gap in Prax's read stack and feeds the schema-
extraction plan. Tracked from [`../IDEAS_BACKLOG.md`](../IDEAS_BACKLOG.md) #14 (as
the OCR front-end option) — validate quality vs. the current stack before
defaulting it on.
