# Steward

Steward is a local-first companion to Codex for your personal files.

Codex is great at *working on* files: organising folders, renaming, moving,
and editing. Steward handles the other side: **getting files in, and getting
them back out**, from wherever you are.

- **Find files without remembering their names.** Search your folders in plain
  language ("my CS3210 notes on queueing") and get the right file back, even
  if you've forgotten its name, location, or format.
- **Retrieve through Telegram.** Open, read, or receive a file from your phone.
- **Summarise and ask questions.** Get a summary of a file, or ask questions
  across your notes, with answers that cite their sources.
- **Upload from anywhere.** Send files or quick notes to Steward on Telegram and
  they land in a local Inbox, ready for Codex to organise later.
- **Stays in sync with Codex.** When Codex renames or moves files, Steward
  picks up the changes on its next scan and keeps each file's history.
- **Easy setup.** Point Steward at a folder you already have. Nothing is copied
  or moved, and your files stay on your machine.

## How Steward and Codex fit together

| | Codex | Steward |
|---|---|---|
| Role | Works *on* files | Finds and delivers files |
| Where | At your computer | Anywhere, through Telegram |
| Does | Organise, rename, move, edit | Upload, search, retrieve, summarise |
| Changes files? | Yes, with your approval | No; your originals stay where they are |

The loop:

```text
upload on Telegram → lands in the Inbox and INBOX.md → Codex files it into the right folder
→ Steward notices the move on the next scan → you find it later with a simple search
```

## Quick start

Requires Python 3.12+. From the repository root, in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[all]"          # or pick extras: semantic, google, gemini
Copy-Item .env.example .env      # then fill in your model key and Telegram token
steward download-embedding-model # once, for meaning-based search
```

Point Steward at a folder you already have, then search it:

```powershell
steward onboard-root "Y4S1" "C:\Users\clare\OneDrive\Desktop\Y4S1"
steward hybrid-search "the cache CPUs use for address translation"
steward ask "What do my notes say about queueing?"
```

Run `steward telegram` to use the same folders from your phone through your
own allowlisted Telegram bot. Steward polls Telegram from your machine; your
files, database, tokens, and model settings never leave it.

## Using Steward from Telegram

- **Find:** send `find my CS3210 queueing notes`, or use
  `/hybrid_search TLB --type pdf --root "CS3210"`. If no content matches,
  Steward also checks registered filenames and labels those results clearly.
- **Open and read:** tap a result to see its card, then **Read content**,
  **Summarize**, **Ask about it**, or **Send original** to get the file itself.
- **Upload:** send a document or a note. Steward stages it, explains what it
  will save, and adds it to the Inbox only after you confirm. **Intended root**
  records where you want it to end up; the file stays in the Inbox until Codex
  moves it.
- **Pick up on your computer:** every saved upload is listed in `INBOX.md`
  inside the Inbox, with its intended root and note. Tell Codex "file my Inbox
  using INBOX.md"; what happens next is up to you.
- **After Codex moves things:** `/moves` shows renames and moves found by the
  last scan. Accepting one keeps the file's identity and history.
- **Privacy:** `/privacy SOURCE_ID` controls whether a file may be sent to a
  cloud model, only a local model, or no model at all.

Source cards show a location relative to an authorized folder or the Inbox,
never an absolute path.

## Everyday commands

```powershell
steward roots                                  # authorized folders and last scan
steward scan-root "Y4S1"                       # reconcile after Codex changes files
steward watch-root "Y4S1"                      # refresh changed files as they happen
steward reconcile-moves "Y4S1"                 # list reviewable renames/moves
steward review-move 1 --accept                 # keep a moved file's identity
steward inbox                                  # what's waiting in INBOX.md
steward set-root-profile "Y4S1" --purpose "NUS Y4S1 coursework" --guidance AGENTS.md
steward health --strict                        # local readiness check
steward backup                                 # snapshot Steward's databases
```

Run a full scan after Codex (or anything else) edits, renames, or moves files.
The watcher is a convenience; the scan is the source of truth. A rename or move
within one folder that matches exactly one file by content becomes a reviewable
move proposal.

Root profiles are optional notes about a folder: its purpose, guidance files
inside it (such as the folder's own `AGENTS.md`), and authority labels. They
are listed in `INBOX.md` as filing guidance and are never treated as
instructions by Steward.

## Optional extras

| Extra | Enables |
|---|---|
| `semantic` | Meaning-based and hybrid search (installs PyTorch) |
| `google` | Explicit Google Drive and Gmail imports into the Inbox |
| `gemini` | The Gemini model provider (the default `STEWARD_MODEL_PROVIDER`) |
| `all` | All of the above |

Without an extra, the related command explains which one to install.

## Data and safety

- Your original files are the source of truth. Steward stores paths, hashes,
  extracted text, search indexes, and activity in local SQLite, all of which
  can be rebuilt from the originals.
- Steward reads only folders you explicitly authorize, plus its Inbox.
- Steward never moves, renames, or deletes your files. Codex does that, with
  your approval.
- A model sees only retrieved excerpts that the file's privacy rule allows.
  Answers cite the exact sections they used.

## Documentation

- [Product](docs/product.md): what Steward is and isn't.
- [Architecture](docs/architecture.md): how the pieces fit.
- [Invariants](docs/invariants.md): rules the code must never break.
- [Developer guide](docs/developer-guide.md): implementation details.
- [Testing](docs/testing.md) and the [Telegram checklist](docs/telegram-manual-test-checklist.md).
- [Windows operations](docs/windows-operations.md): running Steward at login.
- [Decision records](docs/adr/).
