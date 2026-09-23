# Steward

Steward is a local-first companion to Codex for your personal files.

Codex is great at *working on* files: organising folders, renaming, moving,
and editing. Steward handles the other side: **getting files in, and getting
them back out**, from wherever you are.

- **Find files without remembering their names.** `/find that AVX question from
  tut 4` returns the right file with a one-line reason, even if you've
  forgotten its name, folder, or format.
- **Retrieve through Telegram.** Open, read, or receive the original file on
  your phone.
- **Summarise and ask.** Summarise a whole file, or ask a question across your
  files. Every statement cites the section it came from, and statements the
  sources don't support are removed.
- **Upload from anywhere.** Send a file or note on Telegram. It lands in a local
  Inbox and is listed in `INBOX.md`, ready for Codex to file.
- **Stays in sync with Codex.** Steward rescans every 15 minutes. When Codex
  moves or renames a file, Steward keeps its identity and tells you where it was
  filed.
- **Easy setup.** Point Steward at a folder you already have. Nothing is copied
  or moved.

## How Steward and Codex fit together

| | Codex | Steward |
|---|---|---|
| Role | Works *on* files | Finds and delivers files |
| Where | At your computer | Anywhere, through Telegram |
| Does | Organise, rename, move, edit | Upload, find, read, summarise, answer |
| Changes files? | Yes, with your approval | No; originals stay where they are |

```text
upload on Telegram → Inbox + INBOX.md → Codex files it into the right folder
→ Steward's next scan recognises the move and tells you where it went
→ later: /find it, read it, summarise it, or get the original back
```

## Quick start

Requires Python 3.12+. In PowerShell, from the repository root:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[all]"          # or choose extras: semantic, gemini
Copy-Item .env.example .env      # then set a model provider and the Telegram token
steward download-embedding-model # once, for meaning-based search
```

Authorize a folder you already have, then try it:

```powershell
steward onboard-root "Y4S1" "C:\Users\you\Documents\Y4S1"
steward search "the cache CPUs use for address translation"
steward ask "What do my notes say about queueing?"
steward telegram                  # run your own bot from this computer
```

Set `STEWARD_TELEGRAM_ALLOWED_CHAT_IDS` so only your chat can use the bot.

## Using Steward from Telegram

| You do | Steward |
|---|---|
| `/find WORDS` (optionally `--type pdf`, `--root "Y4S1"`) | Finds the file and says why it matches |
| `/ask QUESTION` | Answers from your files, citing each source |
| `/sources` | Browse your folders |
| `/inbox` | Uploads waiting to be filed |
| Send a file or note | Staged; **Save**, **Intended root**, **Add note**, or **Discard** |
| Plain text with no command | Offers **Find**, **Ask**, or **Save as note** |
| Reply to a file card with text | Asks about that file |

A file card offers **Read**, **Summarize**, **Ask**, **Send original**, and
**Folder**. Locations are shown relative to an authorized folder or the Inbox,
never as absolute paths.

## How Find, Ask, and Summarize work

Each is a small LangGraph workflow with narrow model roles, a hard budget on
model calls, and a plain fallback when a role fails. Details are in
[docs/multi-agent-flows.md](docs/multi-agent-flows.md).

- **Find** (≤4 calls): a planner rewrites the request. Keyword, meaning,
  filename, and recent-files searches run in parallel and are fused per file.
  A judge picks from the candidates only, or asks "which one?".
- **Ask** (≤5 calls): a planner chooses up to three searches. A drafter answers
  from numbered sections and may request one more search. A checker removes
  sentences their citations don't support.
- **Summarize** (≤2N+3 calls): parallel note-takers cover the whole file, a
  combiner writes one cited summary, and a coverage check retries if a large
  part was missed. The card shows how many sections were covered. The summary is
  cached until the file changes.

## Command line

```powershell
steward roots                      # authorized folders and last scan
steward scan-root "Y4S1"           # rescan now (the bot also does this every 15 minutes)
steward search "TLB" --type pdf    # --mode hybrid (default) | keyword | meaning
steward ask "..."                  # same Ask flow as Telegram
steward inbox                      # refresh INBOX.md and list what's waiting
steward evaluate-retrieval cases.yaml --mode find   # hit@1, hit@3, MRR
steward health --strict            # local readiness check
steward backup                     # snapshot the database
```

`steward --help` lists everything, including `relocate-root`, `remove-root`,
`reextract`, `rebuild-semantic-index`, `activity`, and `restore`.

## Optional extras

| Extra | Enables |
|---|---|
| `semantic` | Meaning-based and hybrid search (installs PyTorch) |
| `gemini` | The Gemini provider (the default `STEWARD_MODEL_PROVIDER`) |
| `all` | Both |

OpenAI, SoCLaaS (OpenAI-compatible), and local Ollama need no extra.

## Data and safety

- Original files are the source of truth. SQLite holds paths, hashes, extracted
  text, search indexes, summaries, and activity, all rebuildable from the
  originals.
- Steward reads only folders you authorize, plus its Inbox. It never moves,
  renames, or deletes your files.
- Models see only retrieved excerpts. They have no filesystem or tool access,
  and can only pick from, or cite, what they were given.
- Bot tokens are redacted from logs.

## Documentation

- [Product](docs/product.md): what Steward is and isn't.
- [Architecture](docs/architecture.md): how the pieces fit.
- [Multi-agent flows](docs/multi-agent-flows.md): Find, Ask, and Summarize in detail.
- [Invariants](docs/invariants.md): rules the code must never break.
- [Developer guide](docs/developer-guide.md): implementation details.
- [Testing](docs/testing.md) and the [Telegram checklist](docs/telegram-manual-test-checklist.md).
- [Windows operations](docs/windows-operations.md): running Steward at login.
- [Decision records](docs/adr/).
