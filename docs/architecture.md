# Architecture

## Active source-centric path

```text
CLI / Telegram
      ↓
event and command adapters
      ↓
Source, extraction, retrieval, Inbox, privacy, and activity services
      ↓
SQLite operational metadata + explicitly authorized local filesystem roots
```

The filesystem remains the human-readable home of original files. SQLite holds
source identity, hashes, paths, extraction fragments, full-text search data,
embeddings/index references, privacy rules, and activity metadata. It does not
replace an existing vault.

Retrieval combines deterministic lexical/semantic services. A grounded answer
graph receives only retrieved fragments, not arbitrary filesystem access:

```text
question → retrieve fragment IDs → build permitted context → model → cited answer
```

The source tool agent is similarly bounded to `search_sources`, `read_source`,
and `search_activity`. LangGraph orchestrates these services and persists only
conversation/checkpoint state; domain logic and side effects remain in normal
Python services.

## Product modes and retained code

`source_centric` is the default setting. It composes source ingestion,
extraction, retrieval, Inbox, privacy, activity, explicit external imports, and
the bounded read-only agent. It does not compose Workspace, Knowledge, Record,
Task, Calendar, research, or broad-action services into CLI, Telegram, or agent
flows.

Those modules remain in the repository without destructive migration so that
past data and experiments are preserved. `legacy` is an explicit local
development mode, not an advertised default interface. The detailed staged
migration lives in [the pivot plan](source-centric-pivot-plan.md).
