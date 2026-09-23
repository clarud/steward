# Telegram Manual Test Checklist

Use a harmless test root, such as a folder with one example of each format.
Run the bot with `steward telegram` and record results in [testing.md](testing.md).

## Before testing

- `steward health --strict` passes.
- The test root is authorized (`steward roots`) and freshly scanned.
- Only one poller is running for this bot token.

## 1. Start and help

1. Send `/home` and `/help`. Both describe finding files, reading, the Inbox,
   roots, moves, INBOX.md, and privacy. Nothing mentions workspaces, tasks,
   records, Calendar, or research.
2. Send `/tasks` or `/workspaces`. Steward replies with safe guidance, not an
   error or a legacy feature.

## 2. Find

1. Search a phrase from each format (Markdown, PDF, DOCX, PPTX, XLSX, notebook,
   code). The right file comes first or near the top.
2. Send a plain request such as `find my notes on queueing`. It searches without
   a slash command.
3. Run `/hybrid_search translation cache --type pdf --root "Telegram Test"`.
   Only PDFs from that root appear. An unknown root gets a safe clarification,
   never a path prompt.
4. Search a filename word that isn't in any file's text. Results are labelled
   **Filename matches**.

## 3. Open, read, summarise, send

1. Tap a result. The card shows a root- or Inbox-relative location, never an
   absolute path, and whether text is ready.
2. **Read content**, then **Next**. The location matches the page, slide,
   sheet row, or cell.
3. **Summarize**. The summary cites evidence locations.
4. **Ask about it**, then send a question. The answer is about that file only.
   Reply to a card with a question; it uses that card's file.
5. **Send original**. The actual file arrives as a Telegram document.
6. Restart the bot, reply to an old card with `give me the content`. The same
   file opens.

## 4. Ask across files

Ask a question answered by one of the test files. The answer cites fragment
keys that match the listed sources. Ask something not in any file; Steward
says it lacks local information rather than guessing.

## 5. Upload to the Inbox

1. Send a small document. It is staged, not saved. The card explains what will
   happen.
2. Choose **Intended root** and pick the test root. The card names it and says
   the file stays in the Inbox.
3. Save it. `/inbox` lists it, and its source card shows the intended root and
   context.
4. Send a short note, then discard it. No source is created.

## 6. Pick up on the computer and reconcile

1. Open `INBOX.md` in the Inbox folder. The uploaded file is listed with its
   intended root, note, and guidance files, and none of its contents. `/inbox`
   says the same files are waiting.
2. Move or rename that file within the root (by hand or with Codex), then run
   `steward scan-root "Telegram Test"`.
3. `/moves` shows the rename with old and new names. **Preserve source ID**
   keeps its identity; the source card shows the new location.
4. Move the uploaded file out of the Inbox into the root and run `steward inbox`.
   It no longer appears in `INBOX.md` or `/inbox`.
5. Two files with identical content never produce an automatic move match.

## 7. Privacy

1. On a source card choose **Privacy**, then **No model**. A review card
   appears; approve it.
2. **Summarize** is now blocked with an explanation, and **Read content** still
   works.
3. A second pending change for the same file is refused until the first is
   approved or rejected.

## 8. Drive and Gmail (only if configured)

1. `/drive_search TERM`, page with **Next**, import one file. It lands in the
   Inbox and its card opens the imported source.
2. Import the same file again. Steward points to the existing source instead of
   saving a copy.
3. Repeat with `/gmail_search`.

## 9. Agent

`/agent what did I save most recently?` answers using only search, read, and
activity tools. It never proposes changes to files.
