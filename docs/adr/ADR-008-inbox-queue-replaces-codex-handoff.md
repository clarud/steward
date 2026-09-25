# ADR-008: The Inbox queue replaces Codex handoff

## Status

Accepted — 2026-09-24

## Context

`/codex_handoff` wrote one JSON manifest per chosen batch in
`DATA_DIR/handoffs/`. Codex can list the Inbox itself, so the manifest's only
unique value was the owner's upload context (intended root, note). Producing and
passing a manifest for every batch was friction that made that context easy to
lose.

## Decision

Steward maintains one `INBOX.md` in its Inbox listing every file waiting to be
filed, with its upload context and the intended root's guidance files. It is
refreshed on capture, intake acceptance, scans, and `steward inbox`. The
`/codex_handoff` command, `codex-handoff` CLI command, and handoff manifests
are removed. Existing manifests in `DATA_DIR/handoffs/` are left untouched.

## Consequences

Uploads from Telegram are ready on the computer without an extra step; what to
do with them is the owner's choice. Steward still writes only inside its own
data directory and Inbox, and never starts Codex.
