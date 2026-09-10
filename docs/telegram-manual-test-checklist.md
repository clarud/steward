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

- Try a harmless long note containing emoji and a long title. Each delivered
  part should render correctly, the complete title/text should remain readable,
  and action buttons should appear only on the final part.

- Open a source and choose **Ask about it**. Restart once before sending the
  question; the question must still use the chosen document. **Cancel** returns
  to its source card. Answers retain the same model privacy rule and evidence
  reference checks as summaries.

- Open a harmless source and choose **Summarize**, or send `summarize it`.
  Check the generated response uses that document and provides fragment
  locations. A source whose privacy rule denies the configured model must
  receive a denial before model processing. This test sends the selected
  source's text to the configured permitted model.

- Send `/home` with and without pending reviews. Verify it offers source,
  Inbox, Calendar, task, workspace, and review navigation. When `/tasks` is
  empty, follow its example to propose a task and verify saving still needs
  approval.

- Open a source, choose **Read content**, and use **Next**/**Previous** to
  browse extracted sections. Send `give me the content` after opening it,
  including after restart. Check that the text belongs to the selected source
  and retains its page/heading location. Section numbering follows extraction
  units, which may differ from physical PDF page numbers.

- Send `/status`; verify it reports local counts and delivery health, never a
  token or message body.
- Send `/metrics`; verify it reports only aggregate activity-event counts, not
  filenames, paths, source text, or individual event details.
- Send `/help`; verify the listed commands are understandable.
- Send an unknown command such as `/steward_typo`. Verify Steward replies with
  a safe clarification/helpful next step instead of silently ignoring it.
- Send `/sources`, `/inbox`, `/search OpenMP`, `/source 1`, `/workspaces`, and
  `/activity`; use pagination where it appears.
- Open a source card with `/source ID`, restart the bot, then send `open the
  last source` or `show that PDF`. Verify Steward reopens only that exact
  source card; a broader question about a source must remain a normal
  retrieval question rather than being guessed as navigation.
- Open a Travel Record with `/record travel ID`, restart the bot, then send
  `show that flight`. Verify it reopens that exact flight card; do the
  equivalent for a receipt or warranty only after opening a card of that type.
- Search Calendar, open one event card, restart the bot, then send `show that
  event`. Verify Steward fetches and shows that same current Calendar event
  again. If it was deleted or Calendar becomes unavailable, it must clear the
  stale reference and direct you to search Calendar again rather than showing
  old event details.
- Send `/tasks`, open one task with its compact button, restart the bot, then
  send `show that task`. Verify the detail card survives and **Mark complete**
  remains an explicit action rather than an inferred conversational write.
- Send `/workspaces`, open a workspace card, restart the bot, then send `show
  that workspace`. Verify it lists only semantic source links and never moves
  or exposes a local root path.
- Send `/roots`, open a root card, and verify it reports only health and
  exclusion count. A missing/disabled root must direct you to local recovery;
  Telegram must never show its path or offer a scan/enable action.
- Open a deliberately long harmless source or broad search result. Verify a
  long answer arrives as consecutive readable messages rather than a Telegram
  delivery error, and any action buttons appear only on the final message.
- Ask a normal local question and a follow-up that uses a reference such as
  “How does that relate to TLBs?” Verify citations point to test-vault sources.
- Reply to an earlier harmless discussion message with a normal question such
  as `How does this relate?`. Verify Steward uses the replied-to message as
  context, rather than treating only the immediately preceding chat turn as
  the referent.
- Send the same update twice only if you can safely reproduce it; verify a
  duplicate does not create duplicate capture/proposal state.
- If Telegram visibly retries a review-button callback, verify it does not
  accept/reject the proposal twice or send a second confirmation.
- Open a pending action through `/pending`, then reply `yes` (or `no`) to its
  card instead of using its button. Verify only that exact card is accepted
  (or rejected); a stale card or a card from another chat must not authorize a
  change.

## Capture, review, and restart

- With more than eight pending reviews, use **Next**/**Previous** in `/pending`.
  All decisions must be reachable, and the heading must show the total count.
  After completing reviews, an older page button should show a remaining page
  rather than incorrectly report that no decisions exist.

- Reopen a curated-note proposal through `/pending`: its full draft and origin
  must remain visible before approval. Reopen a record correction and check the
  record ID, field, and replacement value; it must not claim your correction
  came from source evidence. Task reviews must retain any scheduled reminder.

- Leave an organization proposal pending, then open a source card. Send `yes`:
  the older organization proposal must remain pending. Reopen its review from
  `/pending` before deciding, or use that proposal's explicit button. Ordinary
  questions after changing cards must not be trapped in the older review.

- Send a document without `/save`. Verify it is described as staged and has
  **Save to Inbox** and **Do not keep** choices.
- Reply to that original attachment with bare `/save`. Verify Steward saves
  that same staged attachment once, without asking you to upload it again.
  Replying from another chat or to a non-staged message must not select a
  pending attachment.
- Send a harmless `https://...` link. Verify it is staged as a **reference**;
  Steward must not fetch it, send it to a model, or save it until you choose
  **Save**.
- Choose **Do not keep**. Verify no file appears in Inbox.
- Send another document, add `/intake_context ID CS3210 OpenMP assignment`,
  then choose **Save to Inbox**. Verify the original appears once in Inbox.
- Prefer the card flow as well: choose **Add context**, then reply normally
  with `CS3210 OpenMP assignment`. Restart the bot before replying once; the
  reply should still revise the same pending card without saving it.
- If local staging is deliberately unavailable in a test setup, verify an
  intake accept/discard/context retry message never exposes the staging path.
- While an intake or organization proposal is pending, stop the local bot with
  `Ctrl+C`, start `steward telegram` again, then finish the decision. Verify
  the result happens once.
- Run `/organize` against Inbox material. Read the suggested destination before
  accepting; reject one proposal and accept one only when its physical move is
  correct.
- For a proposed move, choose **Keep in Inbox**. Verify it becomes a separate
  review card and accepting that card leaves the original in Inbox while
  recording the chosen outcome.
- For an uncertain Inbox proposal, send `/organization_context ID CS3210` using
  an existing workspace name. Verify Steward supersedes the old proposal with
  a new review card; only accepting that new card may move the original file.

## Long-document synthesis

- Summaries and selected-source questions above 60,000 extracted characters
  use evidence batches, then combine their notes. Every section is processed;
  the reply labels the result as multi-pass and warns that condensation can
  omit detail. At most 32 batch calls and one combination call are allowed.
  This costs more model requests than a short-document summary.
- Every batch and final answer must use supplied citation keys. A failed,
  oversized, or uncited batch must not yield a complete-looking partial answer.
  Citation membership is checked, not semantic truth or coverage of every fact.
  Tests cover final-section inclusion and early budget refusal. No live-provider
  or real Telegram long-document acceptance is claimed yet.

## Original document delivery

- After selecting a source, say “send me that PDF” or “send the original”.
  Steward should show the selected filename and ask for a Send original click,
  explicitly noting the Telegram transfer. No attachment is sent merely by
  opening this confirmation. This reference survives restart and is scoped
  to the originating chat; without a selected source Steward must not guess.
  It takes precedence over the selected-source question prompt.

- Open a source card and choose Send original. The original bytes should arrive
  as a Telegram document with only its filename, not a local path. This is an
  explicit external transfer through Telegram, not an LLM request. Reads of
  extracted text and model summaries remain separate actions.
- Delivery uses Steward's conservative 20 MiB cap. Missing/inactive originals,
  changed hashes, excluded paths, and paths outside enabled roots or the
  configured Inbox are refused. Root checks resolve paths before access.
  The cap is an application policy, not a statement of Telegram's maximum.
- Automated export and adapter tests cover boundaries and delivered bytes.
  Real Telegram document delivery remains a manual acceptance check. A crash
  after Telegram accepts the attachment can still cause duplicate delivery.

## External import recovery

- Status must report missing required access ahead of token refreshability.
  An expired metadata-only Drive token still needs local reauthorization for
  downloads, even if it contains a refresh token. Local readiness checks do
  not contact Google or prove that a token is currently accepted by Google.
  Regression coverage includes missing, malformed, and expired timestamps,
  with both string and list scope representations.

- An unavailable Drive/Gmail service or expired authorization must return a
  secret-free recovery card, not a traceback. Retry targets the same item/query;
  Integrations and Inbox remain available. Authorization must happen on the local
  Steward computer, never by sending credentials in Telegram. Check Inbox
  after ambiguous import failures: a failed response is not proof of rollback.
  Automated tests cover OS and provider-style exceptions without disclosure.

## Knowledge disagreement visibility

- Home → Knowledge opens saved concepts without requiring a remembered name.
  More than eight concepts use Next/Previous; Open buttons identify concepts
  by ID, then expose their claims and Evidence reviews. All concepts returns
  to the browser. An empty knowledge store offers Sources rather than claiming
  that no source material exists. Automated tests cover pagination and opening
  a concept on the second page.

- The read-only agent's knowledge results include permitted accepted review
  evidence and caution that acceptance is not proof. Claim text is withheld
  when any supporting fragment is unavailable or its source policy forbids
  the selected model. Review evidence is filtered separately. Local-only and
  no-model regression tests cover this boundary. Missing registered sources
  are also excluded from source, record, and knowledge tool content. When all
  of a concept's claims are withheld, the entire concept result is withheld
  too. Empty concepts and mixed public/private concepts still lack an explicit
  independent concept-level privacy policy.

- Accept a contradiction review, then open its concept with `/knowledge NAME`.
  The original claim must remain intact, with the accepted contradiction
  visible and an Evidence reviews button. Open that history and inspect the
  claim, rationale, and evidence. More than eight accepted reviews must be
  reachable through Next/Previous. Acceptance records the user's review;
  it does not prove the claim, automatically resolve the contradiction, or
  rewrite canonical knowledge. Pending/rejected reviews are not presented as
  accepted evidence. Automated application coverage checks this navigation.

## Workspace navigation

- With more than eight workspaces, use Next/Previous to reach every workspace.
  Within a workspace with more than five linked sources, use its separate
  Next/Previous controls to reach every source. Each Open button must target
  the source displayed beside its number. Page navigation preserves the
  selected workspace reference and does not alter membership or file locations.
  Empty workspaces offer Browse sources. Out-of-range pages clamp to the last
  available page; invalid nonpositive page arguments return usage guidance.

## Tasks and records

- Calendar proposals from travel records and tasks show readable dates and
  the flight/task title before approval, including disclosure of any booking
  reference sent in the event description. Reopening through `/pending` uses
  the same reviewed snapshot. Change the record/task after proposing: approval
  must refuse the stale proposal before a Calendar write. Request a new preview.
  Existing linked Calendar events are reused, not updated by this flow.

- Reopen a travel, receipt, or warranty extraction proposal through `/pending`.
  Check the source filename, current field values, and evidence fragment IDs.
  Merely opening the preview must not create a record. These previews use the
  reviewed field values and evidence fingerprints. Approval refuses changed
  evidence or legacy proposals without snapshots; request a new proposal to
  review the current values. Test this by re-extracting changed content after
  opening a preview, then pressing its old Accept button. No record should be
  created. Travel, receipt, and warranty paths have automated regression coverage.
  Snapshot validation and record persistence share a SQLite write transaction;
  automated two-connection tests verify concurrent evidence writes are blocked.
  The source must still be active. For these Telegram record approvals, record
  insertion, evidence, accepted status, and the acceptance audit share one
  transaction. Failure-injection tests verify rollback and repeated approvals
  cannot create a second record from the same proposal. Separate proposals
  for the same source are not deduplicated by this guarantee.
  Stale reviews offer Fresh preview, View source, and Dismiss old review.
  Pressing Fresh preview must show the new fields without saving a record;
  a second explicit approval is required. Dismissing the old review must
  leave the source intact. Automated application tests exercise the fresh
  preview button route for travel, receipt, and warranty records.

- Send `/propose_task remind me to compare OpenMP scheduling before Tuesday`.
  Verify it is a proposal, not yet a task or Calendar event.
- Send `deadline: submit CS3210 lab due Friday` without a command. Verify the
  review card preserves `due Friday` as a cue and does not invent a date or
  timezone.
- Send `I need to submit CS3210 lab by Friday`. Verify it produces the same
  reviewable task proposal. Send `I need to understand TLBs` separately; it
  must not be treated as a task solely because it begins with `I need to`.
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
- After accepting one, use `/record travel ID`, `/record receipt ID`, or
  `/record warranty ID`. Verify each displayed current field identifies its
  supporting source fragment when that exact value is still present there, and
  **Open source** returns to the original. Correct a field, then reopen the
  record: it must be labelled **not source-evidenced** instead of inheriting
  stale extraction provenance.
- For an accepted travel record, use `/calendar_travel RECORD_ID`; verify no
  Google Calendar event exists until the review action is accepted.
- For a staged flight message with `Flight`, `Departure`, `Arrival`, and
  `Booking Reference` lines, choose **Save**. Verify Inbox capture is followed
  by a Travel Record review card, not an immediately created record. After
  accepting that record, use **Add to calendar** and verify it opens a second,
  separately approved Calendar proposal.
- On any Travel, receipt, or warranty review card, choose **Organize Inbox**.
  Verify Steward opens a separate organization review rather than moving the
  original or accepting the record automatically.
- For a saved travel record, use `/propose_travel_reference RECORD_ID TYPE
  FRAGMENT_ID VALUE` with an exact value that appears in the record's source
  fragment. Verify it stays pending until approval and `/travel_references
  RECORD_ID` shows the saved reference and fragment ID afterward.

## External systems and privacy

- Use `/integrations`. It must report only local readiness, never a client
  path, token value, or refresh token. A missing integration says it needs
  local browser authorization; an expired token says whether local refresh may
  be available or local reauthorization is required. Telegram must not launch
  the OAuth flow.

- Complete Google OAuth locally in a browser, never in Telegram.
- Use `/calendar_search` and `/calendar_get EVENT_ID`; compare results with
  Google Calendar directly.
- With no search terms, Calendar lists upcoming/ongoing events. A named search
  can still find past events. Check readable dates and UTC offsets on both list
  and detail cards; single-day all-day events should show one date, not the
  provider's exclusive next-day end date.
- With a deliberately unavailable test Calendar authorization, verify a
  Calendar read returns a retry-oriented message without a local token path or
  provider diagnostic.
- Use `/drive_search QUERY` or `/gmail_search QUERY`; import exactly one result
  from a button and verify exactly one Inbox source is created.
- With a deliberately unavailable test authorization, verify Drive/Gmail
  search or import returns a retry-oriented message without a local token path,
  client-secret filename, or provider diagnostic.
- Set `/set_privacy SOURCE_ID no_model`. Verify it opens a **Review privacy
  change** card, and `/privacy SOURCE_ID` still shows the previous rule until
  you approve. After approval, confirm restricted source content is not sent
  to a cloud-backed model. Reject a second proposed change and verify the
  existing rule remains unchanged.
- With `STEWARD_MODEL_PROVIDER=local`, set a harmless source to
  `local_model_only` and ask about it through the tool agent. Verify the local
  model can use it. Switch to an external provider and verify the same source
  is withheld from the tool result.
- Choose a harmless Markdown source with a known ID and send
  `/propose_reextract SOURCE_ID`. Verify that no derived text changes before
  approval, accepting refreshes its fragments, and the original file remains
  byte-for-byte unchanged.
- With the embedding model already installed locally, send
  `/propose_rebuild_index`. Verify that it is a review card; approval rebuilds
  vectors from existing fragments without reading or changing original files.
- Choose a harmless registered source and send
  `/propose_unregister_source SOURCE_ID`. Verify that it remains registered
  before approval, the review card shows only its filename, approval removes it
  from `/sources`, and its original file remains on disk unchanged.
- Use `/research QUESTION`. Verify it says **ephemeral, not saved**. Choose
  **Keep this reviewed note** only when you want that exact labeled card,
  including its provider answer and external URLs, retained in Inbox.
- With a deliberately unavailable test research provider, verify the failure
  reply is retry-oriented and does not expose a provider diagnostic or local
  configuration path.
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
  Confirm approval ends with a **Curated note saved** card offering **Inbox**,
  while rejection ends with **Curated note declined** and does not create a
  source.
- Reply to a non-sensitive discussion message with `/curate_synthesize local`.
  Verify that the returned model draft is marked as local-model synthesis and
  remains unsaved until approval. Use `external` only when you deliberately
  authorize sending that reply to the configured external model.
- On that curated-note card, choose **Edit**, then send replacement Markdown as
  the next ordinary message. Verify that Steward shows a new pending review,
  the old draft remains auditable as superseded, and only the edited draft can
  be saved to Inbox. Repeat once after restarting the local bot between
  **Edit** and the replacement message.
- Create a harmless contradiction enrichment proposal and verify the card shows
  both the canonical claim and source-fragment evidence. Accepting it must log
  the review while leaving the canonical claim text unchanged.
- Reopen a pending card with `/knowledge_proposal ID`; verify it presents the
  same evidence plus **Accept** and **Reject** buttons, rather than requiring a
  manually typed review command.

## Failure and recovery checks

- After restarting into the version with the runtime guard, try starting a
  second `steward telegram` with the same data directory. It must report an
  existing instance before polling. After the original exits, startup must
  work without deleting `telegram-runtime.db`.

- With reminders enabled, stop the bot while its reminder worker is idle.
  It should exit promptly rather than wait for the next 60-second poll. Restart
  and verify pending reminders remain available. In-flight network deliveries
  may take until their request completes before shutdown finishes.

- Before and after restarting the local Telegram process, run `steward health`.
  Confirm it reports database/checkpoint availability, root counts, and only
  whether a Telegram token is configured—never paths, source text, or tokens.
- Run Telegram `/status` as well. Confirm it reports only operational/checkpoint
  availability, aggregate root health, and configured provider name—never local
  paths, source text, model URLs, or credentials.
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
# Reminder claim ownership regression

Automated coverage verifies that a worker with an expired claim cannot release
or acknowledge a newer claim, including after a reminder is rescheduled.
Migration 45 adds a per-claim token; claim selection and acquisition use one
SQLite write transaction. Telegram delivery passes that token back when it
acknowledges or releases the claim. This does not guarantee exactly-once external
delivery: a crash after Telegram accepts a message may still cause a retry.
Stop older Steward versions before starting the upgraded runtime.
