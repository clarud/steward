# ADR-006: Source-centric mode is the active product surface

## Status

Accepted — 2026-09-23

## Context

Steward accumulated implementations for workspaces, knowledge, records, tasks,
Calendar, research, and action proposals while its most useful proven workflow
became local source adoption, change-aware retrieval, and Telegram access. A
broad default interface makes the product harder to learn, test, and trust. It
also overlaps with tools such as Codex that are better suited to intentional
filesystem manipulation.

## Decision

The default `source_centric` product mode composes only source roots, scanning,
extraction, retrieval, grounded answers, local Inbox capture, source privacy,
activity, explicit Drive/Gmail imports, and a bounded source/activity read-only
agent. Legacy domains remain in the codebase and their data is not deleted, but
they are omitted from default CLI help, Telegram composition, and tool-agent
schemas. `legacy` remains an explicit local development mode.

## Consequences

Documentation and acceptance testing describe the source-centric path first.
Existing roots and source IDs remain intact. New broad automation is not added
to the active interface without a deliberate product decision. This reduces
surface area now while retaining reversible implementation options later.
