# Pangram Space — what an AI-text detector "sees" (interpretability)

Reference note on **[Pangram Space](https://www.pangram.com/pangram-space)** — an
interpretability project visualizing the internals of *Pangram 3.3.2*, an AI-text-
**detection** model. Entry in the **LLM behavior & interpretability** lane
([README](README.md)); the harness framing there applies — *principles via
output-level proxies, not features* (Prax is an API consumer with no activation
access).

## What it found

Probing 5,120-dim hidden activations (PCA / UMAP / t-SNE + per-layer linear probes):

- **Human vs. AI text separates progressively by depth** — a linear probe reaches
  **1.0 at layer 24**. The distinction is not a surface trick; it's a clean,
  linearly-decodable direction in the model's representation.
- **Model *family* emerges without being trained on family labels** — ~**91%**
  classification from activations alone.
- **"Humanized" AI text occupies a distinct region** — ~**98%** in a three-way
  (human / AI / humanized) split.

## Why it (barely) matters to the harness — and where it actually does

**Direct harness value is thin.** Prax can't read activations, so the
representation-geometry result is, like the [Goodfire manifolds note](llm-emotion-manifolds.md),
a *principle* (models linearly encode high-level properties of text) realized only
through output-level proxies — not a feature to wire. On its own, an AI-detector's
internals are a niche subject for a personal-assistant harness.

**The load-bearing takeaway is the bridge to interoperability.** The robust,
practical fact under the interpretability is: **AI-generated text is reliably
distinguishable from human, the producing model family is identifiable, and
"humanizing" leaves a detectable signature.** For an agent that *produces* content
across channels, the honest response to "your output is detectable" is **not
evasion** — it's **provenance**: attest "produced by Prax / agent X" via content
credentials, so downstream systems *verify* origin instead of guessing. That is
exactly the **content-provenance frontier** in
[`../architecture/interoperability.md`](../architecture/interoperability.md) (the
"Identity & provenance" axis). Interpretability of detection → the case for
provenance over evasion.

## Bottom line

A thin interpretability result by itself (detector internals; no activation access
for Prax) — but it earns its place by **motivating the provenance direction**: AI
content is detectable by design, so Prax should aim to be *verifiably attributable*,
not undetectable. Files cleanly at the seam between the interpretability lane and the
interoperability doc.
