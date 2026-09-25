# Testing

Automated tests prove services, flows, and transport behaviour. Only an
evaluation on real files shows whether Find, Ask, and Summarize are good, and
only a live run proves that the real bot token, model provider, and filesystem
work together. Steward uses all three.

## Automated

```powershell
.venv\Scripts\python -m pytest              # full suite (289 tests, about 35 s)
.venv\Scripts\python -m pyflakes src tests  # unused imports, undefined names
```

- Tests create temporary databases and folders. They never touch `.steward/`,
  the Inbox, or a real vault. No test makes a network call.
- Layout mirrors `src/`: `tests/sources`, `tests/extraction`, `tests/retrieval`,
  `tests/graphs`, `tests/app`, `tests/telegram`, `tests/storage`,
  `tests/evaluation`, plus `tests/test_cli.py` and `tests/test_readable.py`.
- **Flows** (`tests/graphs/`) use a scripted model that returns queued replies
  and fails if a flow makes more calls than scripted. They cover the role
  contracts, repair, fallbacks, the checker, parallel summary workers, the
  cache, and the call limits. Several tests reproduce real-data failures found
  by the evaluations (listed below).
- `tests/storage/test_database.py` checks the migration ledger and the snapshot
  taken before each destructive migration. Add a test there for any new
  migration.
- `tests/test_recovery_workflow.py` rehearses backup, restore, and a fresh
  interpreter reading the restored state.

## Evaluations

Run them on a copy of your data (below). Model output varies, so run each at
least twice and report the range. Case files describe your own files, so keep
them outside the repository.

| Command | Case format | Scores |
|---|---|---|
| `steward evaluate-retrieval CASES --mode keyword\|hybrid\|find` | `{query, file}`, or `files: [a, b]` for identical copies | hit@1, hit@3, MRR, misses |
| `steward evaluate-checker CASES` | `{evidence, supported: [...], unsupported: [...]}`, statements without inner full stops | planted false statements removed, true ones kept; code-only and with the model |
| `steward evaluate-ask CASES --report R.md` | `{question, files}`, or `answerable: false` | an expected file cited; unanswerable questions declined; calls, time |
| `steward evaluate-summaries CASES --report R.md` | `{file, facts: ["18 Sep\|18 September", ...]}` | named key facts mentioned, sections cited, calls, time |

`file` matches the end of a path. The `--report` files hold every answer and
summary, for grading correctness by hand. Write requests in the words you'd
actually use, not the file's title, and don't tune a flow against the same
cases you report: check a change on new cases.

## Against real data, safely

Never experiment on the live database. Snapshot it and point Steward at the
copy:

```powershell
steward backup --destination "$env:TEMP\steward-check"
$env:STEWARD_DATA_DIR = "$env:TEMP\steward-check"
steward health --strict
steward evaluate-retrieval cases.yaml --mode find
Remove-Item Env:STEWARD_DATA_DIR
```

Scans and evaluations only read the originals; they write to the copied
database. The first run on an older database applies pending migrations and
snapshots it first if any are destructive.

## Live Telegram acceptance

Follow [telegram-manual-test-checklist.md](telegram-manual-test-checklist.md)
with a harmless test root. Never record tokens, credentials, private file
content, or absolute paths.

## Current results (25–26 Sep 2026)

A copy of real coursework (136 files: lecture PDFs and transcripts, slides,
DOCX, C++ code, notes), the SoCLaaS model, two runs of each unless noted. Claude
wrote the cases from indexed excerpts and graded answers and summaries by hand;
the sets are small, so they show direction rather than precise accuracy.

| Evaluation | Result |
|---|---|
| Find, 50 requests | hit@3 **90–92%** (hybrid 70%, keyword 10–12%); hit@1 72–86% (hybrid 56–60%); MRR 0.80–0.88 (hybrid 0.66–0.68) |
| Checker, 30 true + 30 planted statements, five runs | 27–30 false removed (mean 95%), 28–29 true kept (mean 95%); code checks alone: 3 false removed |
| Ask, 24 answerable questions (8 vague) | 18–20 correct, 3–4 partly correct, 1–2 withheld or not found, **0 wrong**, 0 uncited |
| Ask, 4 questions the files can't answer | 4 declined |
| Checker removals in real answers (graded) | 9 of 14 right (2 invented deadlines, 7 miscitations), 5 removed a true statement |
| Summarize, 7 files | 7 completed; **93–95%** of 42 named key facts (81–83% before notes stopped being cut); about 700 words; 33–44 s |

Known gaps: Find misses requests whose file has little prose (a table of
results) or shares no words with them ("what do I need to do in the next two
weeks" → `NEXT_14_DAYS.md`); the checker occasionally removes a true sentence;
the combiner still leaves out "Amdahl" in the CS3210 notes summary.

## Defects the evaluations found

Each was traced to its cause and fixed, and most have a regression test.

| Found by | Symptom | Cause | Fix |
|---|---|---|---|
| Find, 3 cases | The right file never appeared | The planner's guessed folder and type were hard filters | Planner guesses only re-rank; only `--root` / `--type` filter |
| Ask | A question searched the wrong folder | The same over-filtering in Ask's planner | Planner returns queries only |
| Summarize | 2 of 7 summaries failed | Notes capped at 1,600 characters, and the prompt's "[Fn]" placeholder copied as `[Fn: F891]` | A real key in the prompt; citation normalisation |
| Summarize | Summaries cut mid-sentence | The sentence splitter broke on decimals and "e.g." | A splitter that ignores them |
| Checker | `[F1, F2]` citations ignored | Only `[F1]` was parsed | `roles/citations.normalize` |
| Ask | "What optimisations did I try" never found the log | A 91k-character Markdown log was 2 sections | Sections over 3,000 characters split; `reextract --all` |
| Ask | Unanswerable questions ended in a model error | The writer kept asking for more evidence | That now means "your files don't seem to answer that" |
| Ask | Correct answers withheld | The checker prompt said "be strict" and rejected paraphrase | It flags only contradictions or added facts; withhold only if nothing cited survives |
| Ask | Title slides matched without content | One PPTX slide per section | Short matches bring the following sections |
| Summarize | Whole summaries rejected, or emptied by the checker | One stray key rejected everything; one 47k-character judgement | One stray key tolerated; 15 sentences per checker call |
| Ask | True facts removed as miscited | Citations pointed at headings or neighbouring sections | Citations re-pointed in code; bare headings replaced by their content |
| Ask | An answer with no citations skipped the checker | Only cited sentences were checked | Uncited answers are repaired or reported as not found |
| Summarize | The brief's deadline and whole topics missing | A 3,000-character cut of each part's notes dropped their end | Notes kept; the combiner compresses to about 700 words |
| Display | Raw `$\lambda$` and `[F4058]` on Telegram | Telegram doesn't render LaTeX or know keys | Unicode maths and `[p.19]` labels (`readable.py`) |

Tried and rejected: a second opinion on every flagged sentence. On short
passages it is a second draw from the same judge, and requiring both to agree
let false statements through (26 of 30 caught in one run). It now applies only
when the first judgement saw an excerpt of a longer section.
