# Architecture

```text
      CLI (steward …)                    Telegram (steward telegram)
             │                                     │  + rescan every 15 minutes
     steward.cli.commands                steward.telegram.adapter
             │                                     │
             │                    steward.app.events.StewardEventApplication
             │                            files · intake · answers
             └───────────────┬─────────────────────┘
                             │  composed by steward.cli.bootstrap (build_flows)
            graphs: find · ask · summarize   ──uses──▶  roles (model contracts)
                             │
     sources · extraction · retrieval · capture · intake · roots · activity
                             │
          SQLite (.steward/steward.db)  +  authorized folders and the Inbox
```

## Layers

| Layer | Package | Responsibility |
|---|---|---|
| Transport | `steward.cli`, `steward.telegram` | Parse input, normalise events, deliver replies, schedule rescans. No domain rules. |
| Composition | `steward.cli.bootstrap` | Build services and flows from `Settings`, once, for both the CLI and Telegram. |
| Use cases | `steward.app` | `files`, `intake`, and `answers`, plus the `events` router. They return `PresentedReply` cards. |
| Flows | `steward.graphs` | LangGraph workflows: Find, Ask, Summarize. |
| Roles | `steward.roles` | One prompt and JSON contract per model role, plus budgets and repair. |
| Domain services | `sources`, `extraction`, `retrieval`, `capture`, `intake`, `roots`, `activity` | Deterministic logic and SQL behind small repositories. |
| Storage | `steward.storage` | Append-only migrations, snapshot, and restore. |

## Data

The filesystem holds the originals. SQLite holds source identity, hashes, paths,
extracted fragments, the FTS5 index, embeddings, cached summaries, Inbox
context, location history, root scans, activity, and Telegram delivery state.
Every derived row can be rebuilt from the originals.

## Model flows

Three flows use a model. Each has a fixed graph shape, narrow roles with JSON
contracts, a call budget, and a deterministic fallback:

| Flow | Shape | Budget |
|---|---|---|
| Find | plan → 4 retrievers in parallel → per-file fusion → judge → (reformulate once) | 4 calls |
| Ask | plan → gather → answer → (one extra search) → check | 5 calls |
| Summarize | cache → split → parallel notes → combine (coverage retry) → check → save | 2N+3 calls |

See [multi-agent-flows.md](multi-agent-flows.md). Everything else (browse, read,
send original, upload, scan, INBOX.md) makes no model call.

`answer.gateway` defines one `ModelGateway.generate()` protocol, implemented for
Gemini, OpenAI, SoCLaaS (OpenAI-compatible), and local Ollama. Roles are plain
prompt-and-parse, so every provider works the same way.

## Working alongside Codex

Codex changes files; Steward observes them. The Telegram bot rescans every
authorized root every 15 minutes, and `scan-root` does it on demand. When
exactly one missing file and one newly seen file share a content hash, the scan
merges them: the source keeps its ID and history, and gains a location-history
row. If the file came from a Telegram upload, the chat is told where it was
filed. Anything ambiguous stays as separate files, which remain searchable.

`INBOX.md` lists every file waiting in the Inbox: when and how it arrived, its
intended root and note, and that root's guidance files. Tell Codex "file my
Inbox using INBOX.md". Steward writes nothing outside its data directory and
Inbox, and never starts Codex.
