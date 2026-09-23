# Architecture

```text
      CLI (steward …)                 Telegram (steward telegram)
             │                                   │
     steward.cli.commands              steward.telegram.adapter
             │                                   │
             │                  steward.app.events.StewardEventApplication
             │                                   │  routes to
             │       read · intake · roots · privacy · question · agent
             └───────────────┬───────────────────┘
                             │  composed by steward.cli.bootstrap
     sources · extraction · retrieval · answer · intake · capture · privacy · activity
                             │
          SQLite (.steward/steward.db)  +  authorized folders and the Inbox
```

## Layers

| Layer | Package | Responsibility |
|---|---|---|
| Transport | `steward.cli`, `steward.telegram` | Parse input, normalise events, deliver replies. No domain rules. |
| Composition | `steward.cli.bootstrap` | Build services from `Settings` once, for both the CLI and Telegram. |
| Use cases | `steward.app` | One module per job: `read`, `intake`, `roots`, `privacy`, `question`, `agent`, and the `events` router. |
| Domain services | `steward.sources`, `extraction`, `retrieval`, `answer`, `intake`, `capture`, `privacy`, `activity`, `roots` | Deterministic logic plus SQL behind small repositories. |
| Orchestration | `steward.graphs` | LangGraph workflows over the services above. |
| Storage | `steward.storage` | SQLite migrations, snapshot, and restore. |

## Data

The filesystem is the human-readable home of original files. SQLite holds
source identity, hashes, paths, extracted fragments, the FTS5 index,
embeddings, privacy rules, Inbox context, move proposals, location history,
root profiles and scan results, activity, and Telegram delivery state. Every
derived row can be rebuilt from the originals.

## Retrieval and answers

```text
question → hybrid retrieval (FTS5 + embeddings, reciprocal-rank fusion)
         → drop fragments the privacy rule forbids for this model
         → bounded context with [F1]… labels → model → citations verified
```

Two LangGraph workflows exist:

- `graphs.retrieval_answer`: the grounded-answer pipeline used for ordinary
  questions. It is a fixed workflow, not an agent.
- `graphs.tool_agent`: a single tool-calling loop behind `/agent` and
  `steward agent`. It can call only `search_sources`, `read_source`, and
  `search_activity`, under a call budget and a tool policy.

Steward is not multi-agent today.

## Model providers

`answer.gateway` defines one `ModelGateway.generate()` protocol, implemented for
Gemini, OpenAI, SoCLaaS (OpenAI-compatible), and local Ollama. `ModelRouter`
sends local-only evidence to the local gateway, or declines if none is
configured. Tool-calling adapters for the agent live in `steward.graphs`.

## Working alongside Codex

Codex changes files; Steward observes them. A full `scan-root` is the
reconciliation authority. The watcher only hints that something changed. A
same-folder rename with exactly one content-hash match becomes a reviewable
move proposal.

`INBOX.md` in the Inbox lists every file waiting to be filed: when and how it
arrived, its intended root and note, and that root's guidance files. Tell Codex
"file my Inbox using INBOX.md"; filed files drop off the list. Steward writes nothing else outside its data
directory and never starts Codex.
