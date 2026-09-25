# Product

Steward is a single-user personal file assistant. It indexes the folders you
already have, lets you find a file by what it says rather than what it's
called, answers questions and summarises files with citations, and delivers
the original to you through Telegram.

## The problem

> "I have information somewhere, but I don't remember its name, location,
> format, or exact wording. Help me find and understand it, even when I'm away
> from my computer."

## The loop

```text
authorize a folder → scan and extract → /find by words or meaning
→ read, ask, summarise, or send the original
→ upload new material to the Inbox → you or your tools file it
→ Steward recognises the move on its next scan
```

## What Steward does

- Registers explicitly authorized folders without copying or moving anything,
  and rescans them every 15 minutes while the bot runs.
- Extracts Markdown, text, code, notebooks, PDF (with OCR for scans), DOCX,
  PPTX, XLSX, HTML, email, and images, keeping page, slide, or line locations
  for citations.
- **Find:** turns a vague request into a file, with a reason, using a planner,
  four retrievers, and a judge.
- **Ask:** answers from your files. Every factual statement cites a section
  that supports it; unsupported ones are removed and can be viewed.
- **Summarize:** summarises long files (up to about 770,000 characters), reports
  coverage, and caches the result until the file changes.
- Works from Telegram: find, browse, open, read, ask, summarise, send originals,
  and upload to the Inbox. Citations read as `[p.19]` and maths as Unicode.
- Keeps `INBOX.md` listing what's waiting to be filed and where you wanted it
  to go. It works with any way of organising: by hand, with a sync tool, or with
  a coding agent such as Codex. When a file is moved, Steward keeps its identity
  and tells you where an upload went.

## What Steward deliberately does not do

- Move, rename, create, or delete your files. Organising is yours to do, by
  hand or with your own tools.
- Give a model filesystem, shell, or tool access.
- Treat a folder's `AGENTS.md` or other guidance file as instructions for
  itself.
- Serve multiple users or sync anywhere.
- Import from other services, manage tasks or calendars, or run an open-ended
  agent. These were built and removed to keep the product small (see
  [ADR-007](adr/ADR-007-remove-legacy-domains.md) and
  [ADR-009](adr/ADR-009-minimal-surface-and-model-flows.md)).
