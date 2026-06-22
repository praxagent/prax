# Open Knowledge Format (OKF) — and what Prax should assimilate

[← Research](README.md)

> **Reference note.** Source: Sam McVeety & Amir Hormati (Google Cloud), *"Introducing the
> Open Knowledge Format,"* Google Cloud blog, 2026-06-12 —
> <https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/>
> · spec/impl: `GoogleCloudPlatform/knowledge-catalog/tree/main/okf`
>
> This note summarises OKF, maps it against how Prax actually stores knowledge today
> (evidence-checked against the repo), and records the one thing genuinely worth assimilating:
> **OKF as an interchange format, not an internal model.**

## What OKF is (one screen)

OKF v0.1 represents knowledge as **"a directory of markdown files with YAML frontmatter, with
a small set of agreed-upon conventions."** The pitch: *"The answer to this problem isn't another
knowledge service. You need a **format**…"* — vendor-neutral, no SDK, human- and agent-readable.

- **One file = one "concept"** (a table, dataset, metric, runbook, API, playbook…). The file
  path is the concept's identity.
- **Frontmatter** carries the few queryable fields; **exactly one is required: `type`**. The
  others (`title`, `description`, `resource`, `tags`, `timestamp`) are optional. Everything
  else — what types exist, what sections the body has — is left to the producer. *"The spec
  defines the interoperability surface, not the content model."*
- **Cross-links** are ordinary markdown links, which **"turn the directory into a graph of
  relationships"** richer than the folder hierarchy.
- **Optional `index.md`** per directory for *progressive disclosure*; **optional `log.md`** for
  change history.
- **Producer/consumer independence:** *"The format is the contract; the tooling at each end is
  independently swappable."* A bundle hand-authored, pipeline-generated, or LLM-synthesised is
  consumable by a visualiser, an agent, or another LLM. Ships as a tarball / git repo / mounted
  filesystem.
- Deliberately **minimal / anti-ontology**: no RDF/OWL/JSON-LD/SPARQL, **no vector embeddings**,
  no schema validation, no graph query language. Inspired by Karpathy's "LLM wiki" gist, Obsidian
  vaults, and Hugo.

The design bet: LLMs are good at exactly the bookkeeping (touch 15 files, keep cross-references
current) that makes humans abandon personal wikis — so give agents *"a shared markdown library
that grows more useful over time"* and curate it like code.

## How Prax already compares

Prax runs **two parallel knowledge systems**, and they sit on opposite sides of OKF:

| OKF property | Prax reality (evidence) | Closeness |
|---|---|---|
| Directory of markdown files, one file = a concept | Library notes at `spaces/{s}/notebooks/{n}/{slug}.md` (`library_service.py:169-182`) | Close (hierarchical, not flat) |
| YAML frontmatter per file | Yes on Library notes/raw/archive (`_serialize_frontmatter`, `library_service.py:221`); **no** on `user_notes.md`/`.progress.md`/logs | Partial |
| Required `type` field | **Absent** — notes use `tags` + a 2-state `status`; `kind` exists only on *spaces*/raw/archive (`library_service.py:317`) | Missing |
| Cross-links → implicit graph | `[[wikilinks]]` (`extract_wikilinks`, `library_service.py:236`), stored in frontmatter | Close, different syntax |
| optional `index.md` | Auto-generated `library/INDEX.md` TOC, rebuilt on every mutation (`rebuild_index`, `library_service.py:1849`) | Present |
| optional `log.md` | Per-space `.progress.md` + on-demand `.progress/` detail (`progress_service.py:37`); `self-improvement-log.md` | Present |
| Human **and** agent consumption | Explicit `author`/`prax_may_edit` on notes; `AGENTS.md` map (progressive disclosure) | Strong |
| Portable plain files | Whole workspace is git-backed plain markdown (`ensure_workspace` → `git init`, commits on every write) | Library: yes |

**The split:** the **Library** already embodies most of OKF — markdown + frontmatter + an
index + a log + progressive disclosure + git portability — independently arriving at the same
shape. But the **second** store, the Neo4j `KnowledgeConcept` graph (`knowledge_graph.py`), is
conceptually *more* OKF-graph-shaped (typed concepts + typed relations + importance + source)
yet lives **in a database with no file representation and no export at all** (grep for
export/serialize/to_markdown in `knowledge_graph.py` → nothing). And there is **no
knowledge-bundle export anywhere** in the repo.

## What's worth assimilating — and what isn't

**Honest framing: OKF is *weaker* than Prax's internal model, so it is not an internal-model
upgrade.** Prax's knowledge graph has dense+sparse vector hybrid retrieval, weighted/bi-temporal
relations, and cross-graph entity links — OKF has none of that (no embeddings, no relation
weights, no query language). Adopting OKF *as Prax's representation* would be a downgrade. The
note's own "what's conspicuously absent" list (no embeddings, no validation, no graph query)
is exactly what Prax already has.

**Where OKF clearly wins, and Prax should assimilate it: portability / interoperability.** OKF's
real value is being *"the contract"* — a lossless, vendor-neutral, plain-files form that any
producer or consumer can exchange. Prax's knowledge is currently trapped: the graph is Neo4j-only,
and nothing emits a portable bundle. That's the gap OKF fills, and it lines up with Prax's own
"plug-and-play / runs anywhere" ethos (the same reason the sandbox was carved into its own repo).

### Why not pure-OKF — what an internal switch would cost (evidence-checked)

"Is our way superior, and would going pure-OKF throw things away?" was answered by a 5-agent
audit that checked each capability against **shipped code** (aspirational-doc claims excluded).
The answer is axis-dependent — and "replace the internal store with OKF files" is a real
downgrade. Superiority by axis:

| Axis | Winner | Evidence |
|---|---|---|
| Knowledge representation | **Prax** | edge `weight`+`evidence` that **accumulate on repeat** (`knowledge_graph.py:351-358`), typed relations, cross-graph `REFERENCES_ENTITY` join (`:393-396`), bi-temporal `valid_from`/`valid_until` + history-preserving supersession (`graph_store.py:178-251`, `consolidation.py:175-183`) — OKF edges are bare links with no properties |
| Retrieval | **Prax** | dense+sparse+graph weighted RRF + intent weighting (`retrieval.py:38-206`), hybrid concept search (`knowledge_vectors.py`), query expansion + LLM-rerank, per-user/namespace/importance scoping — OKF has no embeddings or query layer |
| Memory dynamics | **Prax** | time + interaction decay taking the stronger signal (`vector_store.py:399-408`), reinforce-on-recall (`:211-239`, live at `retrieval.py:108`) — OKF files are static |
| Portability | **OKF** | zero-dependency plain files; pre-bridge the concept graph lived only in Neo4j and writes evaporated when the DB was down (`graph_store.py:377-379`) |
| Human-legibility / interchange | **OKF** | render-anywhere markdown; external producers need no DB — the Neo4j graph needs Cypher/bespoke tooling |

**Lost entirely under pure-OKF** (unrepresentable — confirmed in code): per-edge weights +
accumulation + evidence; the cross-graph `REFERENCES_ENTITY` join; bi-temporal supersession
(OKF would force delete/overwrite, losing history); decay + reinforce-on-recall (no counters, no
jobs); and the whole hybrid-retrieval stack (a consumer would rebuild the embedder, both vector
indexes, fusion, and rerank — OKF gives you grep).

**Lossy:** typed relations and `EXTRACTED_FROM` provenance collapse to generic links; per-user/
namespace/importance *metadata* survives in frontmatter but nothing **enforces** filters or ranks
by it. **Survives cleanly:** `importance`/`source`/`type` (frontmatter) and the wikilink graph
(it *is* OKF's mechanism). **Library fields** (`prax_may_edit`, `status`, `lesson_order`, …) can
sit in arbitrary frontmatter, but their *behavior* — permission gating, status→progress rollups,
ordering, auto-rebuilt `INDEX.md`, bounded+LLM-compacted `.progress.md` — is Prax code OKF doesn't
run: you keep the bytes, lose the behavior.

Two honest caveats: "Ebbinghaus" decay is a label over a standard exponential half-life (no
distinct retention formula); and bi-temporal/decay live on the **conversational Entity graph**
(`graph_store.py`), not the `KnowledgeConcept` graph — so OKF export of the *knowledge* graph
specifically loses less, though it still can't carry that graph's weighted/typed/evidenced/
cross-graph edges or its vector-hybrid retrieval.

**Conclusion:** keep Neo4j+Qdrant as the system of record (the reasoning substrate); expose OKF
only as the interchange surface (the transport format). That's the shipped design — not a
compromise but the correct split.

### Assimilation status

1. **OKF export/import bridge for the knowledge graph** — ✅ **SHIPPED** (2026-06).
   - `prax/services/memory/okf_bridge.py` is the pure format layer: `write_bundle()` emits one
     markdown file per concept (frontmatter `type` from `source_type`, `title`, `description`,
     `resource`, `timestamp`, `tags`; body holds the description + a `# Relations` section of
     markdown links) plus an auto-generated `index.md` and `log.md`; `read_bundle()` parses a
     bundle back into concept records + relation edges (recovering the relation type from the
     `Relations` bullets).
   - `knowledge_graph.export_namespace_okf()` / `import_okf()` wire it to Neo4j via new
     `list_concepts` / `list_relations` readers and the existing `add_concept` /
     `add_knowledge_relation` writers — so import is vector-indexed too and `knowledge_ingest`
     is now effectively **round-trippable**.
   - Agent tools `knowledge_export_okf(namespace, dest)` / `knowledge_import_okf(path, namespace)`
     (workspace-confined paths via `safe_join`, git-committed). Prax knowledge is now portable
     plain files, interoperable with Google's enrichment agent / static visualiser / any OKF tool.
   - Honest scope: OKF is an *interchange* surface — the Neo4j+Qdrant model stays canonical;
     export is lossy on the bits OKF can't carry (relation weights/evidence, embeddings,
     bi-temporal edges).

2. **Make the Library an OKF-conformant bundle** *(cheap interop win — not yet built)*. The Library is already
   markdown + frontmatter + index + log; it's one convention short of OKF. Add an optional `type`
   field to note frontmatter (default e.g. `"note"`/the space `kind`) and an OKF *view/adapter*
   that maps `[[wikilinks]]` → markdown links on export. Then a Library space *is* a browsable
   OKF bundle (renderable by OKF's static visualiser, indexable by any OKF consumer) with near-zero
   internal change.

3. **OKF as the harness↔sandbox/remote knowledge interchange** *(speculative)*. Now that
   prax-sandbox is standalone and remote-capable, an OKF bundle is a natural, dependency-free way
   to ship curated knowledge to a remote sandbox or between harnesses — no shared DB required.

**What to deliberately skip:** replacing the Neo4j/Qdrant model with OKF; downgrading note
frontmatter to OKF's single required field; adopting OKF's flat-directory identity over Prax's
space/notebook hierarchy. Keep the rich internal model; expose OKF only at the boundary.

## See also

- [Memory & Knowledge](../infrastructure/memory.md) — the Neo4j `KnowledgeConcept` graph + hybrid
  concept search (the export source).
- [The Library](../library.md) — markdown notes + frontmatter + `INDEX.md` + wikilinks (already
  ~OKF).
- [Harness Engineering §30](harness-engineering.md) — "repository as system of record" +
  progressive disclosure via `AGENTS.md`, the same instinct OKF formalises.
- [Reliable Agentic Systems (Bayer/PRINCE)](reliable-agentic-systems-bayer.md) — the prior
  external-reference note + its grounding/citation themes.
