# ExtractBench — a vendor benchmark, built unusually well, in a domain next to ours

**Source:** [Introducing ExtractBench](https://www.llamaindex.ai/blog/introducing-extractbench)
(LlamaIndex). Dataset on HuggingFace, harness on GitHub, paper on arXiv.

**Verdict: document-don't-adopt.** Prax does not do schema-driven document
extraction and should not start. Kept for one measured result that
**changes the shape of a queued decision** (#63), and one lesson about reading
vendor benchmarks.

## What it is

370 enterprise documents, 4,869 pages, 8 business domains, 67 document types
each with its own schema — SEC filings, customs documents, healthcare
remittance, bankruptcy filings, energy regulatory forms. Scored on F1 at value
level plus **per-page cost**, across five axes: task challenge, perception
(born-digital / scanned / handwritten / rotated), table structure, document
length, and domain.

The construction deserves credit and is better than most: ground truth from
**multiple extractors run on identical schemas, with agreements as candidate
truth and disagreements resolved by human review**; synthetic long lists built
data-first with known values *before* PDF rendering; and 169 regulatory forms
hand-checked with bounding boxes for 84% of fields. Dataset public, harness
public, identical inputs across systems.

## The caution that has to come first

**This is a vendor benchmark, and the vendor's product wins it.** LlamaExtract
Agentic Plus takes the top slot at 95.6% F1, above Codex/GPT-4.5 at 93.6% and
Claude Code/Opus at 87.1%. That is not an accusation — the methodology above is
more rigorous than most published comparisons, and making the dataset public
invites exactly the checking that would catch a thumb on the scale.

But the base rate for "company publishes benchmark, company wins benchmark" is
what it is, and the honest handling is to treat the **construction** as the
contribution and the **ranking** as unverified. We have been caught by
secondary summaries twice this month; a first-party ranking deserves the same
scepticism.

## The result that matters to us

> On documents **over 50 pages, commercial VLMs drop below 35% recall**, while
> their agentic tier maintains 94.4%. And *"short documents make everyone look
> good"* — eight systems exceed 90% under 10 pages.

This lands directly on **#63** (*Prax cannot LOOK at a PDF page — text layer
only*), which proposed rendering pages for a vision model. Taken at face value,
it says a naive VLM path **inherits a severe length cliff**: the very documents
where our text layer fails worst — long, scanned, dense — are where a
general VLM also collapses.

That does not kill #63; it reshapes it. **Render a specific page on demand,
never the whole document.** A vision call scoped to "page 14, which the text
layer returned empty for" is a bounded question in the regime where VLMs still
work (short context, one page), whereas "read this 200-page PDF with vision"
is the regime measured at sub-35% recall. The coverage banners shipped
2026-08-08 already identify *which* pages are missing, so Prax has exactly the
pointer such a targeted call would need.

That is a genuinely useful input to a queued decision, and it argues for a
smaller build than the one I filed.

## What does not transfer

The headline task is **schema-driven field extraction** — pull these 40 fields
from this form. Prax's PDF use is reading: summarise a paper, capture an
article, answer from a document. The length cliff is measured on the former, so
I am not going to claim it applies unchanged to the latter.

Adding ExtractBench to Prax's eval matrix would be scope creep into a domain we
have not chosen. Per the standing rule from the
[memory survey](agent-memory-survey-2026.md): decline what does not name a gap
we actually have.

## One quiet validation

Grounding — *"whether systems identify supporting evidence"*, scored at value
and record level — is reported as **"an open challenge" across the field**.

Prax's `_extract_pdf_text` emits `--- page N of M ---` markers for exactly this
reason, and the docstring says so: *"an agent quoting a passage should be able
to say where it came from, and 'page 12 of 40' is the difference between a
citation and a vague recollection."* That instinct is cheap, already shipped,
and apparently not universal. Worth knowing; not worth building on.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Reshape #63: render a SPECIFIC page on demand, never the whole document** — VLM recall collapses below 35% past 50 pages, and the coverage banner already names which pages are missing | 📋 updates #63 toward a smaller build |
| ExtractBench as a Prax eval | ❌ declined — schema-driven extraction is not a capability Prax has chosen; scope creep |
| LlamaExtract itself | ❌ declined — a product in an adjacent domain |
