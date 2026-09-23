# Steward Developer Guide

How the current implementation works, subsystem by subsystem. For the big
picture read [architecture.md](architecture.md); for the rules every change
must keep, read [invariants.md](invariants.md) and [AGENTS.md](../AGENTS.md).

## Design principles

1. Original files are authoritative. SQLite holds metadata and rebuildable
   derived data only.
2. Every derived result keeps provenance: a hit names its `SourceFragment`,
   which names its `Source` and location.
3. Deterministic code does filesystem and database work. No model decides what
   is written, read, or deleted.
4. Infrastructure hides behind small protocols (`SemanticIndex`,
   `EmbeddingProvider`, `ModelGateway`), so each layer is testable without a
   model, network, Telegram, or LangGraph.

## Configuration

`Settings.from_environment()` (`config.py`) reads the environment once per
command and never creates paths. `.env` is loaded without overriding variables
already set in the shell.

| Variable | Purpose |
|---|---|
| `STEWARD_DATA_DIR` | Data directory, default `.steward` |
| `STEWARD_INBOX_DIR` | Inbox for uploads and imports, default `vault/inbox` |
| `STEWARD_LOG_LEVEL` | `DEBUG` … `CRITICAL`, default `INFO` |
| `STEWARD_MODEL_PROVIDER` | `gemini` (default), `openai`, `soclaas`, or `local` |
| `GEMINI_API_KEY`, `STEWARD_GEMINI_MODEL` | Gemini |
| `OPENAI_API_KEY`, `STEWARD_OPENAI_MODEL` | OpenAI |
| `SOCLAAS_API_KEY`, `SOCLAAS_MODEL`, `SOCLAAS_BASE_URL` | SoCLaaS (`STEWARD_SOCLAAS_*` aliases accepted) |
| `STEWARD_LOCAL_MODEL`, `STEWARD_LOCAL_MODEL_URL` | Local Ollama |
| `TELEGRAM_BOT_TOKEN`, `STEWARD_TELEGRAM_ALLOWED_CHAT_IDS` | Telegram bot and chat allowlist |
| `STEWARD_GOOGLE_CLIENT_SECRETS` | OAuth desktop-client JSON for Drive/Gmail |

API keys are never part of `Settings`, so they cannot be logged with it.

The data directory contains `steward.db` (operational), `checkpoints.db`
(LangGraph conversation state), `telegram-runtime.db` (single-poller lock),
`backups/`, `cache/intake/` (staged uploads),
`config/` (OAuth tokens), and `logs/`.

## Sources and roots

A `Source` (`sources/models.py`) is one original file: `path`, SHA-256
`content_hash`, `source_type`, `size_bytes`, `modified_at`, `first_seen_at`,
`last_seen_at`, and `status` (`active` or `missing`). A filename is location
metadata, not identity. Two paths with identical hashes are duplicates that
may both be intentional.

**Roots.** `SourceRootRepository` (`roots.py`) records each authorized
directory by name, with optional root-relative exclusions and an enabled flag.
`onboard-root` authorizes and scans in one step. Each full scan appends a
`source_root_scans` row with `new`/`updated`/`unchanged`/`missing` counts, which
`steward roots` and Telegram root cards show. `relocate-root NAME PATH --confirm`
rebinds a root that moved outside Steward: in one write transaction it checks
that every registered file exists under the new directory with the same
SHA-256, then rewrites the paths and keeps every ID. Stop the poller and
watcher first. Optional **root profiles** (`set-root-profile`) store a purpose,
existing guidance files inside the root, and authority labels. They appear only
as filing guidance in `INBOX.md`.

**Scanning** (`sources/scanning.py`, `sources/service.py`). Discovery walks the
root for supported suffixes in sorted order. For each file: new path → add;
changed hash or metadata → update and re-extract; unchanged → refresh
`last_seen_at`. Active rows no longer found become `missing`. Scans are
idempotent.

**Moves** (`sources/moves.py`). After a scan, `SourceMoveReconciliationService`
pairs a missing source with a new source in the same root when exactly one
content hash matches on each side. The proposal records its root. Accepting it
(`review-move ID --accept` or `/moves`) keeps the old source ID, appends to
`source_location_history`, and is refused if either path has left the reviewed
root. Duplicate content never produces a proposal.

**Watching** (`file_watching.py`). `watch-root` debounces `watchdog` events and
then hashes the path before changing anything. Events are hints; `scan-root` is
the reconciliation authority.

## Extraction

`extraction/document.py` picks an extractor from `SourceType` and stores ordered
`SourceFragment`s (`heading`, `ordinal`, `text`, `location`):

| Format | Location |
|---|---|
| Markdown | heading plus line range |
| Plain text, code | bounded line ranges |
| PDF | page; scanned PDFs use `pdftoppm` + Tesseract OCR |
| DOCX | paragraph ranges |
| PPTX | slide number |
| XLSX | sheet and row |
| Notebook | markdown/code cell |
| HTML, email | readable sections; email reads non-attachment bodies |
| Image | Tesseract OCR |

`SourceFragmentRepository.replace_for_source()` deletes and rewrites a source's
fragments and FTS5 rows in one transaction. It never appends. A parser failure
leaves the source registered with no fragments until a later successful
`reextract`, and Telegram shows format-specific recovery guidance.

## Storage

`storage/database.py` keeps an append-only `MIGRATIONS` tuple and a
`schema_migrations` ledger. `initialize_database()` applies only unrecorded
versions. Never edit an existing migration.

Migrations listed in `DESTRUCTIVE_MIGRATIONS` delete user data. Before one runs
on an existing database, `_snapshot_before_destructive_migrations()` writes
`DATA_DIR/backups/pre-migration-<version>-<timestamp>/steward.db`. If that
snapshot fails, nothing is migrated. Migration 65 is the only destructive one:
it drops the legacy tables (ADR-007).

`snapshot_database()` uses SQLite's backup API from a read-only connection and
reserves its destination with exclusive creation, so it never overwrites.
`restore_database()` validates the candidate (`quick_check`, an application
table, and a matching operational/checkpoint role), then writes a safety copy
before replacing anything. `steward backup` snapshots both databases in
sequence; stop writers for a consistent pair.

## Retrieval

- **Lexical** (`retrieval/lexical.py`): FTS5 `MATCH` with BM25, where lower is
  better. Strong for names, acronyms, and rare terms.
- **Semantic** (`retrieval/semantic.py`): `all-MiniLM-L6-v2` embeddings (384
  dimensions) stored as JSON in `source_fragment_embeddings`, keyed by model
  name and dimension, compared by cosine similarity. The model loads from the
  local cache; only `download-embedding-model` may download it. Needs the
  `semantic` extra.
- **Hybrid** (`retrieval/hybrid.py`): reciprocal-rank fusion, `1 / (60 + rank)`
  per list. The fused score ranks results; it is not a probability.

All three modes accept the same scope: `--type TYPE` (repeatable) and
`--root "NAME"`, resolved only against authorized roots. Users never supply a
raw path. When lexical search finds no content, `/search` falls back to
registered filenames and relative paths, labelled **Filename matches**, without
opening any file.

`steward evaluate-retrieval VAULT CASES.yaml [--mode hybrid]` reports Recall@5,
MRR, and every miss for hand-written cases (`tests/evaluation/`).

## Grounded answers and summaries

```text
question → HybridRetriever → privacy filter → ContextBuilder → ModelGateway → verify citations
```

- `AnswerService` (`answer/service.py`) returns a fixed "not enough local
  information" reply without calling a model when retrieval finds nothing.
- `ContextBuilder` labels each fragment `[F1]`, `[F2]`… with filename, heading,
  and location, capped at 12,000 characters (excerpts marked `[truncated]`).
  Local directory structure is not sent.
- `verify_citations` keeps only citations the answer actually used. A missing
  or unknown key is flagged, and for summaries the text is withheld.
- `ModelRouter` sends evidence from `local_model_only` sources to the local
  Ollama gateway, or declines if none is configured. `no_model` fragments are
  removed before context is built. `external_redacted` currently fails closed.
- **Summaries and "Ask about it"** (`app/read.py`) use one source's fragments.
  Over 60,000 characters they go through multi-pass `synthesize_long_document`,
  and access is re-checked before the reply is shown.

## LangGraph workflows

- `graphs/retrieval_answer.py`: prepare → retrieve → answer | no_evidence, with
  a SQLite checkpointer per thread (`cli:ask`, or per Telegram chat), so
  follow-ups can use recent sources. Nodes call ordinary services.
- `graphs/tool_agent.py`: one model ↔ `ToolNode` loop, at most 6 tool calls,
  with repeated identical calls refused and a `ToolPolicy` gate on every call.
  Provider adapters (`gemini_tools.py`, `ollama_tools.py`,
  `openai_compatible_tools.py`) translate tool schemas. Reachable only through
  `/agent` and `steward agent`; ordinary questions use the grounded graph.

`observability.trace()` logs structured events (routes, fragment IDs, tool
names) without source text, prompts, or model output.

## Telegram

`telegram/adapter.py` normalises each update into a platform-neutral
`IncomingEvent` and runs the synchronous application in a worker thread.

- **Router** (`app/events.py`): tries, in order, privacy, roots, moves, Codex
  `/agent`, staged intake, source/activity references and read
  commands, then Drive and Gmail. After that the deterministic `IntentResolver`
  maps text to search, Inbox/activity, a grounded question, capture, or a
  staged note.
- **Delivery ledger** (`telegram/delivery.py`): each update ID is claimed as
  `processing` before handling and marked `delivered` after the reply. Failures
  release the claim so Telegram can retry. This is at-least-once, so handlers
  must be idempotent. Retries are bounded, and exhausted updates become dead
  letters (`telegram-dead-letters`, `telegram-recover-dead-letter`).
- **Buttons** (`telegram/callbacks.py`): callback data is an opaque,
  chat-scoped, expiring token mapped to a locally stored command, so no command
  text travels in the button.
- **References** (`reviews.py`): each object card stores a `(kind, id)` pointer
  against its sent message ID, keeping the newest 500 per chat. Replying to a
  card restores that pointer, so "give me the content" or "send that pdf"
  targets the right source after a restart. `ReviewContextRepository` holds the
  chat's current selection.
- **Presentation**: `PresentedReply` carries text, buttons, title, icon, an
  optional reference, and an optional document for **Send original**
  (`sources/export.py` refuses files outside authorized roots or the Inbox).
- **Runtime lock** (`runtime.py`): an exclusive SQLite transaction in
  `telegram-runtime.db` prevents two pollers on one data directory. It cannot
  stop the same token running elsewhere.

## Inbox and imports

Telegram uploads are staged in `cache/intake/` as a `ProvisionalIntake`
(`intake.py`). The card explains the likely category without a model call and
offers a model boundary for the saved item (`none`, `local`, or `external`),
**Intended root**, extra context, save, or discard. Saving goes through
`InboxCaptureService` (`capture.py`), which writes to the Inbox, registers and
extracts the source, and stores intended root and context in
`source_inbox_contexts`. `/save` captures immediately. Captures are idempotent
by event and content hash.

Drive and Gmail (`drive.py`, `gmail.py`, extra `google`) authorize lazily with
read-only scopes, and only when the owner runs an explicit search or import. An
import downloads one chosen original into the Inbox. Google-native documents
are exported to a supported format.

## Inbox queue

`InboxQueue` (`sources/inbox_queue.py`) writes `INBOX.md` in the Inbox: one
entry per active Inbox source whose file is still there, with received time and
origin, type, intended root and path, the owner's note, and guidance files
(root-profile guidance plus any `AGENTS.md` or `COURSE_WORKFLOWS.md` at the
intended root). It contains no file contents. `InboxCaptureService` refreshes it
after every capture, intake acceptance refreshes it again once routing context
is saved, and `scan-root` and `steward inbox` refresh it too. The file is only
rewritten when its content changes, via a temporary file and atomic replace.
A file Codex moves out of the Inbox drops off at the next refresh; Telegram's
`/inbox` applies the same "still in the Inbox" rule.

## Privacy

`PrivacyService` stores one rule per source: `external_allowed` (default),
`external_redacted` (fails closed), `local_model_only`, or `no_model`. The CLI
`set-source-privacy` applies a rule directly. Telegram `/set_privacy` creates
an `action_proposals` row that must be approved with `/approve_action`, and only
from the chat that created it.

## Activity

`ActivityService` appends audit events (captures, intake decisions, privacy
changes, delivery recoveries). Details that look like paths are reduced to
filenames before they reach Telegram or a model. Event types from retired
features still load (`ActivityType._missing_`).

## Known limitations

- Citation checks confirm a key was supplied, not that the claim follows from
  the evidence.
- `external_redacted` has no redaction pipeline yet and blocks cloud models.
- A crash after domain work but before the delivery ledger updates can repeat
  that work (at-least-once delivery).
- Health checks are local. They do not probe Telegram, OAuth, or model servers.
- Steward is single-user and single-process per data directory.
