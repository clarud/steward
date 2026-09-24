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
| `STEWARD_INBOX_DIR` | Inbox for Telegram uploads, default `vault/inbox` |
| `STEWARD_LOG_LEVEL` | `DEBUG` … `CRITICAL`, default `INFO` |
| `STEWARD_MODEL_PROVIDER` | `gemini` (default), `openai`, `soclaas`, or `local` |
| `GEMINI_API_KEY`, `STEWARD_GEMINI_MODEL` | Gemini |
| `OPENAI_API_KEY`, `STEWARD_OPENAI_MODEL` | OpenAI |
| `SOCLAAS_API_KEY`, `SOCLAAS_MODEL`, `SOCLAAS_BASE_URL` | SoCLaaS (`STEWARD_SOCLAAS_*` aliases accepted) |
| `STEWARD_LOCAL_MODEL`, `STEWARD_LOCAL_MODEL_URL` | Local Ollama |
| `TELEGRAM_BOT_TOKEN`, `STEWARD_TELEGRAM_ALLOWED_CHAT_IDS` | Telegram bot and chat allowlist |

API keys are never part of `Settings`, so they cannot be logged with it.

The data directory contains `steward.db`, `telegram-runtime.db` (single-poller
lock), `backups/`, `cache/intake/` (staged uploads), and `logs/`. Log output
passes through `RedactingFormatter`, which replaces Telegram bot tokens with
`bot<redacted>`; `httpx` request lines are logged only at WARNING.

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
SHA-256, then rewrites the paths and keeps every ID. Stop the bot first.
`remove-root` stops tracking a root and forgets its files; nothing on disk is
touched.

**Scanning** (`sources/scanning.py`, `sources/service.py`). Discovery walks the
root for supported suffixes in sorted order. For each file: new path → add;
changed hash or metadata → update and re-extract; unchanged → refresh
`last_seen_at`. Active rows no longer found become `missing`. Scans are
idempotent, and a file whose extraction fails is logged and skipped without
stopping the scan. The Telegram bot runs `rescan_all` every 15 minutes: every
available root, move reconciliation, `INBOX.md`, and vectors for any new
fragments (`index_missing_vectors`).

**Moves** (`sources/moves.py`). `MoveReconciler` runs after every scan. It
first marks Inbox files that have left the Inbox as missing (the Inbox itself is
not scanned). Then, for each content hash with exactly one missing source and
exactly one present source first seen after the missing one was last seen, it
merges them: the old ID keeps the new path, and `source_location_history`
records the old one. Copies and ambiguous cases are left alone. `filed_notices`
tells the uploading Telegram chat where an Inbox file was filed.

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

Markdown sections longer than 3,000 characters are split at line breaks (a
single longer line is sliced), so a log or heading-less note becomes many
precisely located sections rather than one huge one that search ranks poorly.
After an extractor change, `steward reextract --all` re-extracts every active
file; a file that fails keeps its previous text.

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
snapshot fails, nothing is migrated. Destructive migrations so far: 65 (legacy
domains, ADR-007), 66 (privacy, action proposals, delivery recovery, and root
profiles), and 68 (move proposals), per ADR-009. Migration 69 adds
`source_summaries`.

`snapshot_database()` uses SQLite's backup API from a read-only connection and
reserves its destination with exclusive creation, so it never overwrites.
`restore_database()` validates the candidate (`quick_check`, an application
table, and a matching role), then writes a safety copy before replacing
anything. `steward backup` snapshots `steward.db`.

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

All three modes accept a type scope (`--type`, repeatable) and a folder scope
(`--path-prefix` on the CLI, `--root "NAME"` on Telegram `/find`, which resolves
only against authorized roots). `retrieval/files.py` turns fragment hits into
**file** candidates: `group_by_file` keeps each file's best fragment, and
`fuse` applies reciprocal-rank fusion per file across several ranked lists, with
an optional boost.

`steward evaluate-retrieval CASES.yaml --mode keyword|hybrid|find` scores a
ranking function with hit@1, hit@3, and MRR (`evaluation.py`). A case is
`{query, file}`, and `file` matches the end of the result's path.

## Model flows

Find, Ask, and Summarize are described node by node in
[multi-agent-flows.md](multi-agent-flows.md). In code:

- `roles/structured.py`: `CallBudget`, `generate_json` (validate, one repair,
  then raise), and `generate_text`.
- `roles/citations.py`: one canonical `[F12]` form; `normalize()` rewrites
  `[F1, F2]` and `[Fn: F12]` before any validation.
- `roles/find.py`, `roles/ask.py`, `roles/summarize.py`, `roles/checker.py`: one
  prompt, contract, and validator per role. Validators reject IDs or keys that
  were not supplied.
- `graphs/find.py`, `graphs/ask.py`, `graphs/summarize.py`: the graphs.
  `FindTools`, `AskTools`, and `SummarizeTools` hold the services each graph
  may use. `run_find`, `run_ask`, and `run_summarize` return plain result
  dataclasses.
- `sources/summaries.py`: `SummaryRepository` caches complete summaries by
  source, content hash, and model.
- `app/answers.py`: runs a flow and renders its card. It keeps each chat's
  last Find and Ask turn in memory for follow-ups.
- `cli/bootstrap.py`: `build_flows` builds all three graphs for the CLI and
  Telegram. Without a model gateway, Telegram's flows are disabled and say so.

`observability.trace()` logs structured events (node, decision, calls used)
without source text, prompts, or model output.

## Telegram

`telegram/adapter.py` normalises each update into a platform-neutral
`IncomingEvent` and runs the synchronous application in a worker thread.

- **Router** (`app/events.py`): uploads and intake buttons go to `app/intake.py`;
  `/find`, `/ask`, and `/note` are handled directly; a reply to a card with
  text asks about that card's file (or adds a note to a staged upload); file,
  browse, and Inbox commands go to `app/files.py`. Any other plain text gets a
  card with **Find**, **Ask**, and **Save as note**, with no model call.
- **Delivery ledger** (`telegram/delivery.py`): each update ID is claimed as
  `processing` before handling and marked `delivered` after the reply. Failures
  release the claim so Telegram can retry. This is at-least-once, so handlers
  must be idempotent. A claim left by a crashed process expires after a
  15-minute lease.
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
- **Periodic work**: `run_telegram_polling(periodic=...)` runs `rescan_all` and
  sends filed notices every 15 minutes, off the event loop.

## Inbox uploads

Telegram uploads are staged in `cache/intake/` as a `ProvisionalIntake`
(`intake.py`). The card offers **Save**, **Intended root**, **Add note**, and
**Discard**, and replying to it with text adds a note. Saving goes through
`InboxCaptureService` (`capture.py`), which writes a readable filename into the
Inbox (the note's title or the upload's name, de-duplicated), registers and
extracts the source, and records its capture key in `inbox_captures` and its
intended root and note in `source_inbox_contexts`. `/note TEXT` saves a note
directly. Captures are idempotent by capture key.

## Inbox queue

`InboxQueue` (`sources/inbox_queue.py`) writes `INBOX.md` in the Inbox: one
entry per active Inbox source whose file is still there, with received time and
origin, type, intended root and path, the owner's note, and guidance files
(any `AGENTS.md` or `COURSE_WORKFLOWS.md` at the intended root). It contains no file contents. `InboxCaptureService` refreshes it
after every capture, intake acceptance refreshes it again once routing context
is saved, and `scan-root` and `steward inbox` refresh it too. The file is only
rewritten when its content changes, via a temporary file and atomic replace.
A file Codex moves out of the Inbox drops off at the next refresh; Telegram's
`/inbox` applies the same "still in the Inbox" rule.

## Activity

`ActivityService` appends audit events (scans, captures, intake decisions). Details that look like paths are reduced to
filenames before they reach Telegram or a model. Event types from retired
features still load (`ActivityType._missing_`).

## Known limitations

- The checker uses the same model as the writer.
- Snippets sent to a cloud provider leave the machine; use `local` (Ollama) to
  keep everything local.
- A crash after domain work but before the delivery ledger updates can repeat
  that work (at-least-once delivery).
- Health checks are local. They do not probe Telegram or model servers.
- Steward is single-user and single-process per data directory.
