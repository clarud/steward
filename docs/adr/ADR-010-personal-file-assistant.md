# ADR-010: Steward is a personal file assistant

## Status

Accepted — 2026-09-26

## Context

Steward was described as "a local-first companion to Codex": Codex organises
files, and Steward gets them in and back out. Two parts of that no longer fit:

- Nothing in Steward depends on Codex. Rescans and move reconciliation keep up
  with any change to the folders, whether made by hand, by a sync tool, or by a
  coding agent, and `INBOX.md` is readable by a person or any tool.
- "Local-first" overstated it. Files, the database, and the Inbox stay on the
  owner's computer, but Find, Ask, and Summarize send text to the configured
  model provider, which is usually a cloud service.

## Decision

Describe Steward as **a personal file assistant: find, understand, and retrieve
your files from anywhere**, through Telegram, from the folders you already
have. Codex is named only as one example of a tool that may organise the files.
Say plainly what stays on the machine and what a model provider receives.

No code changes: the product already behaved this way.

## Consequences

The README, product description, AGENTS.md, architecture, and the Telegram
checklist use this positioning. Earlier ADRs (006–009) keep their original
wording as a record of the decisions made at the time.
