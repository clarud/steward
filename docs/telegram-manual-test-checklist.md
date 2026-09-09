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

## Tasks and records

- Send `/propose_task remind me to compare OpenMP scheduling before Tuesday`.
  Verify it is a proposal, not yet a task or Calendar event.
- Send `deadline: submit CS3210 lab due Friday` without a command. Verify the
  review card preserves `due Friday` as a cue and does not invent a date or
  timezone.
- Accept it, inspect `/tasks`, then use `/complete_task ID` twice. The second
  response should say it is already completed, not create another audit event.
- With harmless extracted fixtures, run `/propose_travel_record SOURCE_ID`,
  `/propose_receipt_record SOURCE_ID`, and `/propose_warranty_record SOURCE_ID`.
  Verify shown fields name supporting fragment IDs. Reject one and accept one.
- For an accepted travel record, use `/calendar_travel RECORD_ID`; verify no
  Google Calendar event exists until the review action is accepted.

## External systems and privacy

- Complete Google OAuth locally in a browser, never in Telegram.
- Use `/calendar_search` and `/calendar_get EVENT_ID`; compare results with
  Google Calendar directly.
- Use `/drive_search QUERY` or `/gmail_search QUERY`; import exactly one result
  from a button and verify exactly one Inbox source is created.
- Set `/set_privacy SOURCE_ID no_model` and inspect `/privacy SOURCE_ID`.
  Confirm restricted source content is not sent to a cloud-backed model.
- Use `/research QUESTION`. Verify it says **ephemeral, not saved**. Choose
  **Keep as Inbox note** only when you want a labeled note containing the
  provider answer and external URLs.

## Failure and recovery checks

- Temporarily stop the model provider, then ask a question. Verify a clear
  failure message and that source files remain unchanged.
- Disconnect a mapped/network root if you use one; `/roots` should report it as
  missing and `scan-root` should fail without changing unrelated sources.
- Inspect `/deliveries`, `/delivery_history`, and `/dead_letters`. Confirm they
  contain identifiers/status/timestamps only, never message text.
- Run the automated suite after a manual session:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest
  ```

Record any failed command, exact visible response, expected result, and whether
the issue involved a source mutation, external service, or model. That turns a
daily-use problem into a reproducible evaluation case rather than a vague
regression.
