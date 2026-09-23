# Steward

Steward is a local-first **source memory and retrieval assistant**. Point it at an
existing directory of notes, course materials, documents, or project files; it
records what is there, extracts supported content locally, tracks changes, and
makes the collection searchable from the CLI or Telegram.

It complements coding agents and file managers. Codex can create, move, rename,
and edit files; Steward reconciles those changes on a scan and helps you find
and read the material later—even when you do not remember its filename.

## Active product surface

- Register one or more explicitly authorized local roots.
- Scan and reconcile Markdown, text, source code, notebooks, PDF, DOCX, PPTX, XLSX, HTML, image/OCR, and supported
  imported files without copying existing roots.
- Preserve source identity, hashes, paths, extraction fragments, and provenance
  in SQLite while originals stay in their human-readable locations.
- Search lexically, semantically, or with hybrid retrieval; generate grounded
  answers with fragment citations.
- Use Telegram to search, open source details/content, ask grounded questions,
  upload files or short notes into a local Inbox, and explicitly import Drive or
  Gmail material.
- Apply per-source privacy rules before a remote model receives any content.
- Use the read-only tool agent for bounded source and activity lookup.

`source_centric` is the default runtime mode. Earlier Workspace, Knowledge,
Record, Task, Calendar, research, and broad action implementations remain in
the repository as retained legacy code, but are not composed into the default
CLI, Telegram, or agent experience.

## Quick start

Create and activate a virtual environment, install the project, then configure
`.env` from `.env.example`. `STEWARD_PRODUCT_MODE=source_centric` is the
default.

```powershell
steward onboard-root "Y4S1" "C:\Users\clare\OneDrive\Desktop\Y4S1"
steward scan-root "Y4S1"
steward sources
steward search "parallel scheduling"
steward hybrid-search "the cache CPUs use for address translation"
steward ask "What do my notes say about queueing?"
```

Run `steward telegram` to use the local service through your configured,
allowlisted Telegram bot. The service uses polling locally; originals, SQLite,
tokens, and model configuration stay on the machine running Steward.

## Telegram workflow

Use natural requests such as “find my CS3210 queueing notes”, “show that PDF”,
or “read section 3”. Send a document or a short note to stage it in the local
Inbox, review what would be saved, then explicitly accept it. The active bot
also exposes source browsing, root status, search, source privacy changes, and
explicit Drive/Gmail imports.

Before saving a staged capture, **Intended root** can retain optional routing
context such as a course folder. It does not move the file: the original remains
in Inbox until a separate reviewed workflow handles it.

Use `/codex_handoff` in Telegram to pick a saved Inbox source, or supply source
IDs for a deliberate batch. Steward writes a local metadata-only manifest; it
does not contact Codex or change files.

Source cards show a safe root-relative or Inbox-relative location and whether
derived text is ready to read. They never reveal an absolute local path in
Telegram.

Search can be scoped locally, for example `/hybrid_search TLB --type pdf --root
"CS3210"`. Supported types include `markdown`, `pdf`, `docx`, `pptx`, `xlsx`,
`notebook`, `html`, `image`, and `code`.

If ordinary lexical search has no matching extracted text, Steward also checks
the local registered filename/path metadata and shows clearly labelled filename
matches. It does not read unindexed file content to do this.

Telegram is an interface, not the storage location: uploads are downloaded to
the configured Inbox, and no remote model analyzes a source unless its privacy
rule permits the chosen model.

## Operations

```powershell
steward roots
steward scan-root "Y4S1"
steward watch-root "Y4S1"
steward reconcile-moves "Y4S1"
steward review-move 1 --accept
steward codex-handoff 6 12 --note "Review these before organizing lecture notes"
steward health --strict
steward relocate-root "Y4S1" "D:\Archive\Y4S1" --confirm
```

Run a scan after external edits, renames, or moves made by Codex or another
tool. The watcher incrementally refreshes supported files; full scans remain the
authoritative reconciliation mechanism for missed events, moves, and large
batches. A same-root rename or move with one unambiguous content-hash match is
shown as a reviewable move proposal; accepting it preserves the old source ID.
Root status shows the timestamp and concise new/updated/unchanged/missing counts
from the latest successful full scan.

Optional root profiles are local descriptive metadata: use `set-root-profile`
to record a purpose, existing root-contained guidance files, and ordered
authority labels. They are included only in a metadata-only Codex handoff; they
are never executable instructions.
`codex-handoff` creates a local JSON manifest containing selected source
metadata and applicable root guidance paths. It never sends content to Codex or
executes a Codex session.

## Documentation

- [Source-centric pivot plan](docs/source-centric-pivot-plan.md) — active
  product direction and staged migration.
- [Product](docs/product.md), [architecture](docs/architecture.md), and
  [invariants](docs/invariants.md) — current product contract.
- [Developer guide](docs/developer-guide.md) — implementation details, including
  retained legacy subsystems clearly marked as non-default.
- [Telegram checklist](docs/telegram-manual-test-checklist.md) and
  [testing ledger](docs/testing.md) — manual acceptance procedures and outcomes.

## Data and safety model

Original files are authoritative. SQLite stores operational metadata and
rebuildable derived state such as fragments, indexes, embeddings, and model
summaries. Source paths are only accessed when you explicitly authorize their
root. Consequential filesystem or external actions are outside the active
source-centric surface.
