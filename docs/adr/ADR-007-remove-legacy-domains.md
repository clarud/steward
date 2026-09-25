# ADR-007: Remove legacy domains

## Status

Accepted — 2026-09-24

## Context

ADR-006 made source memory and retrieval the active product but kept the
workspace, organization, knowledge, record, task, Calendar, research, web UI,
and broad-action code behind a `STEWARD_PRODUCT_MODE=legacy` switch. That code
was over half of `application.py` and about half of the test suite, and the
documentation described two products at once. Steward is now positioned as the
companion to Codex: Codex organises files, Steward gets them in and back out.

## Decision

- Delete the legacy modules, application classes, CLI commands, Telegram
  commands, tool definitions, and their tests. Remove `STEWARD_PRODUCT_MODE`;
  a leftover value is ignored with a warning.
- Keep `action_proposals`, limited to reviewed source-privacy changes.
- Schema migration 65 drops the legacy tables and removes non-privacy action
  proposals. Before it runs on an existing database, `initialize_database`
  writes a snapshot to `DATA_DIR/backups/pre-migration-65-<timestamp>/`. If the
  snapshot fails, no migration runs.
- Activity rows written by retired features stay readable: unknown event types
  load as pseudo-members of `ActivityType`.
- Make Google APIs, Gemini, and sentence-transformers optional extras.
- The last commit containing the legacy code is tagged `legacy-final`.

## Consequences

The codebase describes one product. Recovering a retired feature means starting
from `legacy-final` and the pre-migration snapshot, then making a new product
decision. New domains need a new ADR before they are added.
