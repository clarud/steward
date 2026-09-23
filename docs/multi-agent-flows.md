# Multi-agent flows

Steward uses model roles only where a step needs judgment that plain code can't
provide: understanding a vague request, choosing between similar files, writing
an answer, and checking it. Everything else (scanning, extraction, hashing,
move reconciliation, INBOX.md) is deterministic, and filing belongs to Codex.

Each flow is a LangGraph workflow with a fixed shape, not an autonomous agent.
Roles are narrow, their output is validated against a JSON contract, a
`CallBudget` bounds the whole flow, and every role has a plain fallback.

## Routing

Routing is explicit. No heuristic guesses whether text is a question.

| You do | Runs |
|---|---|
| `/find TEXT [--type T] [--root "R"]` | Find |
| `/ask QUESTION`, or `steward ask` | Ask over all files |
| **Ask** on a file card, or reply to a file card with text | Ask, scoped to that file |
| **Summarize** on a file card | Summarize |
| Plain text with no command | Buttons: Find · Ask · Save as note (no model call) |

Without a configured model, the three flows are unavailable. Browse, Read,
Send original, and uploads still work.

## Shared foundation (`steward/roles/`)

- `generate_json` sends the role's instructions, the contract, and the input.
  It takes the text from the first `{` to the last `}`, parses it, and runs the
  role's validator. On failure it makes **one** repair call quoting the error,
  then raises `StructuredOutputError` so the node falls back. This is plain
  prompt-and-parse, so Gemini, OpenAI, SoCLaaS, and Ollama all work.
- `CallBudget` is thread-safe (Summarize's workers share it). A call beyond the
  budget raises `BudgetExhausted`, which is also a `StructuredOutputError`.
- File text is always framed as data, not instructions.
- `observability.trace` logs each node's decision and calls used, never file
  text, prompts, or model output.

## Find: which file is it?

```text
request → PLAN ─┬─ keyword  ─┐
                ├─ meaning  ─┤
                ├─ filename ─┼─ MERGE → JUDGE → reply
                └─ recent   ─┘    ▲        │ no candidates, or no_match (once)
                                  └─ REFORMULATE
```

| Node | Kind | Behaviour | Fallback |
|---|---|---|---|
| Plan | model | `{keywords[], meaning_query, types[], root?, folder_hint?, since?, filename_hint?}`; root and types must exist | Use the request as written |
| Keyword | code | FTS5 with the keywords OR-ed, ≤10 files | — |
| Meaning | code | Embedding search on `meaning_query`, ≤10 files | Skipped without the `semantic` extra |
| Filename | code | Filename and path matches for the hints and keywords | — |
| Recent | code | Files added or changed since `since` | Skipped when `since` is empty |
| Merge | code | Reciprocal-rank fusion **per file**; the plan's root, types, and folder boost the rank; top 8 | — |
| Judge | model | `picks` (1–3 IDs from the 8, each with a reason), `clarify` (question + 2–3 IDs), or `no_match` | Top 3 by fused rank |
| Reformulate | model | One broader plan after an empty merge or `no_match` | — |

Only the owner's own `--type` and `--root` are hard filters. The planner's
guesses only nudge ranking: on real data, a planner that guessed the wrong root
hid the right file entirely, and Find scored 1 of 3. As soft boosts, it scores 3 of
3. If the judge still says `no_match` after reformulating, the reply shows the
**closest matches**, labelled as such, rather than nothing.

Budget: usually 2 calls; at most 4.

## Ask: answers from your files

```text
question → PLAN → GATHER → ANSWER → CHECK → reply
                    ▲         │ need_more (once)
                    └─────────┘
```

| Node | Kind | Behaviour | Fallback |
|---|---|---|---|
| Plan | model | 1–3 `{query, root?, types?}` searches, using the chat's previous turn for follow-ups. Skipped for a single file | One search with the question |
| Gather | code | Runs the searches (hybrid, 6 sections each), dedupes, and labels sections `[F<id>]`, up to 12,000 characters. A single file ≤12,000 characters is used whole | — |
| Answer | model | `{status: "answer", text}` citing only the given keys, or `{status: "need_more", query}` once | "The model couldn't answer; these files look relevant" |
| Check | code + model | See below | Keep the code-checked text |

**Checker** (`roles/checker.py`), shared with Summarize:

1. Code: each sentence's `[F…]` keys must exist in the evidence, and the
   sentence must share at least one content word with the cited sections.
   Failures are removed.
2. Model: `{"unsupported": [indices]}` for the remaining cited sentences.
   Those are removed.
3. Uncited sentences (connectives, "I couldn't find…") are kept.

If more than half the cited sentences are removed, the reply says it couldn't
answer reliably and lists the relevant files instead. Otherwise the card shows
the answer, "N statements removed", and the sources with their page, slide,
or line locations.

Budget: usually 3 calls; at most 5. The chat's last question and answer are
kept in memory for follow-ups and lost on restart.

## Summarize: one file, reliably

```text
file → LOAD (cache?) → SPLIT → [NOTES × N, 4 at a time] → COMBINE (+ coverage retry) → CHECK → SAVE
```

| Node | Kind | Behaviour |
|---|---|---|
| Load | code | Returns the cached summary if the file's hash and the model are unchanged |
| Split | code | ~24,000-character batches on section boundaries; >32 batches → "too long, Ask about a part" |
| Notes × N | model | LangGraph `Send` workers: ≤1,600 characters, citing only their batch's keys. A failed batch is skipped and named on the card |
| Combine | model | One summary citing only keys from the notes. If a run of consecutive uncited sections is large (at least 3 sections and at least 20% of the file), retry once asking to cover it, and keep the better one |
| Check | code + model | Same checker as Ask |
| Save | code | Cache only complete summaries |

If combining fails, the verified notes are shown instead ("notes for each
part"). The card always shows "Covered X of Y sections".

Budget: 2N+3 calls for N batches (each note may repair once, plus combine, a
retry, and the check). A single-batch file uses at most 3.

## What model roles can't do

No role reads the filesystem, calls tools, writes to the database, or runs
without a budget. Judges choose only from supplied IDs, and answers and
summaries cite only supplied keys. Violations fail validation and trigger the
fallback.

## Evaluation

`steward evaluate-retrieval CASES.yaml --mode keyword|hybrid|find` reports
hit@1, hit@3, MRR, and misses for `cases: [{query: ..., file: path/suffix}]`.
Find ships as the default only while it matches or beats hybrid on the owner's
real cases. Results are recorded in [testing.md](testing.md).

## Known limits

- Files whose text can't be extracted can't be found or summarised.
- The checker uses the same model as the writer. The code pre-checks narrow the
  gap, but an independent model would be stronger.
- Find's planner, judge, and checker send snippets to the configured provider.
  With a cloud provider, those snippets leave the machine.
