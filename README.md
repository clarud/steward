# Steward

Steward is a personal file assistant. It lets you **find, understand, and
retrieve your files from anywhere** through Telegram, from the folders you
already have.

- **Find files without remembering their names.** `/find that AVX question from
  tut 4` returns the right file with a one-line reason, even if you've
  forgotten its name, folder, or format.
- **Understand them without opening them.** Ask a question across your files, or
  summarise a whole file. Every factual statement cites the page, slide, or
  lines it came from, and statements the sources don't support are removed.
- **Get the original.** Open, read, or receive the actual file on your phone.
- **Upload from anywhere.** Send a file or note on Telegram. It lands in a local
  Inbox and is listed in `INBOX.md`, ready to be filed however you like.
- **Keeps up with however you organise.** Steward rescans every 15 minutes. When
  files are moved or renamed (by hand, by a sync tool, or by a coding agent such
  as Codex), it keeps each file's identity and tells you where an upload was
  filed.
- **Easy setup.** Point Steward at a folder you already have. Nothing is copied,
  moved, or changed.

```text
upload on Telegram → Inbox + INBOX.md → you (or a tool like Codex) file it
→ Steward's next scan recognises the move and tells you where it went
→ later: /find it, read it, ask about it, summarise it, or get the original back
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
Find, Ask, and Summarize need a model provider (see `.env.example`); browsing,
reading, sending originals, and uploads work without one.

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
**Folder**. Answers and summaries cite locations such as `[p.19]` (or
`[2 p.19]` when an answer draws on several files, matching its numbered
sources), show maths as readable text (E(W) = 1/(μ − λ)), and offer **Show
removed** when the checker cut anything. The chat shows "typing…" while a
request runs. Right after the bot starts, the search model loads in the
background (15–60 seconds); a `/find` or `/ask` in that time gets a short
"still starting up" notice and is answered as soon as it's ready. Locations are always relative
to an authorized folder or the Inbox, never absolute paths.

## How Find, Ask, and Summarize work

Each is a small LangGraph workflow with narrow model roles, a hard limit on
model calls, and a plain fallback when a role fails. Details are in
[docs/multi-agent-flows.md](docs/multi-agent-flows.md).

- **Find** (≤4 calls): a planner rewrites the request. Keyword, meaning,
  filename, and recent-files searches run in parallel and are fused per file.
  A judge picks from the candidates only, or asks "which one?".
- **Ask** (≤6 calls): a planner chooses up to three searches. A writer answers
  from the retrieved sections, one cited fact per sentence, and may ask for one
  more search. A checker removes sentences their citations don't support.
- **Summarize** (≤2N+8 calls for N parts): parallel note-takers read the whole
  file, a combiner writes one cited summary of about 700 words, and a coverage
  check retries if a large part was missed. Files up to about 770,000 characters;
  the summary is cached until the file changes.

Measured on a copy of real coursework (136 files; details in
[docs/testing.md](docs/testing.md)):

| Evaluation | Result |
|---|---|
| Find, 50 test queries | right file in the top 3: **90–92%** (plain hybrid search: 70%) |
| Citation checker, 30 planted false statements | 27–30 removed (95% on average, five runs), 28–29 of 30 true ones kept |
| Ask, 24 answerable questions (8 of them vague) | 18–20 correct, 3–4 partly correct, 1–2 withheld or not found, **0 wrong** (two runs) |
| Ask, 4 questions the files can't answer | 4 declined |
| Summarize, 7 files (PDF, PPTX, notes, a 91k-character log) | 7 completed; 93–95% of 42 key facts mentioned |

## Command line

```powershell
steward roots                      # authorized folders and last scan
steward scan-root "Y4S1"           # rescan now (the bot also does this every 15 minutes)
steward search "TLB" --type pdf    # --mode hybrid (default) | keyword | meaning
steward ask "..."                  # the same Ask flow as Telegram
steward inbox                      # refresh INBOX.md and list what's waiting
steward reextract --all            # re-extract every file after an update
steward health --strict            # local readiness check
steward backup                     # snapshot the database
```

Evaluation: `evaluate-retrieval`, `evaluate-checker`, `evaluate-ask`, and
`evaluate-summaries` (see [docs/testing.md](docs/testing.md)). `steward --help`
lists everything, including `relocate-root`, `remove-root`,
`rebuild-semantic-index`, `activity`, and `restore`.

## Optional extras

| Extra | Enables |
|---|---|
| `semantic` | Meaning-based and hybrid search (installs PyTorch) |
| `gemini` | The Gemini provider (the default `STEWARD_MODEL_PROVIDER`) |
| `all` | Both |

OpenAI, SoCLaaS (OpenAI-compatible), and local Ollama need no extra.

## Data and privacy

- Steward runs on your computer. Your files, the database (paths, hashes,
  extracted text, search indexes, summaries, activity), and the Inbox stay
  there, and everything derived can be rebuilt from the originals.
- Steward reads only folders you authorize, plus its Inbox. It never moves,
  renames, changes, or deletes your files.
- Model calls send only the text a flow needs: retrieved sections for Find and
  Ask, and the file's text for Summarize. With a cloud provider that text leaves
  your machine; with a local Ollama model it doesn't. Models have no filesystem
  or tool access and can only pick from, or cite, what they were given.
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
