# Steward: guide for coding agents

## Product in brief

Steward is a local-first companion to Codex for personal files. Codex works
*on* files (organise, rename, move, edit). Steward gets files **in** (Telegram
uploads, explicit Drive/Gmail imports) and back **out** (plain-language search,
reading, summaries, grounded answers, sending originals) from folders the owner
has authorized. Read [docs/product.md](docs/product.md) before changing
behaviour.

The owner builds Steward partly to learn agentic AI. Product velocity is
secondary to understanding, so explain design choices and keep slices small.

## Rules

1. **Originals are authoritative.** Never modify, move, rename, or delete a
   user file. SQLite holds only metadata and rebuildable derived data.
2. **Authorized roots only.** Source code reads only authorized roots and the
   configured Inbox. No model gets filesystem or shell access.
3. **Models propose, code executes.** Hashing, extraction, database writes,
   imports, and recovery are deterministic Python.
4. **Privacy before context.** A model sees only retrieved fragments that the
   source's `PrivacyRule` allows for that model (cloud vs local).
5. **Provenance on every answer.** Answers and summaries cite fragment keys, and
   citations are verified against the context actually supplied.
6. **Scans reconcile.** After external changes, a full `scan-root` is the truth.
   Watchers only hint. Move proposals need review.
7. **No new domains without an ADR.** Workspaces, knowledge graphs, records,
   tasks, Calendar, and research were removed in
   [ADR-007](docs/adr/ADR-007-remove-legacy-domains.md). Do not re-add them, or
   similar features, without a new ADR.
8. **Migrations are append-only.** Never edit or delete an existing entry in
   `MIGRATIONS`. Add destructive migrations to `DESTRUCTIVE_MIGRATIONS` so an
   existing database is snapshotted first.
9. **Least privilege for agents.** The tool agent's allowlist is
   `search_sources`, `read_source`, and `search_activity`. Any new tool is
   read-only unless an ADR says otherwise.

The full list is in [docs/invariants.md](docs/invariants.md).

## Module map

```text
src/steward/
├── cli/              parser.py, commands.py (main), bootstrap.py (service composition)
├── app/              use cases behind Telegram
│   ├── events.py     router: one handle() per incoming event
│   ├── read.py       sources, Inbox, search, content, summaries, activity
│   ├── intake.py     staged uploads, /save, Drive/Gmail imports
│   ├── roots.py      authorized roots and move review
│   ├── privacy.py    reviewed privacy-rule changes
│   ├── question.py   grounded question → retrieval graph
│   └── agent.py      explicit /agent tool loop
├── telegram/         transport: adapter, callbacks, delivery ledger, presentation
├── sources/          Source model, discovery, hashing, scanning, moves, Inbox queue, export
├── extraction/       per-format extractors and fragment/FTS5 repository
├── retrieval/        lexical (FTS5), semantic (embeddings), hybrid (RRF)
├── answer/           context building, citations, model gateways and routing
├── graphs/           LangGraph: retrieval_answer, tool_agent, provider tool adapters
├── tools/            read-only tool service and tool policy
├── storage/          migrations, snapshot, restore
├── intake.py, capture.py, privacy.py, activity.py, roots.py, reviews.py
├── drive.py, gmail.py    optional Google imports (extra: google)
└── config.py, extras.py, logging.py, observability.py, runtime.py
```

`steward.cli.bootstrap.build_telegram_application` is the single place where
the Telegram application is wired. Add a new use case there and to
`StewardEventApplication`, not inside the transport.

## Working in this repo

```powershell
.venv\Scripts\python -m pytest            # full suite, about 40 s
.venv\Scripts\python -m pytest tests/test_application.py  # narrow runs while iterating
.venv\Scripts\python -m pyflakes src tests # unused imports and undefined names
```

- Tests use temporary databases and folders. Never point a test or experiment
  at `.steward/` or a real vault. Use a copy.
- Do not call a real model, Telegram, or Google in tests. Use fakes, as the
  existing tests do.
- Match the surrounding code: small repositories own SQL, services own rules,
  `app/` modules turn them into `PresentedReply` cards.
- Telegram replies never include absolute local paths.
- Optional packages are imported lazily and raise `MissingExtraError` with the
  extra's name when missing.

## How to take on a task

1. **Design first, without code:** the requirement, where it belongs, the data
   flow, files to change, failure modes, and alternatives. Keep scope to the
   task.
2. **Implement the smallest slice** with tests. No speculative abstractions, no
   unrelated refactors, and LangGraph stays out of domain services.
3. **Explain the diff** to its future maintainer: why each changed file exists
   and what state, side effects, and failure behaviour changed.

A feature is done when it has tests, preserves provenance, is idempotent where
it must be, controls its side effects, breaks no invariant, and its control flow
can be explained.

## Where things are documented

- [README.md](README.md): positioning, setup, everyday use.
- [docs/architecture.md](docs/architecture.md): layers and data flow.
- [docs/developer-guide.md](docs/developer-guide.md): implementation details per subsystem.
- [docs/testing.md](docs/testing.md) and [docs/telegram-manual-test-checklist.md](docs/telegram-manual-test-checklist.md).
- [docs/adr/](docs/adr/): decisions. Add one for any product-shaping change.
