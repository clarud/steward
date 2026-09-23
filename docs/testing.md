# Testing

Automated tests prove services and transport behaviour. Only a local run
proves that the real bot token, model provider, OAuth credentials, and
filesystem work together, so Steward uses both.

## Automated

```powershell
.venv\Scripts\python -m pytest              # full suite
.venv\Scripts\python -m pyflakes src tests  # unused imports, undefined names
```

- Tests create temporary databases and folders. They never touch `.steward/`,
  the Inbox, or a real vault.
- Models, Telegram, and Google are replaced by fakes. No test makes a network
  call.
- Layout mirrors `src/`: `tests/sources`, `tests/extraction`, `tests/retrieval`,
  `tests/answer`, `tests/graphs`, `tests/telegram`, `tests/storage`, plus
  `tests/test_application.py` for the `steward.app` use cases and
  `tests/test_cli.py` for commands.
- `tests/storage/test_database.py` checks the migration ledger and the
  destructive-migration snapshot. Add a test there for any new migration.
- `tests/test_recovery_workflow.py` rehearses backup, restore, and a fresh
  interpreter reading the restored state.
- `tests/evaluation/` holds retrieval cases. `steward evaluate-retrieval VAULT
  CASES.yaml --mode hybrid` runs cases against a real index and reports
  Recall@5 and MRR.

## Against real data, safely

Never experiment on the live database. Snapshot it and point Steward at the
copy:

```powershell
steward backup --destination "$env:TEMP\steward-check"
$env:STEWARD_DATA_DIR = "$env:TEMP\steward-check"
steward health --strict
steward roots
steward scan-root "Y4S1"
steward hybrid-search "TLB"
Remove-Item Env:STEWARD_DATA_DIR
```

`scan-root` only reads the originals; it writes to the copied database.

## Live Telegram acceptance

Follow [telegram-manual-test-checklist.md](telegram-manual-test-checklist.md)
with a harmless test root. Record results below. Never record tokens,
credentials, private file content, or absolute paths beyond what reproduces a
safe test.

## Results

### 2026-09-24: legacy removal (ADR-007)

- Full suite: 405 passed. The suite was 656 before this change; the difference
  is the deleted legacy tests. Nothing live failed.
- Core install in a clean virtual environment without extras: `search` works,
  and `hybrid-search` and `drive-authorize` print the extra to install.
- Migration 65 on a copy of the real database: snapshot written first, 25 legacy
  tables dropped, 3 non-privacy action proposals removed. Sources, fragments,
  embeddings, activity, roots, and Telegram state unchanged. `health --strict`,
  `roots`, `activity` (including retired event types), `scan-root`, `search`,
  and `hybrid-search` succeeded on the copy.
- Not yet run: the live Telegram checklist after the change, and `ask` against a
  cloud model.

### 2026-09-23: source-centric baseline

Full suite passed after the all-format watcher, root scan telemetry, reviewed
move reconciliation, location history, and PPTX/XLSX/notebook/code extraction.
Live Telegram checks passed for root telemetry, source reading and retrieval,
grounded source replies, Inbox staging/save/discard, and the privacy picker.
