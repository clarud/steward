# Testing

Automated tests prove services, flows, and transport behaviour. Only a local
run proves that the real bot token, model provider, and filesystem work
together, and only an evaluation on real files shows whether Find is good.
Steward uses all three.

## Automated

```powershell
.venv\Scripts\python -m pytest              # full suite
.venv\Scripts\python -m pyflakes src tests  # unused imports, undefined names
```

- Tests create temporary databases and folders. They never touch `.steward/`,
  the Inbox, or a real vault. No test makes a network call.
- Layout mirrors `src/`: `tests/sources`, `tests/extraction`, `tests/retrieval`,
  `tests/graphs`, `tests/app`, `tests/telegram`, `tests/storage`,
  `tests/evaluation`, plus `tests/test_cli.py`.
- **Flows** (`tests/graphs/`) use a `ScriptedModel` that returns queued replies
  and fails if a flow makes more calls than scripted. They cover the role
  contracts, repair, fallbacks, the reformulation loop, the checker, parallel
  summary workers, the cache, and the budget limits. One test reproduces the
  real-data failure where a wrong planner guess hid the right file.
- `tests/storage/test_database.py` checks the migration ledger and the snapshot
  taken before each destructive migration. Add a test there for any new
  migration.
- `tests/test_recovery_workflow.py` rehearses backup, restore, and a fresh
  interpreter reading the restored state.

## Evaluating Find on real data

Write cases for files you actually look for, in words you'd actually use:

```yaml
cases:
  - {query: "that AVX question from tut 4", file: CS3210/Tutorials/tut04.pdf}
  - {query: "the cache CPUs use for address translation", file: virtual-memory.md}
```

`file` matches the end of the result's path. When the same content exists in
several places (a copy, or a PDF and its transcript), list them all with
`files: [a, b]`; any one counts. Then compare the modes on a copy
of your data:

```powershell
steward evaluate-retrieval cases.yaml --mode keyword
steward evaluate-retrieval cases.yaml --mode hybrid
steward evaluate-retrieval cases.yaml --mode find    # uses the configured model
```

Each mode reports hit@1, hit@3, MRR, and the misses. Aim for about 25 cases
across formats and courses, including vague ones.

## Against real data, safely

Never experiment on the live database. Snapshot it and point Steward at the
copy:

```powershell
steward backup --destination "$env:TEMP\steward-check"
$env:STEWARD_DATA_DIR = "$env:TEMP\steward-check"
steward health --strict
steward roots
steward scan-root "Y4S1"
steward search "TLB"
Remove-Item Env:STEWARD_DATA_DIR
```

`scan-root` only reads the originals; it writes to the copied database. The
first run on an older database applies pending migrations and snapshots it
first if any are destructive.

## Live Telegram acceptance

Follow [telegram-manual-test-checklist.md](telegram-manual-test-checklist.md)
with a harmless test root, and record results below. Never record tokens,
credentials, private file content, or absolute paths.

## Results

### 2026-09-24: full evaluation of Find, the checker, Ask, and Summarize

Run on a copy of the real database (136 files) with the SoCLaaS model. Claude
wrote the case files from indexed excerpts (`steward-eval/`, kept outside the
repo). Answers and summaries were graded by hand from the `--report` files.

**Find, 50 queries** (set 1: 25 whole-file descriptions; set 2: 25 harder ones:
topics deep in lectures, typos, code files, Inbox uploads, both folders):

| Mode | Hit@1 | Hit@3 | MRR |
|---|---|---|---|
| keyword | 8–10% | 10–12% | 0.09–0.11 |
| hybrid | 56–60% | 70% | 0.66–0.68 |
| find | **72–86%** | **90–92%** | **0.80–0.88** |

Two runs each; the second was after re-extraction (below). On set 2 alone, run
before any changes: find 92% hit@3 against hybrid 76%. Find's remaining misses
include a results table with little prose, `NEXT_14_DAYS.md` for "what do I need
to do in the next two weeks", and the CS3210 notes for "false sharing".

**The first Ask and Summarize runs found real bugs**, all fixed in `ea06bfa`:

| Symptom | Cause | Fix |
|---|---|---|
| 2 of 7 summaries failed; others lost citations | Notes were capped at 1,600 characters (the model writes 2,500–4,700) and cited `[Fn: F891]`, copying the prompt's placeholder | Real key in the prompt; notes trimmed at 3,000, not rejected; citation normalisation |
| Summaries cut mid-sentence ("Version 13.") | Sentence splitter broke on decimals and abbreviations | Splitter that ignores those |
| Citation lists `[F1, F2]` ignored | Checker only read `[F1]` | `roles/citations.normalize` |
| Heritage question searched the wrong folder | Ask's planner guessed folder/type and they filtered the search | Planner guesses no longer filter (the Find lesson again) |
| "What optimisations did I try" never found the log | A 91k-character Markdown log was 2 sections | Sections over 3,000 characters split at line breaks; `reextract --all` |
| Unanswerable questions ended in "the model couldn't answer" | The drafter kept asking for more evidence | That now means "your files don't seem to answer that" |
| Correct answers withheld as unreliable | Checker prompt said "be strict", so it rejected paraphrase | It flags only contradictions or added facts |
| Title slides matched but held no content | PPTX slide per section | A match under 300 characters brings the next 2 sections |
| A whole summary rejected for one invented key | Validation all-or-nothing | One stray key tolerated; the checker removes that sentence |
| Checker removed a whole 51-sentence summary once | One 47k-character judgement | 15 sentences per checker call |

**Citation checker**, 15 real passages, 30 true and 30 planted false statements:

| | False removed | True kept |
|---|---|---|
| code checks only | 3/30 | 30/30 |
| code + model, before calibration | 29/30 | 28/30 |
| code + model, final (two runs) | 30/30 | 26–28/30 |

**Ask**, 20 questions, after fixes (graded by hand):

- 16 answerable: 12 correct, 2 partly correct (a sentence removed by the checker), 2 withheld as unreliable (checker removed true sentences), **0 wrong**. 14/16 cited an expected file.
- 4 unanswerable (MESI, a wifi password, the World Cup, Kubernetes): all declined with "your files don't seem to answer that".
- Mean 3.4 model calls, 4.4 s.
- Before fixes: 11 correct, 1 partly, 4 failed, and only 1 of 4 unanswerable questions declined properly.

**Summarize**, 7 files, after fixes (graded by hand, two runs):

- 7/7 completed in both runs (before: 5/7, with 2 broken).
- Fully accurate and complete: Performance lecture, Queueing lecture, Heritage slides. Accurate with gaps: GPU lecture (memory model thin), assignment brief (omits the deadline), CS3210 notes (later topics missing), optimisation log (one sentence lost its antecedent). No factual errors found.
- Cited-section coverage 43–59% (mean) and variable: it measures traceability more than completeness.
- Mean 6 model calls, 20–29 s.

Full suite: 271 passed.

### 2026-09-24: Find evaluation, 25 cases

25 requests over the Y4S1 coursework folder (106 files: lecture PDFs and
transcripts, tutorials, pptx, docx, html, C++ code, and personal notes). Run on a
copy of the real database with the SoCLaaS model. Identical copies (a PDF and its
transcripts) all count as correct. Claude wrote the cases from short excerpts,
phrased as a student would ask from memory.

| Mode | Hit@1 | Hit@3 | MRR |
|---|---|---|---|
| keyword | 4% | 4% | 0.040 |
| hybrid | 44% | 64% | 0.575 |
| find | **76%** | **84%** | **0.800** |

Find's four misses:
- "perf stat numbers comparing bubble sort and cocktail sort"
- "poisson arrivals and exponential interarrival times"
- "lecture on dividing link capacity fairly between users"
- "what do I need to do in the next two weeks"

Hybrid also missed all four. Tuning Find against these cases would overfit them,
so any change should be checked on a new, separate set of cases.

### 2026-09-24: multi-agent flows and minimal surface (ADR-009)

- Full suite: 255 passed; pyflakes clean.
- Migrations 66–69 applied to a copy of the real database, with snapshots before
  66 and 68.
- `evaluate-retrieval` on a copy of real data, 3 hand-written cases, SoCLaaS
  model:

  | Mode | Hit@1 | Hit@3 | MRR |
  |---|---|---|---|
  | keyword | 0% | 0% | 0.000 |
  | hybrid | 100% | 100% | 1.000 |
  | find (planner guesses as hard filters) | 33% | 33% | 0.333 |
  | find (planner guesses as soft boosts) | 100% | 100% | 1.000 |

  Find used 2 model calls per case. Keyword mode requires every word to match,
  so natural-language requests miss. Three cases are too few to show that
  Find beats hybrid; a 25-case set is the next step.
- Not yet run: the live Telegram checklist after these changes.

### 2026-09-24: legacy removal (ADR-007)

Full suite passed after the legacy tests were deleted. Migration 65 on a copy of
the real database snapshotted first, dropped 25 legacy tables, and left sources,
fragments, embeddings, activity, roots, and Telegram state unchanged.
