# Telegram Manual Test Checklist

Use a harmless test root, such as a folder with one example of each format.
Run the bot with `steward telegram` and record results in [testing.md](testing.md).

## Before testing

- `steward health --strict` passes, and a model provider is configured.
- The test root is authorized (`steward roots`) and freshly scanned.
- `STEWARD_TELEGRAM_ALLOWED_CHAT_IDS` is set, and only one poller runs for this
  bot token.

## 1. Start and help

1. The `/` menu shows only find, ask, sources, inbox, home, and help.
2. `/home` and `/help` describe Find, Ask, browsing, and uploads. Nothing
   mentions privacy, moves, agents, Drive, or Gmail.
3. An unknown command such as `/tasks` gets a short hint, not an error.

## 2. Find

1. `/find` a phrase from each format (Markdown, PDF, DOCX, PPTX, XLSX, notebook,
   code). The right file is in the card, each with a one-line reason.
2. `/find` something vague, in different words from the file ("the cache CPUs
   use for address translation"). The right file still appears.
3. `/find translation --type pdf --root "Telegram Test"`. Only PDFs from that
   root. An unknown root gets a clear message.
4. Two similar files: Find either picks the right one or asks **Which one?**
   with buttons.
5. Nonsense words: **Closest matches** or **No match**, with **Ask instead** and
   **Browse**.
6. **Open N** and **Send N** on a result work.

## 3. Plain text

Send `queueing notes` with no command. A card offers **Find**, **Ask**, and
**Save as note**, and each button does that.

## 4. Browse, open, read, send

1. `/sources` lists roots and the Inbox. **Open** a root, go into a folder,
   use **Next**, **Up**, and **All folders**.
2. A file card shows a root- or Inbox-relative location, never an absolute path.
3. **Read**, then **Next** and **Previous**. The location matches the page,
   slide, sheet row, or cell.
4. **Send original**. The actual file arrives as a document.
5. **Folder** returns to that file's folder.

## 5. Summarize

1. **Summarize** a short file. The summary cites sections, and the card says
   "Covered X of Y sections".
2. **Summarize** a long file (a 100+ page PDF). It completes, and coverage
   covers most sections. Any skipped parts are named.
3. **Summarize** the same file again. It returns at once (cached).

## 6. Ask

1. `/ask` a question answered by one test file. The answer cites it, and
   **Sources** lists the file with page or line locations.
2. `/ask` something not in any file. Steward says it couldn't find anything,
   rather than guessing.
3. Ask a follow-up ("and what about the second one?"). It uses the previous
   turn.
4. Reply to a file card with a question. The answer is about that file only.
   Restart the bot and reply to an old card; it still targets the same file.
5. When an answer says "N statements removed", tap **Show removed**. It lists
   exactly what was cut.
6. Citations read as locations (`[p.19]`, or `[2 p.19]` when several files are
   cited, matching the numbered sources), never as `[F1234]`, and maths such as
   E(W) = 1/(μ − λ) shows as symbols, not `$...$`.

## 7. Upload to the Inbox

1. Send a small document. It is staged, not saved.
2. **Intended root**, pick the test root. **Add note**, or reply to the card with
   a note.
3. **Save**. The Inbox file has a readable name, and `/inbox` lists it.
4. `/note buy more coffee` saves a note directly. Send another note, then
   **Discard** it. No file is created.

## 8. Filing an upload

1. Open `INBOX.md` in the Inbox. The upload is listed with its intended root,
   note, and guidance files, and none of its contents.
2. Move that file into the root (by hand or with Codex). Within 15 minutes (or
   after `steward scan-root "Telegram Test"`), Telegram says where it was filed.
   `/inbox` and `INBOX.md` no longer list it, and its card shows the new location.
3. Rename a file inside the root. After the next scan, the same card (same
   ID) shows the new name.
4. Copy a file (two identical files). Both stay as separate files; nothing is
   merged.
