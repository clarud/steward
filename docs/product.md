# Product

Steward is a single-user, local-first companion to Codex. Codex works *on*
files: it organises, renames, moves, and edits them. Steward gets files **in**
and back **out**: it indexes the folders you already have, lets you find a file
by what it says rather than what it's called, and delivers it to you through
Telegram.

## The problem

> "I have information somewhere, but I don't remember its name, location,
> format, or exact wording. Help me find and understand it, even when I'm away
> from my computer."

## The loop

```text
authorize a folder → scan and extract → search by words or meaning
→ read, summarise, ask, or send the original → upload new material to the Inbox
→ Codex organises it → Steward reconciles the move on the next scan
```

## What Steward does

- Registers explicitly authorized folders without copying or moving anything.
- Extracts Markdown, text, code, notebooks, PDF (including OCR for scans), DOCX,
  PPTX, XLSX, HTML, email, and images, keeping precise locations for citations.
- Searches lexically, semantically, or both, scoped by file type and folder.
- Answers questions and summarises files with citations to the exact sections
  used.
- Works from Telegram: search, open, read, summarise, send originals, upload to
  the Inbox, change privacy, and import a chosen Drive file or Gmail message.
- Tracks changes made by Codex and other tools, and preserves a file's identity
  across a reviewed rename or move.
- Keeps `INBOX.md` listing what's waiting to be filed, with where you wanted
  each file to go, so Codex can organise it on your computer.

## What Steward deliberately does not do

- Move, rename, create, or delete your files. That is Codex's job, with your
  approval.
- Run shell commands or give a model filesystem access.
- Treat a folder's `AGENTS.md` or other guidance file as instructions for
  itself.
- Sync to the cloud or serve multiple users.
- Manage tasks, calendars, travel records, or a knowledge graph. These were
  explored and removed (see [ADR-007](adr/ADR-007-remove-legacy-domains.md)).
