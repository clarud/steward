# Multi-agent flows

Steward uses model "agents" only where a step needs judgment that plain code
can't provide **and** splitting the work into roles measurably beats one model
call. Everything else (scanning, extraction, hashing, reconciliation, filing)
stays deterministic, and filing belongs to Codex via `INBOX.md`.

Each flow is a LangGraph workflow with narrow model roles, not an autonomous
agent: fixed shape, a model-call budget, strict output contracts, and a
fallback to plain behaviour when a role fails.

## Routing

| You do | Runs |
|---|---|
| `/find TEXT` | Find |
| `/ask QUESTION` | Ask over all files |
| **Ask** on a file card, or reply to a file card with text | Ask scoped to that file |
| **Summarize** on a file card | Summarize |
| Plain text with no command | Buttons: Find · Ask · Save as note (no model call) |
| Send a file | Staged upload |

## Shared foundation

- `steward/roles/`: one module per role (planner, judge, answerer, checker,
  notes). Each has a prompt and an output contract.
- `generate_json`: call the configured model, extract JSON, validate it. One
  repair call with the validation error; then raise so the node falls back.
  Works with every provider because it is plain prompt-and-parse.
- A call budget in each graph's state; nodes that would exceed it fall back.
- Trace events per node: name, calls used, fallback taken. Never file text.

## Find: which file is it?

```text
request → PLAN → [keyword | meaning | filename | recent] → MERGE → JUDGE → reply
                  ▲                                             │ no_match (once)
                  └──────────────── REFORMULATE ◀───────────────┘
```

| Node | Kind | Contract / behaviour | Fallback |
|---|---|---|---|
| Plan | agent | `{keywords[], meaning_query, types[], root?, folder_hint?, since?, filename_hint?}`; root and types must exist | Use the request as-is |
| Keyword, Meaning, Filename, Recent | code | ≤10 files each with best section, scoped by root/type | — |
| Merge | code | One row per file, fused rank, folder-hint boost, top 8 | — |
| Judge | agent | `picks` (1–3 IDs from the 8, with reasons), `clarify` (question + 2–3 IDs), or `no_match` | Top 3 by fused rank |
| Reformulate | agent | A broader plan after the first `no_match` | Reply "no match" |

Budget: 2 calls, at most 4.

## Ask: answers from your files

```text
question → PLAN → [search 1..3] → GATHER → ANSWER → CHECK → reply
                                   ▲          │ need_more (once)
                                   └──────────┘
```

| Node | Kind | Contract / behaviour | Fallback |
|---|---|---|---|
| Plan | agent | 1–3 `{query, root?, types?}`; skipped for a single file | One search with the question |
| Search ×N | code | Hybrid retrieval, parallel | — |
| Gather | code | Dedupe, 12k-character cap, `[F1]…` keys | — |
| Answer | agent | `{status: answer, text}` or `{status: need_more, query}` once | Plain grounded answer |
| Check | code + agent | Code: citations exist and share terms with the sentence. Agent: supported yes/no. Unsupported sentences removed and counted | Keep code-checked sentences |

Memory: a per-chat checkpoint for follow-ups. Budget: 3 calls, at most 5.

## Summarize: one file, reliably

```text
file → CACHE? → SPLIT → [notes 1..N, 4 at a time] → COMBINE → COVERAGE → CHECK → SAVE → reply
```

| Node | Kind | Contract / behaviour |
|---|---|---|
| Cache | code | Keyed by file, content hash, and model |
| Split | code | ~24k-character batches on section boundaries, ≤32 |
| Notes ×N | agent | ≤1,600 characters citing only its batch; own retry; a failed batch is skipped and reported |
| Combine | agent | Summary citing only keys from the notes |
| Coverage | code | Share of sections cited; one combine retry naming uncovered sections |
| Check | code + agent | Same checker as Ask |

Budget: N + 2 calls, at most N + 4.

## What agents can't do

No agent reads the filesystem, calls tools, or loops without a budget. Judges
choose only from supplied IDs; answers and summaries cite only supplied keys.

## Evaluation and ship criteria

| Flow | Evaluation set | Ships if |
|---|---|---|
| Find | ~25 requests → expected file | hit@3 beats plain hybrid search |
| Ask | ~15 questions → files that should be cited | fewer unsupported sentences, same or better citation coverage |
| Summarize | ~5 long files | every file summarised; cited sections cover ≥80% |

## Known limits

- Files whose text can't be extracted can't be found or summarised.
- The checker uses the same model as the writer; code pre-checks narrow the
  gap but independent verification would be stronger.
