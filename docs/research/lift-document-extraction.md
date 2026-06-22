# Lift — schema-constrained document extraction (Datalab)

Reference note. **Verdict: document, don't bundle.** Lift is a strong open-weights
model for a capability Prax doesn't currently have — *structured extraction of
typed JSON from PDFs/images against a caller-supplied JSON Schema* — but its
license is **not commercial-friendly**, so it must stay an **optional, user-
installed, self-hosted** path (never a Prax dependency), useful to people running
Prax non-commercially.

- Model card: <https://huggingface.co/datalab-to/lift>
- Package: `lift-pdf` (PyPI) · CLI `lift_extract input.pdf ./output --schema schema.json`

## What it is

A **structured-data-extraction VLM**: input a PDF or image **plus a JSON Schema**,
get back a JSON object matching that schema via **schema-constrained decoding**
(the decoder is masked to the grammar, so output is guaranteed valid + well-typed).
This is *extraction*, not OCR, layout analysis, or markdown conversion.

- **Base / size:** ~9B params (≈10B weights), BF16, built on Qwen 3.5.
- **Schema support:** standard JSON Schema — strings, numbers, integers, booleans,
  arrays, nested objects.
- **Multi-page:** handles 6–64-page docs in a single pass, including values that
  span pages (cross-page fields).
- **Reported benchmark** (225 docs): **90.2% field accuracy**, **20.9% full-document
  accuracy**, **~9.5s median latency**. Claims to beat NuExtract3 (4B) and base
  Qwen3.5-9B on field accuracy.
- **Run it:** vLLM (recommended, lightweight) or HF Transformers (needs torch);
  `pip install lift-pdf` (or `[hf]`); local inference or a remote vLLM server via
  `VLLM_API_BASE`.

Datalab is the team behind **Marker** (PDF→markdown) and **Surya** (OCR/layout);
Lift is their *extraction* model — the complement to those conversion tools.

## License — the reason this is reference-only ⚠️

**Modified OpenRAIL-M** (model weights). Verbatim from the card:

> Free for research, personal use, and startups under $5M funding/revenue.
> Cannot be used competitively with our API. For broader commercial licensing,
> see pricing.

The **code is Apache-2.0**; the **weights are restricted**. Implications for Prax:

- **Do NOT add `lift-pdf` or the weights to Prax's dependency tree or images.**
  Prax is meant to stay usable by anyone, including commercial users; bundling a
  restricted-weight model would taint that and the "can't compete with our API"
  clause is a landmine for a general harness.
- It is a fine **opt-in for non-commercial / sub-$5M users** who choose to install
  and self-host it themselves — that choice (and the license acceptance) is the
  user's, not something Prax ships on their behalf.

## Where it would map in Prax

Lift fills a genuine gap. Prax's current document stack is **read/convert**, not
**extract-to-schema**:

| Stage | Today | Lift |
|---|---|---|
| URL → clean markdown | `prax/services/url_reader.py` (Jina Reader) | — |
| PDF → text | `prax/services/pdf_service.py` (opendataloader-pdf) | — |
| Doc → **typed JSON by schema** | *(none — would be LLM-on-converted-text, no validity guarantee)* | **Lift** |

For structured extraction today Prax would feed Jina/opendataloader text to a
general LLM and hope the JSON is well-formed. Lift's schema-constrained decoding
*guarantees* valid, typed output and is purpose-trained for multi-page documents —
materially better for invoices, forms, lab reports, contracts, etc.

It also fits Prax's existing local-model rails: `VLLM_BASE_URL` / `LOCAL_MODEL`
(`prax/settings.py`) and the GPU sandbox (`make sandbox-gpu`) already exist, so a
non-commercial user could host Lift on vLLM and point a Prax extraction tool at it
via `VLLM_API_BASE` with no new core infra.

## If a non-commercial user wanted it (integration sketch — not built)

Keep it **plugin-shaped**, never core:

1. User self-hosts Lift on vLLM (their license acceptance, their hardware), exposing
   an OpenAI-compatible endpoint.
2. A small **optional plugin** (or a flag-gated tool) `document_extract(path, schema)`
   posts the doc + schema to that endpoint and returns the typed JSON — gated behind
   an env flag (default off) and absent unless the user configures the endpoint, so
   no restricted code/weights ride along by default.
3. Wire it as a higher-accuracy alternative to the LLM-on-text path for explicit
   "pull these fields out of this document" requests.

This mirrors how we treat other restrictive/external pieces: the capability is
documented and reachable, but the license decision and the dependency stay with the
user. No `make ci` impact (nothing added to the default tree).

## Bottom line

A best-in-class open-weights answer to schema-constrained document extraction, and
a real gap in Prax's stack — but the Modified OpenRAIL-M terms mean it can only ever
be an **opt-in, self-hosted** path for non-commercial users. Documented here so it
isn't lost; **not** adopted as a dependency or default.
