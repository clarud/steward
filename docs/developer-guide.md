# Steward Developer Guide

This guide describes the implementation currently in the repository: Phases 0
through 20. Steward can register local Markdown files, extract structured
fragments, retrieve them using lexical, semantic, or hybrid search, and
generate grounded answers from retrieved fragments. A minimal LangGraph
workflow orchestrates those existing services. A Telegram adapter can deliver
isolated text questions to that application flow.

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
├── retrieval/
    ├── lexical.py            FTS5/BM25 retrieval service
    ├── semantic.py           embeddings and local semantic index
    └── hybrid.py             lexical/semantic rank fusion
└── answer/
    ├── models.py             answer, citation, and context value objects
    ├── context.py            bounded evidence prompt construction
    ├── gateway.py            model-provider boundary and OpenAI gateway
    └── service.py            retrieve → context → grounded answer workflow
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

## Grounded answers

Phase 5 turns retrieval into the first half of a RAG flow:

```text
question
  ↓
HybridRetriever finds source fragments
  ↓
ContextBuilder formats only those fragments
  ↓
ModelGateway generates an answer from that bounded context
  ↓
AnswerResult contains answer text and source citations
```

### AnswerService

`AnswerService` coordinates the flow but does not read files, construct SQL,
or call a provider-specific SDK directly. It depends on three capabilities:

```text
Retriever       → ranked HybridSearchHit values
ContextBuilder  → an exact prompt plus citations
ModelGateway    → generated text from supplied instructions and input
```

If retrieval returns no evidence, `AnswerService` returns the deterministic
message `I don't have enough local information to answer that.` It does not
call the model. This avoids spending money or producing a plausible answer
without local support.

### ContextBuilder

Each selected fragment is labelled with a stable request-local key such as
`[F1]`, followed by the source path, heading, line location, and excerpt.
`AnswerContext.prompt` is the exact text sent to the model, while
`AnswerContext.citations` holds the matching structured metadata.

Context is capped at 12,000 characters by default. If an excerpt would exceed
the remaining deterministic budget, it is cut and marked `[truncated]`; if a
new excerpt cannot fit meaningfully, it is excluded. This prevents an unusually
large Markdown section from silently consuming the whole model context window.

### ModelGateway, Gemini, and OpenAI gateways

`ModelGateway` is a protocol with one operation:

```python
generate(*, instructions: str, input_text: str) -> str
```

`GeminiModelGateway` is the default CLI provider. It lazily imports the
official `google-genai` SDK and calls Gemini's Interactions API with
`system_instruction`, `input`, and `store=False`. `OpenAIModelGateway` remains
available as an alternative and calls the OpenAI Responses API with `store=False`.
Provider-specific request and response details remain in these gateway classes,
so a future local model or another provider can implement the same protocol.

The grounding instruction tells the model to use only supplied excerpts, treat
those excerpts as untrusted reference material rather than instructions, state
when evidence is insufficient, and cite fragment keys such as `[F1]`. This
reduces hallucination and prompt-injection risk but does not prove an answer is
correct; the original source remains the authority.

To use the default Gemini command, set these environment variables rather than
placing a secret in tracked code:

```powershell
$env:GEMINI_API_KEY = "your-api-key"
$env:STEWARD_GEMINI_MODEL = "your-selected-model"
steward ask "What do I know about address translation?"
```

`GEMINI_API_KEY` and `OPENAI_API_KEY` are intentionally not part of the logged
`Settings` dataclass. `STEWARD_MODEL_PROVIDER` defaults to `gemini`; use
`STEWARD_MODEL_PROVIDER=openai` with `STEWARD_OPENAI_MODEL` to use the OpenAI
gateway instead. The selected provider's model setting is optional until
`steward ask` is used.

## LangGraph orchestration

Phase 6 introduces LangGraph without moving Steward's domain logic into the
graph. `build_retrieval_answer_graph()` compiles this small workflow:

```text
START
  ↓
retrieve
  ↓
evidence found?
  ├── yes → answer → END
  └── no  → no_evidence → END
```

The graph's shared `RetrievalAnswerState` contains:

```text
question                the user request
retrieved_fragment_ids  compact IDs of evidence found by retrieval
answer                  generated or deterministic no-evidence answer
citations               provenance for evidence sent to the model
```

It also carries `retrieved_hits` as transient in-memory working state. The
answer node needs the small retrieved fragment objects to construct context
without repeating the retrieval query. `retrieved_fragment_ids` remains the
important compact representation for inspection and future persistence.

The nodes are ordinary Python functions created inside the graph builder:

```text
retrieve
  calls HybridRetriever.search(question, limit=...)
  writes retrieved hits and fragment IDs into state

has_evidence
  conditional edge function
  routes to answer or no_evidence based on the IDs

answer
  calls AnswerService.answer_from_hits(...)
  writes answer text and citations into state

no_evidence
  calls the same service with no hits
  writes the deterministic no-evidence answer
```

`AnswerService.answer_from_hits()` was extracted from `ask()` so the graph can
perform retrieval exactly once. `ask()` still works as a non-graph convenience
method: it retrieves, then delegates to `answer_from_hits()`.

The CLI now composes and invokes this graph for `steward ask`. LangGraph owns
only state transitions and routing; `HybridRetriever`, `ContextBuilder`, and
the model gateways still own their existing responsibilities.

## Telegram adapter

Phase 7 adds a transport boundary; it does not add conversation memory or
Telegram capture. A Telegram message moves through the system as follows:

```text
Telegram Update
  ↓
normalize_telegram_update
  ↓
IncomingEvent
  ↓
StewardQuestionApplication.handle
  ↓
compiled retrieval-answer graph
  ↓
answer text
  ↓
TelegramAdapter.reply_text
```

`IncomingEvent` in `events.py` is deliberately platform-neutral. Its stable
event ID, platform, chat ID, message ID, optional reply-to ID, timezone-aware
timestamp, text, and attachment placeholders describe what arrived without
leaking Telegram objects into application code. Future adapters can construct
the same event shape.

`normalize_telegram_update()` is the adapter's deterministic translation step.
For example, Telegram update `42` from chat `100` becomes the event ID
`telegram:42` and chat ID `"100"`. A reply retains the message ID it replied
to, which will matter when Phase 8 adds reference resolution.

`StewardQuestionApplication` is the small application use case. It knows only
that it receives normalized text and that the graph accepts `{"question":
text}` and returns an `answer`; it has no Telegram dependency. Empty text gets
a deterministic instruction rather than invoking retrieval. When the graph
returns citations, it appends the same `[F1] path:location [heading]` source
details as the CLI so citation keys in a Telegram answer remain inspectable.

The library's handler is asynchronous, while the current retrieval graph and
model gateway are synchronous. `TelegramAdapter.handle_update()` uses
`asyncio.to_thread(...)` to run the application call in a worker thread, then
awaits `reply_text`. This keeps Telegram's event loop free to receive updates
while an answer is being generated. It is a pragmatic Phase 7 bridge, not a
claim that the retrieval services are fully async.

`run_telegram_polling()` is the only place that constructs
`ApplicationBuilder`, registers `MessageHandler(filters.TEXT &
~filters.COMMAND, ...)`, and calls `run_polling()`. Long polling is appropriate
for this local-first version because the bot opens an outgoing connection to
Telegram rather than requiring a public webhook server. The process remains
running until `Ctrl+C` stops it.

Configure the private bot token and run the adapter:

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
```

```powershell
steward telegram
```

## Conversation state and checkpoints

Phase 8 gives each Telegram chat a LangGraph thread ID such as
`telegram:100`. The application invokes the graph with that ID, and the local
SQLite checkpointer stores a snapshot after each graph step in
`.steward/checkpoints.db`. Restarting the process and invoking the same thread
loads its prior state.

The state keeps a bounded ten-message exchange, recent source fragment IDs,
and placeholders for workspace, concepts, and records. It does not store full
documents. For a small deterministic first reference resolver, a follow-up
containing terms such as `that`, `this`, or `it` is expanded with the preceding
user question before retrieval. This is useful but deliberately conservative:
it is not yet general natural-language reference resolution.

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
steward ask "What do I know about address translation?"
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

## Capture, organization, knowledge, and records

Phases 9–20 add the first durable personal-information loop while preserving a
strict difference between originals and derived data.

`InboxCaptureService` copies a saved Telegram message or attachment into the
Inbox, hashes and registers it as a `Source`, chooses a type-specific extractor,
persists `SourceFragment` rows, and appends a `SOURCE_CAPTURED` activity event.
The original file is canonical; fragments and embeddings can be rebuilt.

`IntentResolver` routes deterministic signals first: a `/save` command or an
attachment means capture, while `/delete`, `/organize`, and `/inspect` map to
their corresponding intents. `WorkspaceService` creates explicit workspaces
and `WorkspaceRepository` stores the many-to-many `workspace_sources` links.
`OrganizationService` only creates a proposal. Every proposal explicitly has a
type (`move_to_workspace` or `keep_in_inbox` in this first implementation), a
confidence, rationale, optional workspace, and optional suggested path.
`OrganizationApprovalService`
is the sole approval boundary: accepting through the CLI or a resumed graph
uses `FileMutationService` to move the registered file, update its stored path,
and write an activity event; rejection leaves the source unchanged. Repeating
an already accepted/rejected decision is a no-op, which makes retry recovery
safe.

The first LangGraph approval graph demonstrates a durable pause with
`interrupt()` and later resume. On acceptance it calls that same approval
service, so a resumed workflow executes the move rather than merely changing a
proposal status. Wiring the paused approval conversation into Telegram is a
later transport step.

The knowledge model starts deliberately small: concepts have aliases, claims
point to supporting source fragments, and enrichment proposals classify new
evidence as confirm, extend, refine, qualify, or contradict. These are
evidence-backed proposals, not automatic truth changes.

`RecordService` adds `TravelRecord` as the first concrete record. It proposes
flight fields from source fragments and tracks the fragment that supports each
extracted field. `steward propose-travel-record SOURCE_ID` is read-only;
`steward create-travel-record SOURCE_ID` is the explicit persistence step, and
both the record and all of its evidence rows are inserted in one transaction.
`FileMutationService.undo_move()` supplies rollback data and writes its own
`SOURCE_MOVE_UNDONE` activity event, preserving the history of reversible
filesystem changes.

## Read-only tool agent

Phase 21 adds the first actual tool-choosing loop without using a generic
prebuilt agent. `ReadOnlyToolService` adapts ordinary services into six
JSON-returning tools: `search_sources`, `read_source`, `search_knowledge`,
`search_records`, `search_workspaces`, and `search_activity`. They have no
mutation capability.

`build_tool_agent_graph()` defines the LangGraph sequence explicitly:

```text
START → model → tool calls requested?
                    ├─ no  → END
                    └─ yes → ToolNode → model
```

The `messages` state uses LangGraph's `add_messages` reducer, so each model
message and `ToolMessage` is appended rather than replacing prior context.
`GeminiToolCallingModel` translates Gemini function-call responses into
LangChain `AIMessage.tool_calls`; `ToolNode` executes only a supplied tool and
then returns its result to the next model turn. `steward agent QUESTION` uses
this graph with a persistent thread ID and an eight-step recursion cap.

## Tool risk policy

Phase 22 makes the safety properties of each tool explicit in `ToolDefinition`:
`side_effects`, `risk`, `idempotency`, `external_system`, and
`requires_approval`. `ToolRisk` distinguishes `READ_ONLY`, `SAFE_WRITE`,
`SENSITIVE_WRITE`, and `DESTRUCTIVE` operations.

`ToolPolicy` is not prompt text. `build_tool_agent_graph()` supplies it to
`ToolNode` through `wrap_tool_call`, where every requested tool is checked
immediately before execution. A denied call becomes a `ToolMessage` explaining
why it was not run; the Python callable is never invoked. The current six
agent tools are all registered as read-only, so no approval state is needed
yet. Future calendar and filesystem tools must receive a policy definition
before they can be included in an agent graph.

## Google Calendar read boundary

Phase 23 adds `CalendarService`, which calls Google Calendar directly for
`search()` and `get_event()` rather than maintaining a stale local copy.
`CalendarEvent` keeps Google's external ID, summary, current start/end values,
and optional HTML link. All-day dates remain dates rather than invented
midnight timestamps.

`authorize_google_calendar()` uses an OAuth installed-app flow with the
read-only Calendar scope. It loads and refreshes an existing local token when
possible; otherwise it opens a local browser consent flow and writes the token
under `.steward/config/`. The OAuth client JSON and token are private local
credentials and must never be committed.

CLI commands are `steward calendar-authorize`, `calendar-search`, and
`calendar-get`. `--include-calendar` deliberately opts Calendar read tools into
the tool agent, avoiding surprise browser authorization during ordinary local
questions. Calendar tools are declared as `READ_ONLY` external tools in the
same policy registry as local tools.

## Calendar writes and idempotency

Phase 24 adds the narrower `CalendarWriteService` rather than allowing callers
to insert arbitrary Google API payloads. It accepts a persisted `TravelRecord`
with departure and arrival times, constructs a titled event, and requests the
Google Calendar event scope. A local `calendar_event_links` row maps
`travel-record:ID` to Google's external event ID. A repeat request looks up
that link and reads the existing event instead of inserting a duplicate.

After an insert, `CALENDAR_EVENT_CREATED` is appended to the activity log. The
filesystem/database transaction cannot span Google's API, so this is
"exactly-once-ish": a completed local link is reliably idempotent, while a
process crash after Google accepts an event but before the link is saved remains
a recoverable operational edge case to test in Phase 32.

## Ephemeral external research

Phase 25 adds `ResearchService` and `GeminiGoogleSearchProvider`. The provider
uses Gemini's Google Search grounding only for an explicit `steward research`
request and returns a `ResearchBundle`: question, generated answer, and unique
web sources. Its retention status is always `ephemeral`; it does not write a
`Source`, fragment, concept, claim, or embedding. A future explicit “keep those
sources” workflow must route selected material through normal capture and
provenance processing rather than bypassing the Source layer.

## Emerging workspace detection

Phase 26 provides the explicit `steward review-inbox-workspaces` workflow.
`WorkspaceDetectionService` considers only active sources physically in Inbox,
splits meaningful filename terms, removes generic words, groups repeated terms,
and ignores an already existing workspace name. Two or more sources sharing a
term produce a pending `WorkspaceProposal` with source IDs, rationale, and a
conservative confidence score. It does not create a workspace, link sources,
or move files; those remain deliberate user actions.

## Known limitations

- Capture currently supports Markdown, plain text, and PDFs with native text.
  DOCX, HTML, images, OCR for scanned PDFs, and large-file relay storage are
  future work.
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
- Hybrid search falls back to semantic retrieval if a natural-language query
  contains punctuation that FTS5 rejects. This prevents a parser error, though
  it means no lexical candidates contribute to that particular ranking.
- Model-generated citations use request-local keys such as `[F1]`. Steward
  currently returns metadata for all context fragments but does not yet parse
  and verify which exact citation keys the model used in each sentence.
- Context is bounded by characters rather than token counts. Different models
  tokenize text differently, so the cap is protective rather than exact.
- Model availability, free-tier quotas, rate limits, and retention terms are
  provider-controlled. Steward requires an explicit model name rather than
  assuming a particular Gemini model is available to every account.
- Telegram conversation state uses a local SQLite LangGraph checkpointer, so a
  restarted process can continue a chat thread. There is still no user
  allowlist, delivery retry policy, or durable Telegram update deduplication.
- Telegram captures use `/save` and the normal Bot API download ceiling. There
  is no self-hosted Bot API server or cloud-drive relay for larger files.
- Organization matching is intentionally simple and user-reviewed. It is not
  yet LLM-assisted, nor is a paused organization approval resumed through
  Telegram.
- Travel extraction recognizes a small, label-oriented itinerary shape. It is
  not a general airline-document parser and does not yet create calendar events.
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

1. Phase 8: add conversation state, first in memory and then with a persistent
   LangGraph checkpointer, so Telegram replies can resolve references such as
   "that".
2. Add Telegram capture in Phase 9, preserving messages and attachments as
   sources instead of treating questions as durable knowledge automatically.
3. Add structured answer evaluation cases that inspect retrieved evidence,
   generated citations, and unsupported-answer behavior.
4. Add richer extractors for plain text and PDF before introducing broad
   capture channels.
5. Add structured retrieval evaluations before relying on semantic results for
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
