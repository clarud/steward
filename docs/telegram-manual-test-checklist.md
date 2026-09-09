# Telegram Manual Test Checklist

Use a non-sensitive test vault for this checklist. Automated tests verify
service and transport behavior, but only a local run can prove the real bot
token, OAuth credentials, model provider, and machine filesystem work together.

## Before testing

1. Activate the project virtual environment.
2. Confirm `.env` contains a Telegram bot token and an allowlisted chat ID.
3. Use a separate `STEWARD_DATA_DIR` and `STEWARD_INBOX_DIR` for testing.
4. Add and scan a harmless test root locally:

   ```powershell
   steward add-root "Telegram Test" "C:\path\to\test-vault" --exclude generated
   steward scan-root "Telegram Test"
   steward telegram
   ```

5. Do not send credentials, real medical/financial documents, or confidential
   course material while testing cloud-backed models or research.

## Core reads and routing

- Send `/status`; verify it reports local counts and delivery health, never a
  token or message body.
- Send `/help`; verify the listed commands are understandable.
- Send `/sources`, `/inbox`, `/search OpenMP`, `/source 1`, `/workspaces`, and
  `/activity`; use pagination where it appears.
- Ask a normal local question and a follow-up that uses a reference such as
  “How does that relate to TLBs?” Verify citations point to test-vault sources.
- Send the same update twice only if you can safely reproduce it; verify a
  duplicate does not create duplicate capture/proposal state.

## Capture, review, and restart

- Send a document without `/save`. Verify it is described as staged and has
  **Save to Inbox** and **Do not keep** choices.
- Choose **Do not keep**. Verify no file appears in Inbox.
- Send another document, add `/intake_context ID CS3210 OpenMP assignment`,
  then choose **Save to Inbox**. Verify the original appears once in Inbox.
- While an intake or organization proposal is pending, stop the local bot with
  `Ctrl+C`, start `steward telegram` again, then finish the decision. Verify
  the result happens once.
- Run `/organize` against Inbox material. Read the suggested destination before
  accepting; reject one proposal and accept one only when its physical move is
  correct.
- For an uncertain Inbox proposal, send `/organization_context ID CS3210` using
  an existing workspace name. Verify Steward supersedes the old proposal with
  a new review card; only accepting that new card may move the original file.

## Tasks and records

- Send `/propose_task remind me to compare OpenMP scheduling before Tuesday`.
  Verify it is a proposal, not yet a task or Calendar event.
- Send `deadline: submit CS3210 lab due Friday` without a command. Verify the
  review card preserves `due Friday` as a cue and does not invent a date or
  timezone.
- For a precise deadline, send `/propose_task submit CS3210 lab --due-at
  2026-09-18T23:59:00+08:00`. Verify the review card shows the normalized UTC
  instant. A timestamp without an explicit offset must be rejected rather than
  silently assigned your computer's time zone.
- For a deliberate Telegram reminder, send `/propose_task submit CS3210 lab
  --remind-at 2026-09-18T09:00:00+08:00`. Verify the review card shows the UTC
  reminder instant, acceptance adds it to `/tasks`, and it is delivered only
  to the chat that proposed it. Do not use a real past timestamp unless you
  want the reminder to be delivered as soon as the bot is running.
- Accept it, inspect `/tasks`, then use `/complete_task ID` twice. The second
  response should say it is already completed, not create another audit event.
- For a task with an explicit `--due-at` value, use `/calendar_task ID`.
  Verify it is a review card and that accepting it creates only one short
  `Due: ...` deadline marker in Calendar; repeating approval must not duplicate
  the event.
- With harmless extracted fixtures, run `/propose_travel_record SOURCE_ID`,
  `/propose_receipt_record SOURCE_ID`, and `/propose_warranty_record SOURCE_ID`.
  Verify shown fields name supporting fragment IDs. Reject one and accept one.
- For an accepted travel record, use `/calendar_travel RECORD_ID`; verify no
  Google Calendar event exists until the review action is accepted.
- For a saved travel record, use `/propose_travel_reference RECORD_ID TYPE
  FRAGMENT_ID VALUE` with an exact value that appears in the record's source
  fragment. Verify it stays pending until approval and `/travel_references
  RECORD_ID` shows the saved reference and fragment ID afterward.

## External systems and privacy

- Complete Google OAuth locally in a browser, never in Telegram.
- Use `/calendar_search` and `/calendar_get EVENT_ID`; compare results with
  Google Calendar directly.
- Use `/drive_search QUERY` or `/gmail_search QUERY`; import exactly one result
  from a button and verify exactly one Inbox source is created.
- Set `/set_privacy SOURCE_ID no_model` and inspect `/privacy SOURCE_ID`.
  Confirm restricted source content is not sent to a cloud-backed model.
- Choose a harmless Markdown source with a known ID and send
  `/propose_reextract SOURCE_ID`. Verify that no derived text changes before
  approval, accepting refreshes its fragments, and the original file remains
  byte-for-byte unchanged.
- With the embedding model already installed locally, send
  `/propose_rebuild_index`. Verify that it is a review card; approval rebuilds
  vectors from existing fragments without reading or changing original files.
- Use `/research QUESTION`. Verify it says **ephemeral, not saved**. Choose
  **Keep this reviewed note** only when you want that exact labeled card,
  including its provider answer and external URLs, retained in Inbox.
- From the same research card, choose exactly one **Keep source** action.
  Verify it creates a separate Inbox Markdown reference containing that source's
  URL, title, query, provider, and any search-result snippet—not a downloaded
  copy of the webpage.
- With the local embedding model installed, compare `/search TERMS`,
  `/semantic_search QUESTION`, and `/hybrid_search QUESTION`. Verify that all
  results identify only filenames, source IDs, and derived locations; none
  invoke an external model or expose local directories.
- Reply to a text discussion message with `/curate`. Verify the staged note
  identifies its origin as a user-selected Telegram reply, is not saved before
  approval, and becomes a labeled Inbox Markdown note only after approval.
- Reply to a non-sensitive discussion message with `/curate_synthesize local`.
  Verify that the returned model draft is marked as local-model synthesis and
  remains unsaved until approval. Use `external` only when you deliberately
  authorize sending that reply to the configured external model.
- Create a harmless contradiction enrichment proposal and verify the card shows
  both the canonical claim and source-fragment evidence. Accepting it must log
  the review while leaving the canonical claim text unchanged.

## Failure and recovery checks

- Before and after restarting the local Telegram process, run `steward health`.
  Confirm it reports database/checkpoint availability, root counts, and only
  whether a Telegram token is configured—never paths, source text, or tokens.
- Temporarily stop the model provider, then ask a question. Verify a clear
  failure message and that source files remain unchanged.
- Disconnect a mapped/network root if you use one; `/roots` should report it as
  missing and `scan-root` should fail without changing unrelated sources.
- Do not run a second scan while another local Steward process is writing its
  database. If SQLite reports that it is busy, `steward scan` and
  `steward scan-root NAME` should stop with a retry instruction, not a Python
  traceback. The original files must remain unchanged.
- Inspect `/deliveries`, `/delivery_history`, and `/dead_letters`. Confirm they
  contain identifiers/status/timestamps only, never message text.
- For a test-only terminal failure, inspect it locally with
  `steward telegram-dead-letters`, then run
  `steward telegram-recover-dead-letter telegram:UPDATE_ID --confirm` only if
  Telegram can genuinely deliver that update again. This resets the retry
  budget; it never replays unavailable message content.
- In Telegram, `/recover_dead_letter telegram:UPDATE_ID` must first show a
  review card. Approve it only for a test update that Telegram can genuinely
  redeliver; rejection leaves the dead letter terminal.
- Run the automated suite after a manual session:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest
  ```

Record any failed command, exact visible response, expected result, and whether
the issue involved a source mutation, external service, or model. That turns a
daily-use problem into a reproducible evaluation case rather than a vague
regression.
