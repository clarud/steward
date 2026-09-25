# Multi-agent flows

Steward uses model roles only where a step needs judgment that plain code can't
provide: understanding a vague request, choosing between similar files, writing
an answer, and checking it. Everything else (scanning, extraction, hashing,
move reconciliation, INBOX.md) is deterministic, and filing is the owner's.

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
- `roles/citations.py` normalises every model reply's citations: models write
  `[F1, F2]` or `[Fn: F12]` as often as `[F12]`, so all become `[F1][F2]` before
  validation. Otherwise the checker and coverage silently miss them.
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

On 50 test queries over real coursework: hit@3 **90–92%** against 70% for
plain hybrid search, and hit@1 72–86% against 56–60% (two runs; the model's
picks vary). See [testing.md](testing.md).

## Ask: answers from your files

```text
question → PLAN → GATHER → ANSWER → CHECK → reply
                    ▲         │ need_more (once)
                    └─────────┘
```

| Node | Kind | Behaviour | Fallback |
|---|---|---|---|
| Plan | model | 1–3 `{query}` searches, using the chat's previous turn for follow-ups. Folder names are shown to help word queries but never filter. Skipped for a single file | One search with the question |
| Gather | code | Runs the searches (hybrid, 6 sections each), dedupes, and labels sections `[F<id>]`, up to 12,000 characters. A match shorter than 300 characters (a title slide) brings the 2 sections after it, and one under 200 characters (a bare heading) is replaced by them. The extra search also looks inside the 2 files already found, and its results go first. A single file ≤12,000 characters is used whole | — |
| Answer | model | One fact per sentence, each citing every excerpt it uses and adding no details they lack. `{status: "answer", text}` or, once, `{status: "need_more", query}`. Asking for more when no searches are left gets one retry ("answer from this evidence, or say it doesn't answer"). One stray citation key is left for the checker. An answer with no citations is repaired once; an uncited reply that says the evidence doesn't answer becomes "not found" | "Your files don't seem to answer that", with the closest files |
| Check | code + model | See below | Keep the code-checked text |

**Checker** (`roles/checker.py`), shared with Summarize:

1. Re-point citations (code): when a sentence barely shares the words of the
   section it cites but clearly matches another supplied section, its citation
   moves there. An invented statement matches nothing and keeps its citation.
2. Split into sentences. Full stops inside numbers (0.25) and after short
   abbreviations (e.g., r.v.) don't end a sentence, and citations after a full
   stop or on their own line stay with the text before them.
3. Code: each sentence's `[F…]` keys must exist in the evidence, and the
   sentence must share at least one content word with the cited sections.
   Failures are removed.
4. Model, 15 sentences per call: each sentence with the best-matching 800
   characters of each cited section. It returns `{"unsupported": [indices]}`,
   listing only sentences that contradict the evidence or add a fact it doesn't
   give (a number, date, name, or cause). Paraphrase is fine.
5. Second opinion: a flagged sentence that was judged on an excerpt of a longer
   section is judged again against the full section (up to 3,000 characters)
   and removed only if that also fails. When the first look already saw the
   whole section, a second look adds only chance, so the first verdict stands.
6. Uncited sentences (connectives, "I couldn't find…") are kept.

On 60 planted statements from real passages, code checks alone remove 3 of 30
false ones. With the model, over five runs the checker removes 27–30 of 30 (95%
on average) and keeps 28–29 of 30 true ones (95%).

On real answers (two graded Ask runs), 9 of its 14 removals were right: 2
invented deadlines, and 7 true facts cited to the wrong section (a heading, a
progress-file header). 5 removed a true statement.

On the final 28-question set (24 answerable, 8 of them vague; two runs graded
by hand): 18–20 correct, 3–4 partly correct, 1–2 withheld or not found, 0 wrong;
all 4 unanswerable questions declined. Ask always retrieved the right file for
the vague questions; its misses were the wrong section or a checker cut.

The card shows whatever survived with citations as locations (`[p.19]`, or
`[2 p.19]` across several files), "N statements removed", a **Show removed**
button listing them, and the numbered sources. Only when no cited statement survives does it say it couldn't answer
reliably and list the relevant files instead.

Budget: usually 3–4 calls; at most 6. The chat's last question and answer are
kept in memory for follow-ups and lost on restart.

## Summarize: one file, reliably

```text
file → LOAD (cache?) → SPLIT → [NOTES × N, 4 at a time] → COMBINE (+ coverage retry) → CHECK → SAVE
```

| Node | Kind | Behaviour |
|---|---|---|
| Load | code | Returns the cached summary if the file's hash and the model are unchanged |
| Split | code | ~24,000-character batches on section boundaries; >32 batches → "too long, Ask about a part" |
| Notes × N | model | LangGraph `Send` workers write bullet notes citing only their batch's keys (shown a real key as the example), keeping dates, deadlines, requirements, numbers, and named concepts. The parts share a 36,000-character target (2,000–6,000 each). Long notes are kept, not cut: cutting dropped their last points, once a deadline. Only runaway output over 12,000 characters is cut. A failed batch is skipped and named on the card |
| Combine | model | One summary of short bullet points under topic headings, about 700 words, keeping every date, deadline, requirement, number, and named concept, citing only keys from the notes. One repair if it cites nothing, cites many unknown keys, or leaves over 25% of paragraphs uncited (a second thin reply is accepted). If a run of consecutive uncited sections is large (at least 3 sections and at least 20% of the file), retry once asking to cover it, and keep the better one |
| Check | code + model | Same checker as Ask; the card shows "N statements removed" and **Show removed**, and the cache keeps them |
| Save | code | Cache only complete summaries |

If combining fails, the verified notes are shown instead ("notes for each
part"). The card shows citations as locations (`[p.19]`), maths as Unicode, and
"Covered X of Y sections". On 7 files over two runs, summaries mentioned 93–95%
of 42 named key facts (81–83% before notes stopped being cut).

Budget: 2N+8 calls for N batches: each note may repair once, the combiner may
repair once, one coverage retry, up to three checker calls, and one second
opinion. A single-batch file uses at most 8. Typical use is 3–8.

## What model roles can't do

No role reads the filesystem, calls tools, writes to the database, or runs
without a budget. Judges choose only from supplied IDs, and answers and
summaries cite only supplied keys: a reply with many unknown keys fails
validation and triggers the fallback, and a single stray key is removed by the
checker.

## Evaluation

| Command | Measures |
|---|---|
| `evaluate-retrieval CASES --mode keyword\|hybrid\|find` | hit@1, hit@3, MRR for `{query, file}` cases |
| `evaluate-checker CASES` | planted false statements removed, true ones kept; code-only and with the model |
| `evaluate-ask CASES --report R.md` | answers citing an expected file; unanswerable questions declined; calls, time |
| `evaluate-summaries CASES --report R.md` | completion, named key facts mentioned, cited-section coverage, calls, time |

The reports hold every answer and summary for grading by hand. Find ships as
the default only while it beats hybrid on the owner's cases. Results are in
[testing.md](testing.md).

## Known limits

- Files whose text can't be extracted can't be found or summarised.
- The checker uses the same model as the writer. The code pre-checks narrow the
  gap, but an independent model would be stronger. It still removes a true
  sentence in roughly 1 of 3 real answers; **Show removed** lets you see what
  was cut. Most of its correct removals are miscitations by the answer writer,
  so better citing is the next lever.
- "Covered X of Y sections" counts cited sections, so it measures how traceable
  a summary is more than how complete it is, and it varies from run to run. The
  evaluation also checks named key facts per file.
- The model follows word targets but not character targets or "shorten this"
  requests, so length is steered by the prompt, not enforced.
- Find's planner, judge, and checker send snippets to the configured provider.
  With a cloud provider, those snippets leave the machine.
