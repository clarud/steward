# Steward Developer Guide

This guide describes the implementation currently in the repository: Phases 0
through 4. Steward can register local Markdown files, extract structured
fragments, and retrieve them using lexical, semantic, or hybrid search. It is
not yet an LLM answering system, a LangGraph application, or a Telegram bot.

## Design principles

Steward's current implementation follows five important rules.

1. Original files are authoritative. Markdown files stay in the user's vault;
   SQLite contains operational metadata and rebuildable derived data.
2. Derived results retain provenance. A retrieval result identifies its
   `SourceFragment`, which identifies its original `Source` and path.
3. Deterministic code performs filesystem and database work. No model decides
   whether a row is written, how a file is read, or what is deleted.
4. Infrastructure is hidden behind small interfaces. For example, services
   depend on `SemanticIndex`, not on a particular vector database.
5. Each layer can be tested without a model, network, Telegram, or LangGraph.

## Repository map

```text
src/steward/
├── config.py                 application settings from environment variables
├── logging.py                logging configuration
├── cli.py                    command-line adapter and composition root
├── storage/
│   └── database.py           SQLite initialization and ordered migrations
├── sources/
│   ├── models.py             Source, SourceType, SourceStatus
│   ├── discovery.py          recursive Markdown path discovery
│   ├── hashing.py            SHA-256 file hashing
│   ├── repository.py         SQLite persistence for Sources
│   ├── scanning.py           source synchronization logic
│   └── service.py            source scan plus derived-index coordination
├── extraction/
│   ├── models.py             SourceFragment and ExtractionResult
│   ├── markdown.py           heading-aware Markdown extraction
│   └── repository.py         fragment and FTS5 persistence
└── retrieval/
    ├── lexical.py            FTS5/BM25 retrieval service
    ├── semantic.py           embeddings and local semantic index
    └── hybrid.py             lexical/semantic rank fusion
```

`tests/` mirrors these areas. Unit tests use temporary databases and vaults,
so they never alter a user's actual vault.

## Application configuration and startup

`Settings` in `config.py` is an immutable dataclass. `Settings.from_environment()`
reads optional environment variables such as `STEWARD_DATA_DIR` and creates a
single settings object for a command invocation.

The default operational data directory is `.steward/`, which can contain:

```text
.steward/
└── steward.db
```

The CLI is an adapter, not a domain service. It parses command-line arguments,
constructs repositories and services, calls a use case, and prints results.
It does not contain scanning, extraction, or search rules itself. This makes
the same services reusable from a future Telegram adapter or LangGraph node.

## Source registry

### Source model

A `Source` represents an original file known to Steward. Its important fields
are:

```text
id             SQLite identity; assigned only after persistence
path           resolved absolute location of the original file
content_hash   SHA-256 fingerprint of its current bytes
source_type    currently Markdown
size_bytes     current file size
modified_at    file timestamp reported by the filesystem
first_seen_at  when Steward first registered it
last_seen_at   when Steward most recently observed it
status         active or missing
```

The filename is useful location metadata but is not source identity by itself.
Two files can have different paths and identical hashes, which means they have
duplicate content but may still be intentionally kept in two locations.

### Discovery and hashing

`discover_markdown_files(root)` validates the supplied root, recursively uses
`Path.rglob("*")`, keeps files with the `.md` suffix, resolves them to absolute
paths, and returns them in deterministic sorted order.

`sha256_file(path)` streams a file in 64 KiB chunks into `hashlib.sha256()`.
Chunking avoids loading an entire large file into memory. The hexadecimal
digest is a 64-character representation of the file's SHA-256 hash.

### Scan lifecycle

`scan_markdown_root(root, source_repository)` compares the observed filesystem
against active rows in `sources`.

```text
vault file
  ↓ discover path
filesystem metadata + SHA-256
  ↓
existing Source at path?
  ├── no  → add new active Source
  ├── yes and metadata/hash changed → replace its current metadata
  └── yes and unchanged → refresh last_seen_at
  ↓
active database Source absent from discovery → mark missing
```

The operation is idempotent: scanning unchanged files again does not create
additional `Source` rows. This matters because scans will eventually happen
after captures, scheduled maintenance, and restarts.

`SourceRepository` is the boundary that owns SQL for this table. Code outside
the repository works with `Source` objects rather than SQL row tuples.

## Markdown extraction

### Fragments and provenance

`MarkdownExtractor` turns one persisted Markdown `Source` into an
`ExtractionResult` containing ordered `SourceFragment` objects.

A fragment stores:

```text
id         assigned when stored
source_id  the original Source it came from
heading    Markdown heading, if any
ordinal    zero-based order inside the source
text       extracted Markdown text
location   human-readable line range, for example "lines 9-12"
```

The extractor treats headings as structural boundaries. For example:

```markdown
# Virtual Memory
Introduction text.

## TLB
A TLB caches translations.
```

becomes fragments approximately like:

```text
ordinal 0 | heading "Virtual Memory" | lines 1-3
ordinal 1 | heading "TLB"            | lines 4-5
```

This is deliberately different from fixed-length chunks. A heading and line
range give a future answer a useful, inspectable citation.

### Replacement rather than append

`SourceFragmentRepository.replace_for_source(result)` deletes every old
derived fragment for one source, then inserts its current extraction result.
It also rebuilds that source's FTS5 entries in the same database transaction.

Fragments are derived data. Replacing them is safer than attempting a fragile
line-by-line update algorithm, because the original Markdown remains available
to rebuild from. It also prevents stale fragments from being returned after a
file has been edited.

## SQLite and migrations

`initialize_database(database_path)` creates the database directory and keeps
a `schema_migrations` ledger. Each migration has an integer version. On every
startup, Steward checks the ledger and runs only migrations that have not
already been recorded.

Current schema progression:

```text
1  schema_migrations
2  sources
3  source_fragments
4  source_fragments_fts (SQLite FTS5 virtual table)
5  source_fragment_embeddings
```

This means an existing Phase 3 database is upgraded by applying only migration
5. It is not recreated, and original source metadata is not discarded.

SQLite is appropriate at this stage because it is local, transactional,
inspectable with standard tools, and needs no separately operated server.

## Lexical retrieval

### FTS5

SQLite FTS5 is a full-text search extension. It builds an inverted index:

```text
term "translation"
  → fragment 7, fragment 19, fragment 31
```

That is much faster than reading every Markdown file during each query.

`SourceFragmentRepository.search(query)` executes a parameterized `MATCH ?`
query against the FTS5 table. It joins matching rows back to
`source_fragments` and returns a BM25 score.

`LexicalSearchService` adds the original `Source`, yielding:

```text
LexicalSearchHit
├── source
├── fragment
└── score
```

FTS5 BM25 scores rank exact token matches. With SQLite's FTS5 implementation,
lower (often more-negative) BM25 scores are generally better, so results are
ordered ascending in SQL.

Lexical retrieval is strong for exact names, acronyms, filenames, and rare
technical words. It is weaker for paraphrases because it does not understand
that "translation cache" might mean "TLB".

## Semantic retrieval

### Embeddings

An embedding model maps text to a vector of numbers. The selected local model,
`sentence-transformers/all-MiniLM-L6-v2`, produces 384-dimensional vectors.
Texts with similar learned meaning generally point in similar vector
directions.

The application uses two small interfaces:

```python
class EmbeddingProvider(Protocol):
    def embed_documents(self, texts): ...
    def embed_query(self, query): ...

class SemanticIndex(Protocol):
    def replace_for_source(self, fragments): ...
    def search(self, query, *, limit=5): ...
```

The current provider is `SentenceTransformerEmbeddingProvider`. It loads the
model from the local cache during normal operation. The explicit
`download-embedding-model` command is the only supported path that allows an
initial download, avoiding an unexpected network request during indexing.

### Index storage

`SQLiteSemanticIndex` stores one derived vector per current fragment:

```text
source_fragment_embeddings
├── fragment_id   foreign key to source_fragments.id
├── model_name    prevents mixing model outputs
├── dimension     prevents comparing incompatible vector lengths
└── vector_json   JSON array of floating-point values
```

The vectors are stored as JSON rather than a binary format because the initial
implementation prioritizes transparency and inspectability. They are derived
data: deleting this table does not lose user knowledge because running
`steward index <vault>` rebuilds it.

When fragments are replaced after a source edit, the foreign key's
`ON DELETE CASCADE` removes their old embeddings. The semantic index then
embeds and stores the newly persisted fragments.

### Similarity search

For a query, the index embeds the query using the same provider, reads vectors
created by the same model and dimension, and calculates cosine similarity:

```text
cosine similarity
= dot product of vectors
  ÷ (length of first vector × length of second vector)
```

Scores close to `1` mean similar vector directions; scores closer to `0` are
less related. The results are sorted descending.

`SemanticSearchService` then resolves each result's `source_id` through
`SourceRepository`. This preserves the full evidence chain:

```text
SemanticSearchHit
  → SourceFragment
  → Source
  → original Markdown path and line location
```

## Hybrid retrieval

`HybridRetriever` calls both `LexicalSearchService` and
`SemanticSearchService`, then combines their ranks using reciprocal-rank fusion
(RRF).

```text
contribution for rank r = 1 / (60 + r)
```

The constant 60 softens the effect of any single ranking. A fragment found by
both methods receives two contributions and normally rises above a result
found by only one.

RRF is used instead of adding raw scores because BM25 and cosine similarity
are not comparable measurements:

```text
BM25: lower score is better in this SQLite query
cosine: higher score is better
```

A `HybridSearchHit` retains the final fused score plus the individual lexical
and semantic scores. The fused score is a ranking value, not a probability or
confidence statement.

## Commands and data flow

```powershell
# Register sources, extract fragments, and build lexical FTS5 entries.
steward scan path\to\vault

# Explicitly download the local semantic model once.
steward download-embedding-model

# Scan, extract, and build the semantic index.
steward index path\to\vault

# Search in one mode.
steward search "address translations"
steward semantic-search "little cache CPUs use for address translation"

# Combine both modes.
steward hybrid-search "little cache CPUs use for address translation"
```

The full `index` flow is:

```text
CLI
  ↓ constructs repositories, extractor, provider, and index
SourceService.scan_markdown_root
  ↓
filesystem discovery + SHA-256 + SourceRepository
  ↓
MarkdownExtractor
  ↓
SourceFragmentRepository
  ├── source_fragments
  └── source_fragments_fts
  ↓
SQLiteSemanticIndex
  └── source_fragment_embeddings
```

The full hybrid search flow is:

```text
query
  ├── FTS5 MATCH → BM25-ranked lexical fragments
  └── embedding model → cosine-ranked semantic fragments
        ↓
reciprocal-rank fusion
        ↓
fragments with source path, heading, and line provenance
```

## Testing strategy

Tests live under `tests/` and use `tmp_path`, which creates isolated temporary
directories and SQLite databases for each test.

Semantic tests use `FakeEmbeddingProvider`, a small deterministic provider,
instead of loading the real model. This makes the tests fast, reproducible,
offline, and focused on Steward's own behavior rather than a third-party model.

The test suite currently covers source discovery, hashing, idempotent scans,
missing files, fragment extraction, repository behavior, FTS5 search, schema
migrations, semantic persistence, semantic retrieval, stale-embedding
replacement, hybrid rank fusion, and lexical retrieval evaluation cases.

Run it with:

```powershell
pytest
```

## Known limitations

- Only Markdown is supported. PDFs, DOCX, HTML, plain text, images, Telegram
  attachments, and other source types are future work.
- Heading-based fragments are useful but not universally optimal. Very long
  sections can create overly large fragments; very short headings can create
  too little context.
- SQLite semantic search currently reads all stored vectors for the selected
  model and calculates cosine similarity in Python. It is suitable for a small
  personal vault, but will become slow as fragment counts grow.
- Vectors are stored as human-readable JSON. This is not as compact or fast as
  binary vectors or a dedicated vector index.
- Changing the embedding model requires re-indexing because vectors from
  different models live in incompatible semantic spaces.
- Semantic similarity is not proof. It can return a plausible but irrelevant
  fragment, especially for short or ambiguous queries.
- FTS5 is token-based. It does not automatically stem every grammatical form,
  so `cache` may not match `caches` with the current tokenizer configuration.
- Hybrid search uses the user's query in FTS5 as well as semantic search;
  malformed FTS5 syntax can therefore reject an otherwise meaningful query.
- There is no LLM answer generation, citations in final prose, conversation
  memory, LangGraph workflow, access policy, or external action support yet.
- The current CLI constructs services directly. As the application grows, a
  dedicated composition module or dependency-injection approach may improve
  startup composition.

## Good future improvements

### Retrieval quality

1. Build a real retrieval evaluation set from your own questions and measure
   Recall@K and MRR for lexical, semantic, and hybrid search.
2. Experiment manually with heading, paragraph, fixed-size, and overlapping
   chunk strategies before changing the default extractor.
3. Improve FTS5 query construction so ordinary punctuation and natural
   language cannot accidentally become invalid FTS syntax.
4. Add snippets with query-term highlighting and include explicit scores in a
   diagnostic search mode.
5. Add metadata filters such as source type, path prefix, workspace, or date
   once those domains exist.

### Scale and storage

1. Add a `rebuild-semantic-index` command that removes and regenerates all
   vectors for a selected model.
2. Store compact binary vectors if JSON size becomes material.
3. Implement another `SemanticIndex`, backed by a local vector engine, when
   full scanning is measurably too slow. The existing protocol is the intended
   replacement seam.
4. Record the model revision and embedding configuration alongside vectors,
   not just the model name.

### Product progression

1. Phase 5: add `ModelGateway`, `ContextBuilder`, and `AnswerService` so only
   retrieved fragments are sent to an LLM and answers include provenance.
2. Phase 6: use LangGraph to orchestrate a minimal `retrieve → answer` graph
   while keeping all domain logic in the services documented here.
3. Add richer extractors for plain text and PDF before introducing broad
   capture channels.
4. Add structured retrieval evaluations before relying on semantic results for
   important personal records or actions.

## Practical debugging

Use these commands while developing:

```powershell
pytest
steward --help
steward sources
steward search "exact term"
steward semantic-search "meaning-based phrase"
```

For database inspection, use any SQLite client to examine:

```text
schema_migrations
sources
source_fragments
source_fragments_fts
source_fragment_embeddings
```

Never manually edit derived index rows as a normal workflow. Fix the source or
the deterministic extraction/indexing code, then rebuild the derived data.
