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
