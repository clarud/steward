# Steward Developer Guide

This guide describes the implementation currently in the repository. Steward can register local Markdown, plain-text, DOCX, HTML, images with optional local OCR, and native-text PDF files, extract structured
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
`[F1]`, followed by a source filename, heading, line location, and excerpt.
`AnswerContext.prompt` is the exact text sent to the model, while
`AnswerContext.citations` holds the matching structured local metadata,
including the physical path needed for provenance. The prompt deliberately
does not expose local directory structure to a model.

After generation, `CitationVerification` extracts inline request-local keys
such as `[F1]` and compares them with that exact context. For a generated
answer, `AnswerResult.citations` therefore exposes only valid citations the
model actually used. An answer with
no inline citation, or one containing an unknown key such as `[F99]`, carries a
visible verification warning rather than being silently presented as grounded.

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

`GEMINI_API_KEY`, `OPENAI_API_KEY`, and `SOCLAAS_API_KEY` are intentionally not part of the logged
`Settings` dataclass. `STEWARD_MODEL_PROVIDER` defaults to `gemini`; use
`STEWARD_MODEL_PROVIDER=openai` with `STEWARD_OPENAI_MODEL` to use the OpenAI
gateway instead. `STEWARD_MODEL_PROVIDER=soclaas` configures NUS SoCLaaS with
`SOCLAAS_MODEL` and `SOCLAAS_BASE_URL`; its API key stays in
`SOCLAAS_API_KEY`. The `STEWARD_SOCLAAS_*` aliases are also accepted. SoCLaaS uses OpenAI-compatible responses for normal
answers and client-executed function calls for the agent loop. The selected provider's model setting is optional until
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

Telegram is still a transport boundary: it does not own retrieval, capture,
organization, or external API logic. A normalized Telegram message moves
through the application router, then into ordinary services or a deliberately
small LangGraph workflow as appropriate:

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
to, which contributes a deterministic reference signal for richer future
resolution.

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

### Current primary-interface interactions

The Telegram router intentionally distinguishes read-only inspection from
explicit operations:

- `/search`, `/source`, `/sources`, `/inbox`, `/workspaces`, `/activity`,
  `/records`, `/tasks`, `/roots`, and Calendar reads inspect current local or
  authoritative external state.
- Attachments and substantial text are staged as provisional intake. Saving,
  discarding, or adding context is explicit; an attachment is not silently
  retained merely because it arrived in chat. Provisional material defaults to
  `no_model`; `/intake_analysis ID external|local|none` makes the model boundary
  explicit before acceptance. When accepted, that choice becomes the captured
  source's privacy rule before any model-assisted organization can run. A bare `/save` reply can select only the pending staged
  intake from that exact chat message; it reuses the original staged bytes and
  does not re-download or infer an attachment from reply text.
- Curated note drafts are similarly non-canonical. **Edit** opens a durable,
  chat-scoped input step; the replacement creates a new pending proposal and
  marks the earlier draft rejected as superseded. Steward never overwrites the
  draft that was originally shown to the user. The opaque proposal reference is
  SQLite-backed, so the replacement can be supplied after a process restart.
- Tasks, curated notes, source/workspace links, records, travel corrections,
  Calendar writes, and organization moves become durable only through a
  pending proposal and a deterministic approval path.
- `/home` and `/pending` unify outstanding actions, organization choices,
  staged intake, and knowledge updates into identifiable review cards. After
  opening one card, an unambiguous `yes`/`accept` or `no`/`reject` reply is
  resolved through its durable, chat-scoped review reference and invokes the
  same validated approval command as the card button. A stale card, or an
  intake from another chat, cannot be confirmed this way. The card reference
  is retired as soon as a decision is submitted, even if deterministic
  execution then reports a recoverable failure.
- A Telegram task may additionally use `--remind-at` with an explicit
  offset-aware ISO timestamp. Approval creates a separate durable reminder
  addressed only to the originating chat. The polling process claims due
  reminders, acknowledges them only after Telegram accepts the message, and
  releases failed sends for a later retry; a crash can therefore produce at
  most an at-least-once duplicate rather than silent reminder loss.
  The worker owns a stop event: the Telegram post-stop hook signals and awaits
  it before HTTP shutdown. Cycle errors are logged without provider details
  and retried on the next interval. Shutdown waits for an in-flight delivery
  to finish, but interrupts the idle 60-second wait immediately.
- `/research` is explicitly external and ephemeral. Keeping its result writes
  a provenance-labeled Inbox note; it does not archive or silently promote web
  information to canonical personal knowledge.
- Drive/Gmail search returns metadata cards, followed by an explicit
  single-item import. OAuth setup, source-root authorization, file watching,
  and secrets remain local-only operations.

`/help` exposes the available interaction groups and `/status`,
`/deliveries`, `/dead_letters`, and `/integrations` provide metadata-only
operational visibility. The full real-bot validation sequence is maintained in
`docs/telegram-manual-test-checklist.md`.

### Durable update delivery

Before the adapter calls an application handler, it uses
`TelegramUpdateDeliveryRepository` to insert the normalized event ID (for
example, `telegram:42`) into SQLite as `processing`. SQLite's primary-key
constraint makes that claim atomic: a concurrent or later delivery of the same
update cannot enter the application again. After `reply_text()` succeeds, the
row becomes `delivered`; delivered duplicates are ignored without producing a
second answer.

If downloading, application work, or the Telegram reply raises an error, the
adapter deletes only its `processing` claim and re-raises the error. Telegram
or a later polling cycle can then retry the update. This gives Steward a
deliberate **at-least-once** boundary: it does not lose a message merely
because the reply failed, but a process crash after domain work and before the
delivery record can repeat that work. Capture, reviewed organization moves, and
future action services must therefore remain idempotent. A true external
exactly-once guarantee would require an outbound-message idempotency facility
that the Telegram Bot API does not provide.

`steward telegram-deliveries` exposes only local coordination metadata: update
ID, processing/delivered status, claim time, and delivered time. It never stores
or prints Telegram message text, but makes a stuck lease or repeated delivery
observable during local troubleshooting.
`telegram-delivery-history` additionally shows past claimed, reclaimed,
released, and delivered transitions after a processing row has been released.

### Reviewable agent writes from Telegram

The tool agent can create a pending `ActionProposal` for workspace creation and,
when Calendar tools are explicitly enabled, for a saved travel record's
Calendar event. Neither tool executes the final action. `ActionProposalRepository`
makes a proposal durable, so reviewing it is resumable without restoring model
state. The Telegram application handles these exact commands:

```text
/action_proposals
/approve_action ID
/reject_action ID
```

`StewardActionProposalApplication` validates the numeric ID and routes only
recognized proposal types to their deterministic reviewer. Workspace acceptance
performs idempotent workspace creation; Calendar acceptance invokes its
idempotent writer only after approval. The model has no route to either final
action. This is a deliberately narrow first
human-in-the-loop interface: all configured allowlisted chats are trusted
administrators, and proposals are not yet owned by an individual Telegram user.

### Retained external research

`ResearchService` is provider-independent through its `ResearchProvider`
protocol. The initial Gemini Google Search adapter produces an **ephemeral**
`ResearchBundle`: a query, model answer, and returned URLs. The normal
`steward research QUESTION` command prints it but creates no local source.

`steward research-retain QUESTION` is the explicit lifecycle transition. It
runs the same provider, then `ResearchRetentionService` renders a Markdown note
with an unambiguous warning that the answer is model-generated and the URLs are
provenance, not archived page contents. It uses `InboxCaptureService`, so the
note becomes a normal local `Source`, is fragmented and indexed, and records a
capture activity event. A SHA-256 fingerprint of the rendered note supplies a
stable synthetic event ID; retaining an identical bundle again returns the
existing source instead of creating another note. Original webpage download and
archiving remain separate future work.

### Google Drive search and explicit Inbox import

`/integrations` is a metadata-only Telegram readiness card. It never calls a
Google API, starts OAuth, or displays a token/client path. It reports whether a
token is absent, unreadable, present, expired with a local refresh token, or
expired without one. This guides the user back to local authorization without
claiming that a token file alone proves remote access will succeed.

`GoogleDriveService` is a separate read-only external boundary. Its OAuth flow
uses `drive.readonly`, stored in a dedicated `google-drive-token.json`, rather
than reusing broader Calendar credentials. A token created for the older,
metadata-only scope must be reauthorized once.
Telegram's `/integrations` screen reads only non-secret local token metadata:
for Drive and Gmail it can report that a token lacks the required import scope
or that scope metadata is unavailable. It never shows the scope name, token,
refresh token, client-secret path, or asks for authorization in chat.
`steward drive-search QUERY` queries Google Drive for non-trashed filenames and
returns current metadata: Drive ID, name, MIME type, modification time, web
link, and available size. Drive is authoritative for that state.

`DriveInboxImportService` is the explicit retention path behind `steward
drive-import FILE_ID` and Telegram's `/drive_import FILE_ID` command. It looks
up exactly that ID, streams its original bytes to a temporary local file, then
calls `InboxCaptureService.capture_file()` with a synthetic `drive:FILE_ID`
event. The normal capture service preserves the original, hashes it, extracts
derived fragments once, and records activity. The stable event identity makes a
repeat import idempotent. There is no background sync and no model-selected
Drive download. Telegram's oversized-upload reply merely guides the user to
this explicit route; its adapter never handles OAuth or Drive bytes.

For selected Google-native files, an original binary is not available. A Google
Doc is therefore explicitly exported as `.txt`, a Sheet as `.csv`, and a Slide
deck as `.pdf` before the same capture path runs. The export name makes this
derivative visible; the Drive item stays authoritative and no background sync is
introduced. Unsupported native types are rejected rather than downloaded with
an ambiguous format.

### Gmail search and explicit Inbox import

`GmailService` uses its own `gmail.readonly` OAuth token. `steward
gmail-search QUERY` passes the user's Gmail query syntax directly to Gmail,
lists matching IDs, and requests each result with `format="metadata"` and only
the `Subject`, `From`, and `Date` headers. It returns those headers and Gmail's
snippet; it never requests bodies, attachments, mail sending, labels, or
deletions. Gmail remains authoritative. `steward gmail-import MESSAGE_ID`
explicitly requests that message's raw RFC 822 data, stores it as a canonical
`.eml` original through `InboxCaptureService`, and never sends or automatically
imports mail.

`GmailInboxImportService` is shared by the CLI and Telegram's explicit
`/gmail_import MESSAGE_ID` command. The command is available only to the
allowlisted Telegram adapter and uses a lazy OAuth boundary: bot startup and
ordinary messages do not inspect Gmail or trigger browser authorization.

Telegram treats Drive/Gmail adapter failures as safe operational failures. It
does not echo provider exception strings, because those can include local token
paths or other machine diagnostics. The reply tells the owner to verify local
authorization and retry; detailed diagnosis stays in the local runtime.

Telegram applies the same rule to Calendar reads: a provider/OAuth failure
returns a retry-oriented, secret-free status rather than the raw exception.

Raw `.eml` sources use `EmailExtractor` rather than the generic plain-text
extractor. Python's standard-library MIME parser selects readable `text/plain`
or `text/html` parts, skips declared attachments, records the email subject and
part ordinal as provenance, and keeps the original RFC 822 file untouched.

Normal scans use hashes to avoid redoing extraction. `steward reextract
SOURCE_ID` is the explicit maintenance operation for an unchanged original
after installing OCR, improving an extractor, or repairing derived fragments.
It replaces only rebuildable fragments and their local semantic vectors; it
never changes the original file or source identity.

Telegram exposes the same capability only as `/propose_reextract SOURCE_ID`.
It stores a narrow proposal containing the registered source ID, shows an
approval card, and then calls `SourceService.reextract_source()` only after the
owner accepts. Telegram never accepts a path, parser argument, or arbitrary
filesystem target. Extraction failure leaves the proposal pending so that the
user can repair the local condition and deliberately retry; success records
`SOURCE_REEXTRACTED` plus the approval audit event.

`/propose_rebuild_index` follows the same review boundary for the whole local
semantic index. Its approved callable loads the already-local embedding model,
clears and regenerates only derived vectors from persisted fragments, and never
accepts a model name, URL, path, or download instruction from Telegram. A
model/cache failure leaves the proposal pending and returns a secret-free retry
message.

`/propose_unregister_source SOURCE_ID` is the reviewed Telegram counterpart to
the CLI's `unregister-source` command. It accepts only a registered numeric
source ID, displays the source filename rather than its local path, and removes
only Steward's SQLite metadata and derived rows after approval. The original
file is deliberately retained. This is useful when a file was indexed by
mistake; it is not a file-deletion command.

### Local search UI

`steward ui` runs a small standard-library HTTP server on `127.0.0.1:8765` by
default. The implementation rejects non-loopback hosts, uses the existing
`LexicalSearchService` by default (or `HybridRetriever` with `--mode hybrid`), escapes all query/source content before HTML rendering,
and exposes a search form plus source-path and fragment-location results.
Search-hit links lead to `/sources/{id}`, which reads only a registered SQLite
source identity and its already-derived fragments: it never accepts an arbitrary
filesystem path. Source pages escape extracted text, show source type/status and
fragment provenance, and page long sources in groups of 50 fragments. The UI
intentionally contains no write controls, model calls, OAuth credentials, or
external integrations. This gives Steward a locally inspectable UI surface
without silently expanding its trust boundary.

`/records` is a second read-only local page. It renders travel, receipt, and
warranty metadata from `RecordService`, marks each row by record type, and links
back to `/sources/{source_id}`. It never renders source text itself, accepts no
write requests, and keeps the source rather than its derived record authoritative.

### Retrieval path filtering

Lexical search accepts `--path-prefix PATH`. The value is resolved locally and
becomes a parameterized SQLite `sources.path LIKE PATH%` filter alongside the
active-source and optional source-type filters, before FTS5-ranked results are
returned. This is useful for a large vault with project/course subtrees and
does not change the global index or canonical source locations.

Configure the private bot token and run the adapter:

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
```

```powershell
steward telegram
```

## Conversation state and checkpoints

**Ask about it** stores a `source_question` context for the selected source.
The next ordinary message consumes that input step and restores a `source`
reference. `/ask_source ID QUESTION` provides an explicit equivalent. The same
selected-document model path enforces privacy, whole-input size limits, and
citation membership for both answers and summaries. This input step survives
restart and adds no document text to the reference store.

When the tool agent has consumed its execution budget, it permits one final
model invocation using the results already received and an instruction to
finish. Additional tool requests from this response are never executed;
continued tool requests or provider failure yield the bounded fallback. This
adds at most one model request at budget exhaustion and preserves the original
tool-execution cap. It lets a successful last lookup contribute to an answer.

Source cards also expose `/summarize_source ID`; `summarize it` resolves the
selected source from chat context. The configured model receives only that
source's stored fragments and location labels after the source privacy policy
permits it. No local path is included. The generated answer includes evidence
locations and is not saved as knowledge. Inputs over 60,000 characters are
declined explicitly. Missing or unknown citation keys cause the generated text
to be withheld with retry/read actions. This checks reference membership, not
whether each claim follows from its evidence; whole-document batching and semantic citation verification
remain limitations. No new model dependency or persistence table is required.

Calendar list/detail cards use `calendar_time_label` for readable dates and
explicit provider UTC offsets. All-day dates retain date-only semantics and
their exclusive end is converted to the last included day. No-argument Calendar
browsing passes the current UTC time as a lower bound; named searches retain
historical reach. This presentation change adds no dependency or persisted state.

Source cards expose **Read content** through `/source_content ID [SECTION]`.
The application reads stored fragments in document order and presents one
section with its location and Previous/Next actions. The existing Telegram
presenter splits oversized sections across messages. `give me the content`
uses the selected source's durable chat context; without a selected source it
asks the user to open one. Reading extracted content invokes no model and does
not reparse the original. This is an explicit owner read delivered through
Telegram; model-access privacy rules remain separate.

Phase 8 gives each Telegram chat a LangGraph thread ID such as
`telegram:100`. The application invokes the graph with that ID, and the local
SQLite checkpointer stores a snapshot after each graph step in
`.steward/checkpoints.db`. Restarting the process and invoking the same thread
loads its prior state.

The state keeps a bounded ten-message exchange, recent **source** IDs and
filenames, and placeholders for workspace, concepts, and records. It does not
store full documents. For a small deterministic first reference resolver, a
follow-up containing terms such as `that`, `this`, or `it` is expanded with the
preceding user question and recently retrieved filenames before retrieval. This
is useful but deliberately conservative: it is not yet general natural-language
reference resolution. Separately, opening a Telegram `/source ID` card stores a
durable, chat-scoped source reference. Exact navigation requests such as
`open the last source` or `show that PDF` reopen that same card without an LLM;
broader phrases still go through ordinary retrieval rather than being guessed.
The same narrow navigation rule applies to explicitly opened record cards:
`show that flight`, `open the last receipt`, and `show that warranty` resolve
only when the record type and the chat-scoped durable record reference agree.
Tasks use compact `/tasks` cards with `Open` actions; after opening one,
`show that task` can reopen that precise task after restart. Marking a task
complete remains an explicit **Mark complete** action or `/complete_task ID`.
Workspaces likewise use `/workspaces` cards and `/workspace ID` detail views.
Their source list is a semantic link list, not a directory listing or a move
instruction; `show that workspace` only reopens the explicit workspace card.
Calendar event IDs are opaque external identifiers rather than Steward integer
IDs. Opening `/calendar_get EVENT_ID` therefore stores the event ID in the same
chat-scoped context. String IDs use a storage prefix to prevent SQLite's legacy
integer-column affinity from stripping leading zeroes or rounding long IDs.
Existing integer Steward references remain compatible. `show that event` refetches that exact event from
Calendar after restart. It never stores a stale Calendar copy; an unavailable
or deleted event clears the reference and asks the user to search again.

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

`ModelAssistedOrganizationService` is an optional second proposer for the
explicit `steward propose-organization SOURCE_ID --model-assisted` command. It
sends only the source filename/type, bounded extracted excerpts, and the IDs
and names of existing workspaces to the configured model. The model must return
JSON naming one of those IDs or `null`; deterministic validation rejects an
unknown ID, a malformed response, an invalid confidence, or provider failure.
It can neither create a workspace nor select a file path: code derives the
destination from the validated workspace, and the normal approval flow remains
required before a move. If the model is unavailable or uncertain, the source
stays in Inbox through the existing conservative fallback.

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
`TravelRecordReference` extends that small record without widening its core
schema for every possible identifier. A reference has a normalized type, value,
and required supporting `SourceFragment` ID. The database enforces that both
the travel record and fragment exist; `add_reference()` is idempotent for the
same record/type/value and returns the existing reference on a retry. The CLI
exposes `add-travel-record-reference RECORD_ID TYPE VALUE FRAGMENT_ID` and
`travel-record-references RECORD_ID` for explicit inspection.
`FileMutationService.undo_move()` supplies rollback data and writes its own
`SOURCE_MOVE_UNDONE` activity event, preserving the history of reversible
filesystem changes.

## Read-only tool agent

Phase 21 adds the first actual tool-choosing loop without using a generic
prebuilt agent. `ReadOnlyToolService` adapts ordinary services into six
JSON-returning tools: `search_sources`, `read_source`, `search_knowledge`,
`search_records`, `search_workspaces`, and `search_activity`. They have no
mutation capability. Source tool results expose a Steward source ID and
filename, not a physical filesystem path; path-like Activity details are
redacted before entering model context.

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
Provider request construction—including SDK message conversion—is wrapped as a
recoverable `ModelGatewayError`, so an incompatible provider SDK response ends
the tool turn with Steward's safe unavailable-model reply rather than crashing
the Telegram update handler.

The Telegram `/agent` boundary also catches a `GraphRecursionError` from this
loop and returns a clear, non-mutating failure message. It does not treat a
limit breach as a reason to retry the update or perform another tool call.

`OllamaToolCallingModel` provides the same narrow graph-facing interface for a
local model. It sends the conversation and the allowlisted JSON tool schemas to
Ollama's `/api/chat` endpoint. Ollama returns requested function names and
arguments; the adapter creates internal call IDs because the Ollama response
does not supply them, and `ToolNode` uses those IDs only to pair local results
with the request. On the next turn the adapter replays the assistant tool call
and each result in Ollama's `role=tool`, `tool_name`, `content` format. It never
executes a tool itself. Its local HTTP timeout is 180 seconds because CPU
inference can be much slower after tool results expand the transcript. Set `STEWARD_MODEL_PROVIDER=local` and
`STEWARD_LOCAL_MODEL` to select it for `steward agent`.

The graph counts individual tool calls, not only model turns. If a provider
returns a batch larger than the remaining `max_tool_calls` budget, Steward ends
the request before `ToolNode` executes any call in that batch. This matters for
local models, which can occasionally emit many duplicate requests at once.

### Proposal-only agent writes

The first agent write capability is deliberately indirect:
`propose_create_workspace(name)`. It is a `SAFE_WRITE` because it persists a
reviewable `ActionProposal`, but it never creates the workspace. The tool result
includes the exact `steward review-action-proposal ID accepted` command needed
to authorize execution. `ActionProposalService.review()` then creates the
workspace through `WorkspaceService`, records audit events, and marks the
proposal accepted. Repeating an unreviewed proposal for the same normalized
name reuses it; repeating an accepted review returns the existing workspace.
This is the generic safety seam future agent actions will use instead of giving
the model direct access to a side-effecting service.

## Tool risk policy

Phase 22 makes the safety properties of each tool explicit in `ToolDefinition`:
`side_effects`, `risk`, `idempotency`, `external_system`, and
`requires_approval`. `ToolRisk` distinguishes `READ_ONLY`, `SAFE_WRITE`,
`SENSITIVE_WRITE`, and `DESTRUCTIVE` operations.

`ToolPolicy` is not prompt text. `build_tool_agent_graph()` supplies it to
`ToolNode` through `wrap_tool_call`, where every requested tool is checked
immediately before execution. A denied call becomes a `ToolMessage` explaining
why it was not run; the Python callable is never invoked. Read-only tools are
always permitted, while proposal-only local writes have narrow `SAFE_WRITE`
definitions. No model-callable tool directly creates a Calendar event or moves
a source; final actions remain behind deterministic approval boundaries.

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

`calendar-propose-travel-event RECORD_ID` adds a pending generic
`ActionProposal`, after deterministic validation that the record exists and has
timezone-aware departure and arrival times. `calendar-review-travel-event ID
accepted` is the second, explicit step. Only that accepted path authorizes
Calendar OAuth and invokes `CalendarWriteService`; rejection stays entirely
local. This lets a future agent request the same proposal without being trusted
to create a Calendar event itself.

When calendar tools are explicitly enabled for the tool agent, its only write-
adjacent capability is `propose_create_travel_calendar_event(record_id)`. It
uses that exact same proposal service, accepts only a persisted travel-record
ID, and returns a review command. `ToolPolicy` labels it `SAFE_WRITE` because
the proposal is a durable local mutation, but it is not a remote Calendar
write. The model never receives a callable `create_calendar_event` tool.

`StewardActionProposalApplication` recognizes this action type before its
workspace-specific reviewer. Its Telegram `/approve_action ID` path obtains a
Calendar writer from a lazy factory only after the human accepts. Thus starting
the Telegram process, listing proposals, rejecting one, or a model requesting
one never starts OAuth or contacts Google.

Phase 24 adds the narrower `CalendarWriteService` rather than allowing callers
to insert arbitrary Google API payloads. It accepts a persisted `TravelRecord`
with departure and arrival times, constructs a titled event, and requests the
Google Calendar event scope. A local `calendar_event_links` row maps
`travel-record:ID` to Google's external event ID. A repeat request looks up
that link and reads the existing event instead of inserting a duplicate.

After an insert, `CALENDAR_EVENT_CREATED` is appended to the activity log. The
filesystem/database transaction cannot span Google's API, so this is
"exactly-once-ish": a completed local link is reliably idempotent. Phase 32
also closes the crash window after Google accepts an event but before the link
is saved by reconciling the remote event with its deterministic private
idempotency key on retry.

## Ephemeral external research

Phase 25 adds `ResearchService` and a provider boundary. `GeminiGoogleSearchProvider`
uses Gemini's Google Search grounding for a generated, cited answer. The key-free
`DuckDuckGoSearchProvider` is a source-first adapter: it returns result titles,
URLs and snippets and explicitly says it has not read the linked pages. `steward
research --provider auto` prefers configured Gemini research and otherwise selects
DuckDuckGo, independently of the model provider used for answers; either can be
selected explicitly with `--provider`. Both return a `ResearchBundle` with a
provider identity. Its retention status is always `ephemeral`; it does not write a
`Source`, fragment, concept, claim, or embedding. A future explicit “keep those
sources” workflow must route selected material through normal capture and
provenance processing rather than bypassing the Source layer. This is now
provided by `research-retain`: it captures a labeled local Markdown note whose
provider identity and URLs preserve provenance, never copies the cited pages.

## Emerging workspace detection

Phase 26 provides the explicit `steward review-inbox-workspaces` workflow.
`WorkspaceDetectionService` considers only active sources physically in Inbox,
splits meaningful filename terms, removes generic words, groups repeated terms,
and ignores an already existing workspace name. Two or more sources sharing a
term produce a pending `WorkspaceProposal` with source IDs, rationale, and a
conservative confidence score. It does not create a workspace, link sources,
or move files; those remain deliberate user actions.

## Knowledge connections

Phase 27 adds `KnowledgeConnector` and `steward connect-knowledge`. It joins
claim evidence to find pairs of different concepts whose claims cite the same
fragment. Each proposal contains both concept names, supporting fragment IDs,
a confidence score, an explanation of the shared evidence, and an explicit
statement of where the apparent analogy may fail. Nothing is persisted as a
relationship yet; user feedback such as useful, obvious, stretch, or wrong is
the later learning loop.

## File watching

Phase 28 adds `FileWatchService` plus `steward watch ROOT`. `watchdog` supplies
filesystem notifications, but notifications are only hints: each changed path
is debounced and then `SourceService.refresh_markdown_path()` hashes it before
declaring a change. New files are registered and extracted, unchanged hashes do
nothing, changed files replace their derived fragments (and semantic vectors
when configured), and deleted registered files become `MISSING`. This avoids
relying on noisy filesystem events as the truth source.

## External integration boundary

Phase 29 adds `IntegrationRegistry`. An external integration declares its
adapter name, domain service, tool names, and whether it records activity.
Registration verifies that every named tool has matching external-system risk
metadata. Google Calendar is the first real integration; web research remains
an explicitly ephemeral provider. This keeps future Gmail, GitHub, or task
system additions narrow rather than offering the model a generic HTTP tool.

## Per-source privacy policy

Phase 30 stores a `PrivacyRule` for individual sources: `external_allowed`,
`external_redacted`, `local_model_only`, or `no_model`. `PrivacyService` uses
SQLite foreign keys to prevent orphan policies and exposes the rule through
`set-source-privacy` and `source-privacy`. The default preserves the existing
local-first workflow as `external_allowed`. The cloud `AnswerService` filters
retrieval hits before it builds model context, and the cloud tool-agent adapter
filters source searches, source reads, and source-derived travel records before
they become tool results. `external_redacted` intentionally fails closed until
there is a genuine, reviewable redaction pipeline; it is unsafe to assume that
the rule's name transforms sensitive text.

The same boundary is enforced before model-assisted organization and
model-assisted knowledge enrichment: cloud providers receive only
`external_allowed` source excerpts, local providers may receive any source
except `no_model`, and denied material produces a deterministic local response.

## Model routing

Phase 31 adds `ModelRouter` and `OllamaModelGateway`. It selects a cloud
gateway when every evidence source is `external_allowed`; otherwise, a source
marked `local_model_only` or `external_redacted` requires the configured local
Ollama gateway. A `no_model` source is removed before context construction.
If local-only evidence is retrieved but Ollama is not configured, Steward
returns an explicit privacy limitation instead of falling back to the cloud.
Ollama is a deliberately narrow local HTTP adapter: `/api/generate` is used for
ordinary grounded answers and `/api/chat` is used for the explicit tool loop.
The local tool agent receives only the same allowlisted schemas as Gemini and
still relies on `ToolNode` and the policy registry to execute a request.

## Reliability engineering

Phase 32 turns failure cases into explicit recovery rules rather than silent
assumptions. Capture is idempotent by the Telegram event/source hash path;
repeated scans and watcher notifications hash before replacing derived data;
missing or manually moved files become `MISSING` and are repaired by the next
scan. Bad document extraction or a model/provider failure stops that operation
without replacing the original Source, so the original can be retried after
fixing the parser or credentials. SQLite's short-lived locks surface as an
operation failure; retry the command rather than retrying unboundedly inside a
transaction. A corrupt semantic index is rebuildable: run `steward index`.

Calendar is the special cross-system case. A local database transaction cannot
atomically include Google's API. `CalendarWriteService` first checks its local
link, then queries Google for the deterministic private extended property
`steward_idempotency_key=travel-record:ID`; only if neither exists does it
insert an event. It saves the local link and audit activity after either a
recovered or newly created event. Therefore, a crash after remote acceptance
and before the SQLite link is repaired by retrying the same command, without a
second Calendar event. Network/OAuth failures still leave no local success
record and should be retried only after the external condition is resolved.

## Observability

Phase 33 adds the `steward.trace` structured local logger. Retrieval graphs
emit preparation, retrieval fragment IDs/counts, conditional routes, answer
citation counts, and no-evidence paths. The custom tool graph additionally
records model-call counts, routing decisions, and requested tool names. Trace
events deliberately exclude raw source text, prompts, model output, API keys,
and token values. With `STEWARD_LOG_LEVEL=INFO`, the JSON payloads remain
visible in normal local process logs and can be searched without relying on an
external agent-observability platform.

## Evaluation framework

`steward evaluate-retrieval VAULT CASES.yaml` promotes the lexical evaluation
fixture into a local acceptance tool. `--mode hybrid` evaluates the same cases
against the local hybrid retriever. A case is a user-written query plus an
expected source path (relative to that vault) and heading. It reports Recall@5,
mean reciprocal rank, and every miss rather than hiding failures behind a
single aggregate. It reads the existing SQLite index and never sends vault text
to a model or changes canonical sources. Personal course cases should stay
outside the repository unless the author explicitly wants to publish them.

### Model-assisted knowledge enrichment

`propose-knowledge-enrichment CLAIM_ID FRAGMENT_ID --model-assisted` gives the
model exactly one existing claim and one selected source fragment. It can only
classify their relationship as `confirm`, `extend`, `refine`, `qualify`, or
`contradict`, with a short rationale. `ModelAssistedKnowledgeService` validates
the JSON enum and rationale length; malformed or unavailable model output falls
back to `KnowledgeService.compare_evidence()`. The result is persisted as a
pending row with foreign keys to the canonical claim and original fragment, not
a claim mutation. `knowledge-enrichment-proposals` lists those rows and
`review-knowledge-enrichment ID accepted|rejected` records the explicit
decision in the activity log. A contradiction is never silently added as claim
support. The CLI checks the source privacy rule before sending evidence to a
cloud or local model.

The tool agent can also call `propose_knowledge_enrichment(claim_id,
fragment_id)` when the user explicitly requests an evidence comparison. The
tool uses ordinary Python to load both IDs, computes the initial relationship
deterministically, and persists a pending proposal. It does not receive model
text as a command, and it cannot edit claims, concepts, or evidence.

### Versioned knowledge reviews and stale-review recovery

`KnowledgeEnrichmentProposalRepository.add()` saves an evidence snapshot with
each new proposal: claim text, fragment text and location, source ID, and source
content hash. Migration 46 preserves older review IDs/statuses without inventing
snapshots. The uniqueness key includes the snapshot, so requesting the same
proposal again is idempotent, while a changed evidence version gets a new ID.

`review()` uses a SQLite write transaction to check pending status, source
availability, and snapshot equality before accepting. Approval/rejection and
the corresponding activity event commit together. An audit failure rolls the
review back; accepted interpretations never silently rewrite canonical claims.
Legacy proposals without snapshots can be rejected but require a fresh proposal
before acceptance. Current accepted-review lookup excludes outdated snapshots;
the stored historical review remains available while its referenced rows exist.

`StaleKnowledgeReviewError` is a `ValueError` subclass carrying the proposal,
claim, and fragment IDs. CLI callers retain their ordinary error handling.
`StewardKnowledgeApplication.handle_command()` catches the specific subtype and
returns a transport-neutral recovery card with three bounded actions:

- **Fresh review** calls `/propose_enrichment CLAIM_ID FRAGMENT_ID`, comparing
  current evidence and creating a preview only. This path uses the deterministic
  comparison; it does not reproduce a previous model-assisted interpretation.
- **View saved review** reopens the old snapshot for inspection.
- **Dismiss old review** explicitly rejects only the old proposal.

Refreshing neither approves the replacement nor dismisses the old review.
The test in `tests/test_application.py` follows these exact action commands,
checks saved versus current text, and verifies the two proposal statuses remain
independent. Knowledge repository tests cover version changes, migration, and
transaction rollback. Live Telegram acceptance is still tracked separately in
the manual checklist. Snapshot validation protects evidence identity, not the
semantic accuracy of the interpretation; referenced-row deletion and long-term
history retention require separate lifecycle consideration.

`KnowledgeService.accepted_reviews()` joins proposals, claims, fragments, and
active sources in one SQL query, then compares the saved snapshot with those
returned values. Missing/inactive sources and missing referenced rows are omitted
without changing historical approval status. A restored active source becomes
eligible again only when its evidence snapshot still matches. This avoids a
two-connection read race and prevents a missing evidence row from crashing the
lookup. Provider-specific privacy checks still apply separately before model use.
Telegram concept cards use this current-review lookup for operation summaries.
They count other accepted reviews as historical and needing revalidation.
The paginated Evidence reviews screen retains all accepted history but labels
each entry as current or historical-only; a current label means matching,
available evidence, not a proof that the claim is true. Restoring an unchanged
source can make its interpretation current again without another database write.

Phase 34 keeps evaluation data in versioned YAML under `tests/evaluation/`.
`retrieval_cases.yaml` measures lexical Recall@5 and MRR against a small vault
fixture. `product_cases.yaml` adds reviewable cases for organization (including
acceptable Inbox alternatives), travel-record fields, expected knowledge
integration operations, tool selection/forbidden tools, and approval safety.
`product_evaluator.py` validates that every subsystem has cases with the fields
needed for a future deterministic or model-backed evaluator. This avoids
claiming model quality from one-off manual examples while keeping the expected
behavior easy to edit and inspect in code review.

## Restore preflight

Restore also compares recognized schema roles: `schema_migrations` plus
`sources` identify operational state, while `checkpoints` plus `writes` identify
the installed SQLite LangGraph saver format. A recognized destination refuses
a different or unrecognized candidate role before safety-copy writes. Tests use
real saver tables and non-descriptive filenames in both swap directions.
An unreadable/unrecognized destination cannot establish its role, so this guard
does not prevent recovery solely on that basis. Operators must still verify
installation identity, backup date, and matching backup pairs; marker tables
are a mistake-prevention check, not authentication or full schema validation.

The organization graph recovery regression uses real SQLite checkpoints and
operational repositories. With writers stopped it snapshots both databases at
an interrupt, completes a rejection, restores the paused pair, and reconstructs
the graph. State inspection alone leaves the original untouched; explicit
`Command(resume="accepted")` performs the reviewed move. The safety copies keep
the rejected state, and the original backup keeps the pending state. This
rehearses a consistent stopped-writer pair, not live cross-database atomicity or
restoration of Telegram messages/callbacks already delivered externally.

The CLI backup command reserves the output directory exclusively and tracks
successful copies individually. Failure reports an incomplete set and lists
retained completed snapshots; it does not delete usable copies or report overall
success. A corrupt-second-database regression exercises this path. Successful
multi-database backups warn that copies are sequential: stop all writers for a
coordinated recovery point. No cross-database atomicity is claimed.

`tests/test_recovery_workflow.py` rehearses one complete operational recovery:
create a synthetic source/claim/pending enrichment, snapshot the database, reject
the review, restore the pending snapshot with a safety copy, and accept the
restored review in a fresh Python interpreter. It verifies current-evidence
lookup, one acceptance audit, preservation of the rejected state in the safety
copy, an unchanged pending backup, and byte-identical original source content.
The subprocess is bounded by a timeout. This verifies one local database
workflow, not paired operational/checkpoint restoration or external actions.
Restoring old local state can forget later remote effects; do not replay Calendar
or other external actions without reconciling their authoritative live state.

`snapshot_database()` reserves its output with exclusive file creation (`xb`)
before SQLite opens it. A competing destination created after the initial
existence check causes refusal rather than overwrite. Source connections use
SQLite read-only mode, and explicit connection closing allows failed-copy cleanup
on Windows. Regression tests inject a competing file at reservation time and
verify its contents are untouched; a corrupt-source test verifies cleanup of
the newly reserved output. This prevents cooperating backup operations from
overwriting one another, not arbitrary filesystem replacement by another actor.

`restore_database()` validates the candidate using a read-only SQLite connection,
`PRAGMA quick_check`, and the presence of an application table before creating
the safety copy or opening the destination for restore. A zero-byte file is
explicitly rejected: SQLite otherwise treats it as an empty database. Invalid,
truncated, or unreadable candidates produce a bounded error with no restore.
Tests verify byte-for-byte preservation of the input and destination and no
safety-copy creation on preflight failure. Valid restoration retains its existing
write-once safety-copy behavior. This is structural validation, not proof that
the operator selected the correct database or backup date; stop Steward and
check those choices before an explicitly confirmed restore.

## Telegram runtime ownership verification

`telegram_runtime_lock(data_dir)` holds an exclusive SQLite transaction in
`telegram-runtime.db` for the polling process lifetime. The file is not a PID
marker: its existence after shutdown does not mean Steward is still running.
The OS releases ownership when the connection/process closes.

`tests/test_runtime.py` checks normal exit, abrupt `os._exit`, and a live
cross-process contender. The latter waits for an explicit lock-acquired
handshake, confirms that another process is refused for the same data directory,
checks an independent directory, kills only the test-owned process, and then
reacquires the original lock despite its file remaining. All subprocess waits
are bounded and cleanup terminates the owned child on assertion failure.
This is local process coordination coverage, not a live Telegram polling test.
Different data directories using the same bot token are not coordinated by this
guard; run only one polling instance per bot, including on other machines.

## Known limitations

When source-content browsing finds no stored fragments, `StewardReadApplication`
returns a recovery card instead of inferring an empty document. The card offers
the existing reviewed re-extraction command, source details, and original-file
delivery when configured. PDF/image guidance identifies local OCR dependencies;
other formats suggest encoding/parser checks. These are possible causes, not a
persisted extraction diagnosis. Displaying the card runs neither parser nor
model; a separate review controls re-extraction. Tests verify empty storage,
source-specific commands, and format hints after reconstructed source navigation.

The tool-agent `ToolNode` uses a fixed JSON error message for handled argument
validation and execution exceptions. It does not return exception strings or
validation payloads to the model. Its callable handler explicitly re-raises
`GraphBubbleUp`: the installed ToolNode wrapper-level catch would otherwise
convert an intentional interrupt into an ordinary failure.
The error is marked as a failed ToolMessage
and explicitly warns against claiming success or assuming no side effects;
the graph may then synthesize a bounded explanation. No automatic write retry
is introduced. Tests verify both invalid arguments and execution errors through
the graph, and that LangGraph interrupts still propagate as pauses. The interrupt
regression also resumes with `Command(resume="accepted")`, checks that work after
the interrupt has not run before the decision, and observes one successful
ToolMessage retaining the original call ID before the model answers. This is an
in-memory synthetic tool test; it does not establish exactly-once external writes
or durable recovery for every tool. This guard
does not sanitize successful tool return values or replace per-tool privacy
policy. It also does not by itself diagnose the underlying local failure.

`CalendarReadToolService` returns fixed, secret-free error payloads when client
creation, API requests, or event projection fails. Provider exception strings
are deliberately excluded: they may contain OAuth paths, tokens, request URLs,
or personal identifiers and would otherwise be sent back to the model as tool
results. Search projection now runs inside the same exception boundary as the
request. Tests call the actual LangChain tool wrappers with synthetic sensitive
diagnostics at each failure stage. Responses explicitly say current event data
was not retrieved; they do not fabricate empty calendars or attempt authorization.

Calendar event projections retain optional location and description for Telegram
detail cards. `CalendarService._event_from_api()` maps these fields without new
database storage. `_event_card()` shows up to 500 location characters and 2,000
description characters, explicitly labeling truncation. Existing card rendering
handles escaping; provider text is not executed as markup or instructions.
The model-facing Calendar tool projection remains its existing explicit field
allowlist and does not include these additional details. Tests cover absent
fields, populated fields, the unchanged tool projection, and bounded presentation.

Calendar read failures return a transport-neutral recovery card with explicit
Retry, Upcoming, and Integrations actions. `StewardCalendarApplication` preserves
the selected opaque event ID on failure: a transport/OAuth exception does not
prove deletion. Retrying fetches current provider data, never cached event text;
event cards also expose Refresh. The failure boundary does not disclose provider
exceptions or start browser authorization. Tests simulate outage then recovery
with reconstructed application/context objects. Deleted events may keep failing
until the user selects another event; differentiated provider error categories
remain future work.

- Capture supports Markdown, plain text, DOCX, HTML, images, and PDFs. PDFs use native text first;
  an image-only PDF falls back to local OCR at 200 DPI using separately installed Poppler (`pdftoppm`)
  and `tesseract`. Unavailable OCR never prevents preservation of the original: scanning logs the
  extraction failure and keeps its derived text empty. The content hash prevents unchanged images or
  scanned PDFs from being OCRed again during a normal reindex.
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
- Citation verification proves only that each inline key refers to a fragment
  supplied in the current request. It cannot prove that every factual sentence
  is supported by the cited fragment; semantic entailment remains future work.
- Context is bounded by characters rather than token counts. Different models
  tokenize text differently, so the cap is protective rather than exact.
- Model availability, free-tier quotas, rate limits, and retention terms are
  provider-controlled. Steward requires an explicit model name rather than
  assuming a particular Gemini model is available to every account.
- Telegram conversation state uses a local SQLite LangGraph checkpointer, so a
  restarted process can continue a chat thread. The adapter also has a local
  allowlist, durable successful-update deduplication, and a metadata-only
  dead-letter table. It limits repeated failures to three attempts, which can
  be inspected with `steward telegram-dead-letters`. Failed updates wait 15,
  then 30 seconds, then up to a five-minute capped exponential delay before a
  redelivered update can be claimed again.
- Telegram attachments and substantial text use provisional intake by default;
  their default analysis boundary is `no_model`, and `/intake_analysis` can
  explicitly permit a local or external model before saving. `/save` remains an
  explicit immediate-capture shortcut. The normal Bot API
  download ceiling still applies. An oversized upload can be selected through
  explicit Drive search/import, but there is no self-hosted Bot API server or
  automatic Drive sync.
- Organization matching has a deterministic filename fallback plus a
  model-assisted existing-workspace proposal on Telegram capture when the
  source permits the configured model. The model sees source fragments and
  known workspace IDs, never constructs a path, and deterministic validation
  rejects invalid output. Telegram supports explicit `yes`/`no` decisions and
  a durable **Change workspace** step that accepts an existing workspace name,
  then redraws a fresh approval card. It still does not infer a filesystem path
  or move an original without visible approval.
- Travel extraction recognizes a small, label-oriented itinerary shape. It is
  not a general airline-document parser; Calendar events require a travel
  record plus explicit proposal approval or an explicit CLI write command.
- Receipt records recognize a conservative labeled shape (merchant, total,
  currency, purchase date, and receipt number), retain field-level provenance,
  and are created only after review. They are not a general receipt
  understanding system and do not infer unlabeled totals.
- Warranty records use `propose-warranty-record`, `create-warranty-record`,
  and `warranty-records`. They retain source-fragment evidence for a labeled
  product, provider, warranty number, or coverage-end timestamp. They do not
  infer warranty duration or eligibility from marketing language.
- The read-only agent tool `search_records` now returns travel, receipt, and
  warranty result shapes, marked with `record_type`. It performs no mutation
  and applies the source privacy policy before a result can reach a cloud model.
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

1. Add structured answer evaluation cases that inspect retrieved evidence,
   generated citations, and unsupported-answer behavior.
2. Add richer extractors for plain text and PDF before introducing broad
   capture channels.
3. Add structured retrieval evaluations before relying on semantic results for
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
