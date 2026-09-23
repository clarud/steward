# Steward: guide for coding agents

## Product in brief

Steward is a local-first companion to Codex for personal files. Codex works
*on* files (organise, rename, move, edit). Steward gets files **in** (Telegram
uploads to a local Inbox listed in `INBOX.md`) and back **out** (Find, Read,
Summarize, Ask, Send original) from folders the owner has authorized. Read
[docs/product.md](docs/product.md) before changing behaviour.

The owner builds Steward partly to learn agentic AI. Explain design choices and
keep slices small. The product is deliberately minimal: every feature must be
well implemented and meaningful, or it goes.

## Rules

1. **Originals are authoritative.** Never modify, move, rename, or delete a
   user file. SQLite holds only metadata and rebuildable derived data.
2. **Authorized roots only.** Code reads only authorized roots and the Inbox.
   No model gets filesystem, shell, or tool access.
3. **Models judge, code acts.** Model roles return JSON validated against a
   contract (`roles/structured.py`). A judge picks only from supplied IDs, and
   answers cite only supplied keys. Hashing, extraction, database writes, move
   reconciliation, and fallbacks are deterministic Python.
4. **Budgets and fallbacks.** Every flow has a `CallBudget`. Every role has one
   repair attempt and then a plain fallback. A flow never loops without a bound.
5. **Provenance on every answer.** Answers and summaries cite fragment keys, and
   the checker removes cited statements that aren't supported.
6. **Scans reconcile.** A scan (every 15 minutes in the bot, or `scan-root`) is
   the truth. A move is merged only when exactly one missing and one new file
   share a hash.
7. **Minimal surface.** Removed features (ADR-007, ADR-009) include workspaces,
   tasks, calendar, research, Drive/Gmail import, per-file privacy, action
   proposals, the `/agent` tool loop, handoff manifests, and move review. Do not
   re-add them, or similar features, without a new ADR.
8. **Migrations are append-only.** Never edit or delete an entry in
   `MIGRATIONS`. Add destructive migrations to `DESTRUCTIVE_MIGRATIONS` so an
   existing database is snapshotted first.

The full list is in [docs/invariants.md](docs/invariants.md).

## Module map

```text
src/steward/
├── cli/          parser.py, commands.py (main), bootstrap.py (composition, build_flows)
├── app/          use cases behind Telegram, returning PresentedReply cards
│   ├── events.py     router: commands, plain-text choice card, replies to cards
│   ├── files.py      home, /sources, /browse, /inbox, file card, read, send original
│   ├── answers.py    runs Find/Ask/Summarize and renders their cards
│   ├── search.py     parse_find: /find flags → FindScope
│   └── intake.py     staged uploads: Save, Intended root, Add note, Discard
├── graphs/       LangGraph flows: find.py, ask.py, summarize.py
├── roles/        model roles and contracts: find, ask, checker, summarize, structured
├── retrieval/    lexical (FTS5), semantic (embeddings), hybrid (RRF), files (per-file fusion)
├── sources/      model, discovery, scanning, moves, inbox_queue (INBOX.md), summaries, export
├── extraction/   per-format extractors and the fragment/FTS5 repository
├── answer/       model gateways (Gemini, OpenAI, SoCLaaS, Ollama)
├── telegram/     adapter (polling, 15-minute rescan), callbacks, delivery ledger, presentation
├── storage/      migrations, snapshot, restore
└── intake.py, capture.py, roots.py, reviews.py, activity.py, evaluation.py,
    config.py, extras.py, logging.py (token redaction), observability.py, runtime.py
```

`steward.cli.bootstrap.build_telegram_application` is the single place where
the Telegram application is wired, and `build_flows` builds the three graphs
for both the CLI and Telegram.

## Working in this repo

```powershell
.venv\Scripts\python -m pytest             # full suite, about 30 s
.venv\Scripts\python -m pytest tests/graphs # narrow runs while iterating
.venv\Scripts\python -m pyflakes src tests  # unused imports, undefined names
```

- Tests use temporary databases and folders. Never point a test or experiment
  at `.steward/`. Use a copy (see [docs/testing.md](docs/testing.md)).
- Do not call a real model or Telegram in tests. Flow tests use a scripted model
  that returns queued JSON and fails if the flow makes more calls than scripted.
- Match the surrounding code: repositories own SQL, services own rules, and `app/`
  modules turn results into cards.
- Telegram replies never include absolute local paths.
- Optional packages are imported lazily and raise `MissingExtraError`.

## How to take on a task

1. **Design first:** the requirement, where it belongs, the data flow, failure
   modes, and alternatives.
2. **Implement the smallest slice** with tests. No speculative abstractions, and
   LangGraph stays out of domain services.
3. **Measure model changes.** A change to a role prompt or flow shape is
   checked with `steward evaluate-retrieval CASES --mode find` on a copy of real
   data, against `--mode hybrid`.
4. **Explain the diff:** why each changed file exists, and what state, side
   effects, and failure behaviour changed.

## Where things are documented

- [README.md](README.md): positioning, setup, everyday use.
- [docs/architecture.md](docs/architecture.md): layers and data flow.
- [docs/multi-agent-flows.md](docs/multi-agent-flows.md): Find, Ask, Summarize.
- [docs/developer-guide.md](docs/developer-guide.md): subsystem details.
- [docs/testing.md](docs/testing.md) and [docs/telegram-manual-test-checklist.md](docs/telegram-manual-test-checklist.md).
- [docs/adr/](docs/adr/): decisions. Add one for any product-shaping change.
