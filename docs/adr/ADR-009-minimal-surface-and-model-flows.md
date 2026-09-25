# ADR-009: Minimal surface and three model flows

## Status

Accepted — 2026-09-24

## Context

After ADR-007 and ADR-008, Steward still carried features that added code,
buttons, and tables without serving "get files in and back out":

- per-file privacy rules with reviewed action proposals;
- Drive and Gmail import;
- an open-ended `/agent` tool loop;
- admin commands for delivery dead letters and recoveries;
- a watcher;
- manual review of every move found by a scan;
- root profiles.

Answers came from a single retrieve-then-answer pipeline, and summaries were
capped. The owner accepts cloud models seeing file snippets.

## Decision

**Remove:**
- privacy rules and action proposals;
- Drive and Gmail import and the `google` extra;
- `/agent` and its tool adapters;
- delivery history, dead letters, and recoveries;
- `watch-root`, root profiles, and the LangGraph checkpoint database;
- move review.

Migrations 66 and 68 drop the tables, with a snapshot first. All four model
providers stay.

**Automate:**
- The bot rescans every root every 15 minutes.
- A move is merged automatically when exactly one missing file and one new file
  share a hash.
- If a merged file came from a Telegram upload, the chat is told where it was
  filed.

**Replace** the answer pipeline with three LangGraph flows (see
[multi-agent-flows.md](../multi-agent-flows.md)):
- **Find** (planner, four parallel retrievers, per-file fusion, judge);
- **Ask** (planner, drafter, checker);
- **Summarize** (parallel note-takers, combiner with a coverage retry, checker, cache).

Each has JSON role contracts, a call budget, and deterministic fallbacks.

Routing is by `/find`, `/ask`, and card buttons. Plain text offers a choice
instead of guessing.

## Consequences

Telegram has four primary commands and a small set of buttons. Every model
call is bounded and every model decision is validated, so a bad model reply
degrades to plain search rather than failing. Removing privacy means any file's
snippets can reach the configured provider. With a local Ollama model, nothing
leaves the machine. Find's quality depends on the planner not over-filtering,
so its guesses are soft boosts, and Find is measured against plain hybrid
search with `evaluate-retrieval`.
