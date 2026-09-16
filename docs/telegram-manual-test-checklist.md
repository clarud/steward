# Telegram Manual Test Checklist

Use a non-sensitive test vault for this checklist. Automated tests verify
service and transport behavior, but only a local run can prove the real bot
token, OAuth credentials, model provider, and machine filesystem work together.

## Drive/Gmail pagination acceptance

With locally authorized test accounts, search for a term matching more than five
items using `/drive_search` and `/gmail_search`. Confirm each card has at most five
numbered results and compact Import buttons. Tap More results until the final page;
confirm later items are reachable and only the selected ID is imported when you
explicitly tap its Import button. Merely browsing must not add Inbox sources.
Restart Steward while a More results button is still within its callback lifetime
(15 minutes by default), then use that button. Confirm continuation retains the
original query. On a provider failure, check Retry and Restart search; no credential,
local token path, or raw provider diagnostic should appear. These checks remain
manual acceptance, separate from the synthetic automated cursor tests.

After importing an item, use its Read content and Source details buttons. Confirm
they open that exact local source. Link workspace should open a selection/review
flow, not move or link anything merely by displaying the import card. Import the
same external item again: the duplicate card should open the existing local source
without claiming that an already organized file has returned to Inbox.

Immediately after import, say `give me the content` without first opening another
card. Repeat after a restart and verify that the imported source remains selected.
If the import reports that conversation selection could not be updated, use the
source-specific buttons instead; do not assume `that` changed its meaning.

## Before testing

### Local moved-root recovery

Using a disposable test root, scan at least two harmless files and record their
source IDs. Stop Telegram/watchers, rename the root directory outside Steward,
then run `steward health --strict`; it should exit 1. Confirm `relocate-root`
without `--confirm` changes nothing. Make one replacement file differ and confirm
the approved command rolls back without changing any root/source path. Restore
the exact bytes, run `steward relocate-root NAME NEW_PATH --confirm`, then
`steward scan-root NAME` and `steward health --strict`. The same source IDs and
hashes should remain and the scan should create no duplicates. Restart Telegram
and read one relocated source. Do not perform this rehearsal on the real vault
until a separate filesystem backup exists.

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

- Open source A and keep its Telegram card. Open source B so it becomes the most
  recent selection. Reply directly to source A's detail/content/summary card with
  `give me the content`; Steward must read A, not B. Restart the bot after opening
  B and repeat against A's existing card. Try the same reply from another test
  chat if authorized; it must not reuse chat A's message mapping. A source-list
  card itself must not select its first result. Cards created before this feature,
  pruned after the latest 500 mappings, or not durably recorded may require their
  explicit Open/Read button. Do not count automated tests as this live acceptance.

- Repeat the older-card test with two workspace details, two task details, and two
  details of the same record type. Reply to the first card after opening the second
  and after restarting Steward. Use `show that workspace`, `show that task`, and
  the matching `show that flight`, `show that receipt`, or `show that warranty`
  phrase. Steward must reopen the object attached to the replied-to message, not
  the newest chat-wide selection. List cards must not select their first entry.

- Repeat with two Calendar event detail cards, including an event whose ID is
  numeric-looking if available. Reply `show that event` to the older card after
  opening the newer card and after restart. Steward must preserve the ID as text
  and fetch current Calendar state for the older event; it must not replay the old
  rendered card as cached truth.

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
  again. If it was deleted or Calendar becomes unavailable, it must preserve the
  selected event ID for an explicit retry and offer Calendar/integration recovery
  actions rather than showing old event details.
- After opening an event, try `what is this?`, `when is it?`, `where is it?`,
  and `show details`. Each must refetch that event without invoking a model or
  creating a Calendar write. The response must remain a Calendar card rather than
  saying the review type is unavailable. Verify detail, result, empty-result, and
  failure cards offer only applicable compact navigation/recovery actions.
- Send `/tasks`, open one task with its compact button, restart the bot, then
  send `show that task`. Verify the detail card survives and **Mark complete**
  remains an explicit action rather than an inferred conversational write.
- Send `/workspaces`, open a workspace card, restart the bot, then send `show
  that workspace` or `show its sources`. Verify it lists only semantic source
  links and never moves or exposes a local root path.
- Send `/roots`, open a root card, and verify it reports only health and
  exclusion count. A missing/disabled root must direct you to local recovery;
  Telegram must never show its path or offer a scan/enable action.
- After opening another card or restarting Telegram, reply to that older root
  card with `show that root`. It must reopen the same health-only root card;
  it must not expose a path or gain a root-management action.
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
- Keep review card A, then open review card B. Reply `what is this?` to A and
  verify A is redrawn. Reply `yes` or `no` to A only when its proposed effect is
  safe to test; Steward must decide A rather than B, including after restart.
  If A is stale or is no longer the one resumable organization workflow for the
  chat, Steward must refuse it and must still never decide B. Repeat across an
  action and one organization, intake, or knowledge review. Opening `/pending`
  alone must not make its first item confirmable by a bare `yes`; choose
  **Review 1** first.
- Reply `show the original` to a source-backed organization, record, or
  knowledge review card. It must identify and offer the exact original source,
  then let source follow-ups resolve against that source after restart. An
  intake or non-source action review must not guess an original.

## Capture, review, and restart

- With more than eight pending reviews, use **Next**/**Previous** in `/pending`.
  All decisions must be reachable, and the heading must show the total count.
  After completing reviews, an older page button should show a remaining page
  rather than incorrectly report that no decisions exist.

- Reopen a curated-note proposal through `/pending`: its full draft and origin
  must remain visible before approval. Reopen a record correction and check the
  record ID, field, and replacement value; it must not claim your correction
  came from source evidence. Its **Open source** action must return to the
  original before you apply or reject the correction. Task reviews must retain
  any scheduled reminder.

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
- Open a staged item through `/pending` rather than its original card. Verify
  it offers the same **Save**, **Use local**, **Allow external**, **Add context**,
  and **Do not keep** choices. Select **Add context**, supply a harmless course
  or project hint, and confirm the revised card remains staged until Save.
- Run `/organize` against Inbox material. Read the suggested destination before
  accepting. Use **Open source** to inspect the original, then reject one
  proposal and accept one only when its physical move is correct.
- For a proposed move, choose **Keep in Inbox**. Verify it becomes a separate
  review card and accepting that card leaves the original in Inbox while
  recording the chosen outcome.
- For an uncertain Inbox proposal, choose **Change workspace**. Browse more than
  six workspaces with Next/Previous, choose a numbered target, and verify Steward
  supersedes the old proposal with a new review card; only accepting that new
  card may move the original. A workspace removed before selection must produce
  a safe retry message and leave the proposal and original unchanged. Typed
  `/organization_context ID CS3210` remains an advanced fallback.
- With more than one matching workspace, say `put it with my CS3210 and CS4226
  course material`. Verify the current proposal remains pending and Steward
  opens the picker; it must not silently choose either workspace or discard the
  review.
- Choose **New workspace**, restart Steward before sending the name, then send a
  harmless new workspace name. Verify Steward first shows a replacement proposal:
  neither the workspace nor the move exists until that proposal is accepted.

## Long-document synthesis

- Source availability and model privacy are rechecked before each long-document
  batch, repair, and combination call, and before displaying the answer.
  Revoking access stops subsequent calls and withholds the result. It cannot
  recall information already sent or cancel an in-flight provider request.
  Automated tests cover revocation at the batch/repair/combination boundaries.

- Summaries and selected-source questions above 60,000 extracted characters
  use evidence batches, then combine their notes. Every section is processed;
  the reply labels the result as multi-pass and warns that condensation can
  omit detail. At most 32 batch calls, one shared format-repair call, and one
  combination call are allowed. A failed batch is retried from original
  evidence once; the repair budget is shared across the entire document.
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
  visible as unresolved and an Evidence reviews button. Choose **Keep claim**,
  **Mark disputed**, or **Needs revision**; this must update the displayed
  outcome without rewriting either text. Open that history and inspect the
  claim, rationale, and evidence. More than eight accepted reviews must be
  reachable through Next/Previous. Acceptance records the user's review;
  a selected outcome records the user's conclusion but does not prove the claim or
  rewrite canonical knowledge. Pending/rejected reviews are not presented as
  accepted evidence. Automated application coverage checks this navigation.
- Repeat with **Needs revision**. Stop/restart the bot while it is asking for
  wording, then send a complete replacement claim as ordinary text. Verify a
  separate action-review card shows the exact wording, original claim ID,
  conflict ID, and evidence fragment. Rejecting must create nothing. On a new
  draft, approve and verify the concept card retains the original as superseded,
  shows the replacement as its reviewed revision, and keeps the evidence review.
  Changing or removing the evidence before approval must leave the action pending.
- With a harmless source whose privacy rule permits the configured model, open
  the same `needs_revision` conflict and choose **Suggest draft**. The resulting
  review must identify a model-generated origin, preserve the original claim and
  conflict evidence, and create no claim before approval. Reject once, then
  repeat and approve only if the proposed wording is accurate. Set the source to
  **No model** or **Local only** while using an external model and confirm the
  suggestion is hidden (and a typed `/suggest_claim_revision ID` request is
  refused without calling a model). A temporarily unavailable model must also
  produce no pending review or canonical change.

## Workspace navigation

- With more than eight workspaces, use Next/Previous to reach every workspace.
  Within a workspace with more than five linked sources, use its separate
  Next/Previous controls to reach every source. Each Open button must target
  the source displayed beside its number. Page navigation preserves the
  selected workspace reference and does not alter membership or file locations.
  Empty workspaces offer Browse sources. Out-of-range pages clamp to the last
  available page; invalid nonpositive page arguments return usage guidance.

## Tasks and records

- `/tasks` offers paginated open tasks and a Completed button. Completed task
  history is also paginated and opens ordinary task details without a Mark
  complete action or an active reminder display. Empty open tasks still link
  to history. Browsing does not reopen, delete, or otherwise mutate tasks.
  Automated coverage verifies access beyond eight open/completed tasks.

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
- Open an explicit task card, then say `mark that task complete`. Verify it
  completes that exact task, reports that no Calendar event changed, and the
  repeated phrase reports it was already completed. Do not expect a phrase like
  `done` in ordinary chat to select or complete a task.
- For a task with an explicit `--due-at` value, use `/calendar_task ID`.
  Verify it is a review card and that accepting it creates only one short
  `Due: ...` deadline marker in Calendar; repeating approval must not duplicate
  the event.
- Reopen that task. It must show a linked Calendar marker and **View calendar**
  instead of offering another creation proposal. The button must fetch current
  Calendar data. Complete the task and verify the task can still be viewed and
  completed independently; this flow does not delete or edit its Calendar event.
- From that opened Calendar marker, say `what task is this for?` or use the
  linked-task follow-up. Verify it offers **Open task** for the original task.
  After opening other cards or restarting Telegram, reply to that older
  **Linked task** card with `show that event`; it must refetch the same exact
  Calendar event. Repeat this from an unlinked event's **No linked task** card.
  Neither follow-up may create or edit a Calendar event.
  Repeat the phrase on an ordinary unlinked Calendar event: Steward must say it
  has no linked task, rather than inventing one.
- On any Calendar event card, use **Tasks**. It must only open Steward's local
  task list; it must not create a task, Calendar event, or task-event link.
- On an ordinary unlinked Calendar event, choose **Link task**. Select one
  harmless open task and verify the review names both objects and explicitly
  says Google Calendar will not change. Before approval, neither card may show
  a relationship. Approve, then confirm the event offers **Linked task**, the
  task offers **View calendar**, and `what task is this for?` opens that exact
  task. Completing it must not change the event. Rejecting a second test review
  must leave both objects separate. Restart while the first review is pending;
  it must remain reviewable. A Calendar outage during approval must leave the
  proposal pending and create no local association or Calendar write.
- Open an unlinked task with a precise deadline and verify **Add to calendar**
  creates a review rather than an event. A task with only a vague due cue, a task
  without a deadline, and a completed unlinked task must not be silently scheduled.
- With harmless extracted fixtures, run `/propose_travel_record SOURCE_ID`,
  `/propose_receipt_record SOURCE_ID`, and `/propose_warranty_record SOURCE_ID`.
  Verify shown fields name supporting fragment IDs. Reject one and accept one.
- After accepting one, use `/record travel ID`, `/record receipt ID`, or
  `/record warranty ID`. Verify each displayed current field identifies its
  supporting source fragment when that exact value is still present there, and
  **Open source** returns to the original. Use **Evidence** to open the exact
  extracted section for each currently supported field. Correct a field, then
  reopen the record: it must be labelled **not source-evidenced** instead of
  inheriting stale extraction provenance, and it must not appear in Evidence.
- On a direct or `/pending` record proposal, use **Open source** before
  accepting. Confirm it opens the exact original while the record remains
  uncreated. This action must also be available on a correction proposal.
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
- Add a harmless labelled `Passenger Name: ...` line to a staged itinerary.
  The travel review and saved record must show the passenger with its supporting
  fragment. An unrelated or unlabelled name must not be guessed as a passenger.
  Use the normal reviewed correction flow to change the passenger and confirm
  the correction is marked as user-supplied rather than source-evidenced.
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
  change** card with **Open source**, and `/privacy SOURCE_ID` still shows the
  previous rule until you approve. After approval, confirm restricted source
  content is not sent to a cloud-backed model. Reject a second proposed change
  and verify the existing rule remains unchanged.
- With `STEWARD_MODEL_PROVIDER=local`, set a harmless source to
  `local_model_only` and ask about it through the tool agent. Verify the local
  model can use it. Switch to an external provider and verify the same source
  is withheld from the tool result.
- With a deliberately unavailable non-sensitive tool-model/provider test
  setup, send `/agent find my CS3210 notes`. Verify Telegram returns a short
  retry-oriented read-only-workflow message without a local path, token, or raw
  provider diagnostic. No source, task, Calendar, or proposal state may change.
- Choose a harmless Markdown source with a known ID and send
  `/propose_reextract SOURCE_ID`. Verify that no derived text changes before
  approval, **Open source** inspects the same original, accepting refreshes its
  fragments, and the original file remains byte-for-byte unchanged.
- With the embedding model already installed locally, send
  `/propose_rebuild_index`. Verify that it is a review card; approval rebuilds
  vectors from existing fragments without reading or changing original files.
- Choose a harmless registered source and send
  `/propose_unregister_source SOURCE_ID`. Verify that it remains registered
  before approval, the review card offers **Open source** and shows only its
  filename, approval removes it from `/sources`, and its original file remains
  on disk unchanged.
- Use `/research QUESTION`. Verify it says **ephemeral, not saved**. Choose
  **Keep this reviewed note** only when you want that exact labeled card,
  including its provider answer and external URLs, retained in Inbox.
- After opening other cards or restarting Telegram, reply to the older research
  card with `keep that research` or `save that research`. Verify only that
  still-live, reviewed card is retained; Steward must not rerun research,
  select a different result, or retain anything after the card expires.
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
  both the canonical claim and source-fragment evidence, with **Flag conflict**
  and **Not a conflict**. Flagging it must log the review, leave the canonical
  claim unchanged, and show the three separate resolution choices. Resolve it
  as disputed and verify both the concept card and saved evidence review display
  that outcome after a bot restart.
- Before choosing a conflict outcome, use **Open source** on the conflict card.
  Verify it opens the source associated with the displayed evidence fragment;
  inspection alone must not accept/reject the review or change the claim.
- Reopen a pending card with `/knowledge_proposal ID`; verify it presents the
  same evidence plus decision buttons (conflicts use **Flag conflict** and **Not
  a conflict**), rather than requiring a manually typed review command.
- Open a concept and a knowledge-evidence card, then open other cards. Reply to
  the older concept with `show that concept`, and to the older evidence card
  with `show that evidence`. Restart Telegram and repeat. Each reply must reopen
  the exact original object, never the newest concept/review in the chat or a
  model-generated interpretation.

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
## Runtime ownership verification

Before a live outage rehearsal, run the synthetic provider matrix:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\graphs\test_tool_agent.py tests\test_calendar.py tests\test_external_search_navigation.py tests\test_application.py -q
```

It must cover bounded SoCLaaS, Ollama, and Gemini adapter failures; Calendar
factory/request/projection failures; and Drive/Gmail search/import recovery. The
tests use synthetic secret-bearing diagnostics and must not print those values.
They do not contact providers or prove live OAuth/quota recovery.

For a controlled extraction failure, approve re-extraction of a non-sensitive
test source whose parser cannot complete. Expect Text refresh incomplete with
Retry refresh, Read stored text, Source details, and Dismiss review, without local
error paths. The explanation should match the source type: PDF mentions native
text/Poppler/Tesseract; images mention Tesseract and image quality/languages; DOCX
mentions genuine Office Open XML; HTML mentions UTF-8/static content; email
mentions body parts versus attachments; Markdown/text mention UTF-8; unsupported
binary formats recommend conversion rather than repeated parsing.
Repair the local dependency before retrying; inspect derived content because
partial refresh is possible. Do not infer original deletion or successful
indexing from this failure card. Actual parser-outage Telegram validation remains
pending despite simulated application coverage.

Open a source with no extracted fragments and choose Read content. Expect an
explanation that no text is stored, the same relevant format guidance, and Review
re-extraction. Tapping that action should create a review, not immediately run
extraction. Check the named source before approval; the original must remain
unchanged. Live verification of this recovery card is still pending.

Calendar tool error privacy is now tested through real tool wrappers with
synthetic secret-bearing failures. For live agent outage checks, expect an
unavailability explanation—not an empty-calendar claim or provider traceback.
Do not paste actual credentials to test redaction. Automated coverage does not
establish the final provider-generated wording in a live Telegram conversation.

Open a Calendar event with a location and description. Verify both appear below
the date, Refresh fetches edits, and long descriptions are explicitly labeled
as truncated. Missing optional fields should not show `None`. This uses current
provider data; rendering with actual Telegram remains a manual acceptance item.

For Calendar outage acceptance, open an event, temporarily disconnect the test
runtime from the network, and ask “show that event”. Expect a generic recovery
card, no private diagnostic path, and no claim that the event was deleted.
Restore connectivity and tap Retry: it should fetch the current event and show
Refresh/Upcoming actions. This live procedure is not established by the mocked
provider recovery test; do not alter OAuth tokens merely to simulate an outage.

A disk-backed organization graph test now rehearses stopped-writer backup and
restore of both operational and checkpoint databases. It confirms restored
pending state does nothing until explicit approval. During live Telegram recovery,
also verify that old buttons reference the intended restored proposal: restoring
SQLite cannot retract or restore already delivered Telegram messages. That
external callback acceptance step remains unverified by the graph test.

An automated synthetic rehearsal (`tests/test_recovery_workflow.py`) verifies
restoring a pending knowledge review and completing it in a fresh interpreter.
For live restore acceptance, stop Steward and preserve both operational and
conversation state before proceeding. Confirm that pending cards and callbacks
refer to the restored review, and reconcile any external actions performed
after the backup instead of assuming rollback undoes them. This live procedure
has not been performed by the synthetic test.

Local runtime ownership tests now also exercise a live subprocess contender and
forced termination using temporary directories. They do not test Telegram API
delivery. During manual deployment acceptance, verify only one poller uses the
bot token across all machines/data directories; the SQLite guard coordinates
one data directory, not the bot token globally. Do not delete a runtime lock
file as a substitute for checking and stopping the actual process.

# Reminder claim ownership regression

Automated coverage verifies that a worker with an expired claim cannot release
or acknowledge a newer claim, including after a reminder is rescheduled.
Migration 45 adds a per-claim token; claim selection and acquisition use one
SQLite write transaction. Telegram delivery passes that token back when it
acknowledges or releases the claim. This does not guarantee exactly-once external
delivery: a crash after Telegram accepts a message may still cause a retry.
Stop older Steward versions before starting the upgraded runtime.

## Knowledge review transaction coverage

After accepting an enrichment, inspect its concept and Evidence reviews.
Current interpretations should have a **Current evidence version** label. If
the source becomes missing or the saved content changes, the concept should
count the review as historical rather than summarize it as current evidence.
The history entry must remain inspectable and labeled **Historical only**.
Restoring unchanged evidence should restore its current label. Automated
application tests cover missing/restored source transitions; live validation
of these cards remains pending.

Knowledge review reliability is covered separately by automated audit-failure
injection: failed audit persistence must leave the proposal pending, and retry
must produce one review event. Acceptance of a missing supporting source is
refused. Snapshot regression tests also change claim text, evidence text,
location, and source hash: old acceptance must fail, fresh proposals must be
reviewable, and outdated accepted interpretations must not enter current lookup.
Migration coverage preserves legacy IDs/status without inventing evidence versions.
In Telegram, reopen a proposal after a local evidence change: the card must show
its saved version, and acceptance must request a fresh proposal. Live acceptance
of this scenario is still pending.
The stale-approval reply should offer **Fresh review**, **View saved review**,
and **Dismiss old review**. Fresh review creates only a preview using current
claim/evidence; inspect and accept it separately. The saved-review action must
still show the previous version. Dismissing the old proposal must not reject
the newly accepted one. Automated application coverage exercises these exact
button commands; live Telegram acceptance is still required.

## Guided source-to-workspace linking

After selecting a source, ask “Which workspace is this in?” The reply should
name that source and list its actual workspace memberships, or explicitly say
there are none. Check the same follow-up after restart. Use Open to inspect a
workspace, and Link workspace to start a separately approved link. More than
eight memberships must be reachable through Next/Previous. No model is needed
for this lookup, and the response must not claim memberships are physical folders.

Open an active source from `/sources`, tap **Link workspace**, and select a
workspace. The review must name both the original file and destination workspace
and say that no file moves. Before approval, workspace membership must remain
unchanged. Approve, reopen the picker, and confirm the workspace now reads
“already linked” with a View action. Repeat approval must not duplicate the link.
With more than eight workspaces, check Next and Previous and select a workspace
on the second page. These are live acceptance steps, not yet verified in Telegram.

Automated failure injection also covers an audit insert failing during approval:
membership and approval must roll back together. Retrying creates one link and
one approval audit; duplicate approval does not duplicate either. Once reviewed,
an opposite decision is refused. This is local transaction coverage, not proof
of Telegram delivery or crash recovery across every workflow.
