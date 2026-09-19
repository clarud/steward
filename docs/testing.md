# Steward manual testing ledger

This document records live local acceptance results. It complements automated
tests; it does not replace them. Do not record tokens, credentials, private
source content, or local absolute paths beyond what is necessary to reproduce a
safe test.

## Session: 2026-09-16

### Automated verification

- Full test suite: passed after the source privacy-picker change.
- The documented synthetic provider matrix passed on 17 September 2026:
  `tests/graphs/test_tool_agent.py`, `tests/test_calendar.py`,
  `tests/test_external_search_navigation.py`, and
  `tests/test_application.py`. It covers mocked provider/Calendar/import
  recovery only; it does not claim live OAuth, quota, or network-outage proof.
- The isolated `tests/test_recovery_workflow.py` backup/restore rehearsal also
  passed on 17 September 2026. It validates restored pending local state in a
  disposable SQLite environment; it does not retract delivered Telegram
  messages or substitute for a live restore exercise.
- The run exposed a stale database migration-ledger expectation: schema version
  53 (`task_calendar_associations`) existed but was omitted from the test. The
  test was corrected and the full suite then passed.
- Follow-up automated coverage passed for stable source IDs, revoked-token
  recovery, and reviewed task-to-Calendar associations.
- Later focused automated coverage also passed for privacy-gated claim-revision
  drafts, travel passenger provenance, and durable Telegram knowledge-card
  references.

### Session conclusion

The 2026-09-16 local and Telegram acceptance batch is complete and passed for
the exercised features. No unresolved functional defect was found in that
batch. The remaining items in this ledger are deliberately deferred specialist
rehearsals: a naturally revoked OAuth token, provider-unavailable recovery, and
future feature-specific Telegram checks. They are not failures and should not
be marked as passed until the relevant real condition occurs or a safe,
non-sensitive test environment is available.

### Passed

#### Local moved-root recovery

- Registered root: `Telegram Test`.
- A renamed/missing root caused `steward health --strict` to report one missing
  root, as expected.
- `relocate-root` without `--confirm` changed no state.
- An invalid replacement directory was rejected without relocation.
- The confirmed relocation verified and updated 19 tracked source paths.
- Follow-up scan reported `new=0 updated=0 unchanged=19 missing=0`.
- Strict health then reported one available root and no missing roots.

#### Google Drive

- Search was read-only and pagination showed at most five results per page.
- More-results continuation survived a Telegram restart.
- Explicit Drive import created an Inbox source and extracted DOCX content.
- Read content, source details, and generated summary with fragment evidence
  worked for the imported source.
- Re-importing the same Drive item was idempotent: Steward opened the existing
  local source and did not create a duplicate.

#### Gmail

- Search, pagination, restart continuation, explicit import, source reading,
  and duplicate protection were manually verified as working.

#### Durable Telegram source references

- After opening source A and then source B, replying to source A's older card
  with `give me the content` correctly read source A.
- The same reply-to-card reference remained correct after a Telegram restart.

#### Google Calendar reads and event references

- Calendar authorization was renewed successfully after the expired-token
  recovery described below.
- `/calendar_search` displayed readable, local-time event dates and UTC offsets.
- Opening event A, then event B, and replying `show that event` to event A's
  older card correctly re-opened event A rather than the newer selection.
- Unthreaded `show that event` also re-opened the selected event.
- `when is it?`, `where is it?`, and `show details` re-fetched the selected
  event and displayed its current time, location, and description.
- A newly visible Calendar event appeared in a later search, consistent with
  querying current provider state rather than replaying cached list content.

#### Task navigation and explicit-write safety

- Task detail reopened correctly through `show that task` after a Telegram
  restart.
- The **Tasks** action on a Calendar event opened only Steward's local task
  list; it did not create a task, edit/create a Calendar event, or create a
  task–Calendar link.
- Task completion remained an explicit action rather than an inferred response
  to ordinary conversational language.

#### Workspace context and reviewed source linking

- An opened workspace re-opened correctly through `show that workspace` after
  a Telegram restart.
- `show its sources` listed semantic source memberships without exposing local
  root paths or moving files.
- **Link workspace** opened a review that named the selected source and target
  workspace; viewing the review did not create a membership or move a file.

#### Source privacy picker

- Source cards exposed **Privacy** and model-blocked summary cards exposed
  **Change privacy**.
- The picker displayed the current rule and compact replacement choices.
- A selected replacement created a **Review privacy change** card; the active
  policy remained unchanged until explicit approval.
- Applying the review successfully updated the intended source privacy rule.

#### Reviewed task-to-Calendar association

- An existing harmless Calendar event could be selected from an event card and
  proposed for an open local task.
- Approval created only Steward's local association; it did not create, edit,
  or delete a Google Calendar event.
- The resulting task and event cards each exposed the same relationship.

#### Guided Telegram acceptance batch

- The review-context safety and staged-capture checks were completed in the
  guided local session and reported as working.
- The current guided manual batch is complete. The open issues and remaining
  specialist recovery rehearsals below remain intentionally tracked rather than
  being treated as passed by this summary.

### Errors, fixes, and follow-up rehearsals

No unresolved functional defect was found in the completed 2026-09-16 manual
acceptance batch. The issues below are retained as resolved history so their
regression coverage and safe operating guidance remain discoverable.

#### Source list pagination does not expose stable source IDs — open bug

**Status: resolved.** The heading is retained as historical context for the
original report. Each source-list entry now shows `ID N`, including subsequent
pages, and each source card shows `Source ID: N`. List ordinals remain only
page-local button labels; every **Open** command carries the exact stable ID.
Automated pagination coverage verifies source 11 appears as `ID 11` on page
two. The same behavior passed the guided Telegram acceptance batch.

#### Calendar OAuth refresh recovery — open bug

**Observed:** `steward calendar-search` failed because Google rejected the
stored Calendar token as expired or revoked. The Telegram Calendar card safely
reported Calendar as unavailable and made no Calendar change.

**Root cause:** `authorize_google_calendar()` loads an existing token and calls
`credentials.refresh(Request())`. A failed refresh is not caught, so
`steward calendar-authorize CLIENT_SECRETS` exits with a traceback rather than
opening the browser consent flow.

**Temporary recovery:** Preserve the unusable token by renaming it (rather than
deleting it), then run Calendar authorization again. Use the actual configured
token and client-secret paths; do not commit either file.

**Required product fix:** Catch the provider refresh exception, discard the
in-memory credentials for that run, start the local browser OAuth flow, and
write a new token only after authorization succeeds. Add automated coverage for
an expired/revoked refresh token. Telegram must continue to hide raw provider
diagnostics and all credential material.

#### Resolved after the initial session

##### Stable source IDs on pagination

Source lists now show `ID N` on every entry, including page two and later
pages. Source-detail cards show `Source ID: N`; list ordinals remain only
page-local navigation positions. Automated pagination coverage verifies that
source 11 appears as `ID 11` on page two.

**Live check:** Passed in the completed Telegram acceptance batch.

##### Revoked Google OAuth token recovery

Calendar, Drive, and Gmail authorization now catch Google's revoked-token
refresh exception, discard the in-memory credential, and start local browser
consent. A new token is written only after consent succeeds. Parameterized
automated tests cover all three integrations.

**Live check pending:** Do not revoke a working real token solely for testing.
The next genuine expired/revoked-token event should launch browser consent
without a traceback. Telegram must continue to show only safe recovery text.

#### Calendar duplicate-reply observation — dismissed

One unthreaded `show that event` appeared to have two identical cards in the
initial transcript. It was not reproduced and is not tracked as a defect. The
misspelled `show deatils` clarification is expected behavior, not a defect.

## Historical acceptance instructions — completed

### Source privacy picker

Source cards now include a compact **Privacy** action. When a source is blocked
from the configured model, the denial card includes **Change privacy** alongside
**Read content**. Both open a picker showing the current rule and compact
replacement choices:

- **Allow cloud** → `external_allowed`
- **Local only** → `local_model_only`
- **No model** → `no_model`
- **Block external** → `external_redacted`

Choosing a replacement creates the existing **Review privacy change** card; it
does not alter the policy until **Apply privacy rule** is explicitly approved.
The picker is covered by automated application tests.

### Manual acceptance steps

1. Open a harmless source with `/source SOURCE_ID` and tap **Privacy**.
2. Confirm the current rule is shown and buttons are compact/readable.
3. Choose **Allow cloud** (or another deliberate replacement) and confirm the
   review names the source and old/new rule.
4. Before approval, run `/privacy SOURCE_ID`; the old rule must remain active.
5. Reject the review for a no-mutation test, or approve it and confirm the new
   rule permits/blocks the configured model as expected.
6. For a source currently blocked from the configured model, tap **Summarize**
   and confirm **Change privacy** reaches the same review-required picker.

### Future specialist rehearsals

- Do not revoke a working OAuth token merely to test recovery. When a genuine
  revoked/expired Calendar, Drive, or Gmail token occurs, confirm browser
  consent opens without a traceback and Telegram displays only safe recovery
  text.
- Exercise Drive/Gmail unavailable-provider recovery cards only with a
  non-sensitive test authorization.
- Continue the broader Telegram manual checklist as new features are added.

## Post-session automated delivery: model-assisted claim revision drafts

- A configured model can suggest one replacement only for an accepted,
  `needs_revision` contradiction review with a saved evidence snapshot.
- The suggestion is source-privacy-gated before any evidence is passed to a
  model and is staged as a separately approved action proposal; it cannot
  mutate canonical knowledge directly.
- Focused application tests passed for a successful local-model suggestion,
  zero-call denial under source privacy, and provider unavailability. The new
  live Telegram steps are in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: richer travel-record provenance

- Migration 54 adds an optional `passenger` field to existing travel records
  without changing prior records.
- Deterministic extraction accepts only labelled `Passenger Name` or `Traveler
  Name` values and records the supporting fragment; the reviewed record card
  displays that provenance.
- Record/migration/application coverage passed for extraction, approval,
  persistence, user correction, and Telegram rendering. The harmless live
  itinerary exercise is recorded in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: durable knowledge-card references

- Concept and knowledge-evidence cards now persist opaque Telegram references,
  allowing exact `show that concept` and `show that evidence` follow-ups after
  other cards have been opened or the bot restarts.
- Focused application and adapter suites passed. The real Telegram restart and
  older-card reply exercise is recorded in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: durable Calendar task-follow-up references

- **Linked task** and **No linked task** cards now retain the originating
  opaque Calendar event ID. An older-card reply can therefore request the
  current event without selecting a newer event or treating the local task
  association as a Calendar mutation.
- Focused application coverage passed. The corresponding harmless Telegram
  restart exercise is included in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: durable root-card references

- An opened authorized-root card now retains only its opaque local root ID.
  Exact `show that root` or `open that root` follow-ups can restore the same
  health-only card after a restart without disclosing a local path or granting
  Telegram any scan, enablement, relocation, or other root-management action.
- Focused application coverage passed. The manual restart exercise is included
  in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: read-only tool-workflow recovery

- Unexpected exceptions crossing the Telegram tool-agent boundary now produce a
  generic, secret-free temporary-unavailability reply. The local log records
  only the exception class; raw provider diagnostics and filesystem paths are
  not returned to Telegram.
- Focused tests passed for recursion recovery, known model-gateway recovery,
  and an unexpected exception containing deliberately private-looking text.
  The manual non-sensitive provider-outage rehearsal remains in the checklist.

## Post-session automated delivery: exact-card research retention

- A research result card now persists only an opaque, chat-bound ephemeral
  token. `keep/save that research` retains the exact still-live reviewed bundle
  after a restart without rerunning the provider or selecting another result.
- Focused research coverage passed for one provider call, restart recovery,
  expiry behavior, and the retained note's exact reviewed answer. The harmless
  Telegram exercise is included in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: complete pending-intake controls

- A staged item opened from `/pending` now exposes the same Save, local/external
  model-boundary selection, Add context, and Do not keep actions as its original
  intake card. Context selection revises only the staged review; it does not
  save or move the original material.
- Focused application coverage passed for displayed actions and unchanged source
  state. The matching Telegram exercise is included in the manual checklist.

## Post-session automated delivery: Telegram activity-event inspection

- `/activity` now provides compact **Open** actions for individual redacted
  audit events. An event card is read-only, names when/type/opaque object ID,
  and explicitly states that opening it cannot replay the recorded operation.
- Focused coverage passed for path redaction, exact event selection, and durable
  `show that activity` navigation after restart. The manual Telegram exercise
  is included in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: timestamp record provenance

- Record evidence verification now compares a timestamp field's stored
  ISO-8601 value with its extracted fragment, while Telegram still displays a
  readable offset-aware time. This prevents presentation formatting from
  incorrectly marking a supported time as unsupported.
- Focused record-card coverage passed for a labelled travel departure timestamp
  and its Evidence entry. The matching manual record test is in the checklist.

## Acceptance-pass closeout — 16 September 2026

The current local and Telegram acceptance pass is complete for the features
exercised in this session. Automated coverage was rerun after the final
provenance change, and the following manual scenarios were confirmed:

- moved-root recovery preserved 19 source identities and hashes with no
  duplicate scan results;
- Drive search pagination, import, repeat-import idempotency, source reading,
  and generated summaries worked as expected;
- Gmail search and inbox import worked as expected;
- Calendar consent recovery restored access after a revoked token, and Telegram
  displayed offset-aware local dates, event details, and exact-card follow-ups;
- source privacy-rule updates worked through both the local interface and
  Telegram.

The remaining checklist items are not known product failures. They are
specialist rehearsals intentionally deferred because they require a controlled
provider outage, revoked non-sensitive integration token, or separate backup
environment: SoCLaaS, Ollama, Gemini, Calendar, Drive, and Gmail outage paths;
backup/restore; and start-at-login registration. Do not revoke a healthy
production authorization merely to complete them.

## Post-session automated delivery: paginated multi-vault root browsing

- Telegram `/roots [page]` now exposes at most eight health-only root cards per
  page, with compact **Next**/**Previous** navigation. Root IDs remain opaque
  selection handles; no page or button reveals a local path or enables remote
  scans, relocation, authorization, or other filesystem mutation.
- Focused application coverage exercises nine authorized roots, page bounds,
  compact action labels, and path redaction. The optional live multi-root
  exercise is in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: durable claim-revision approval card

- A staged claim-revision card now preserves only its opaque action-review ID
  for Telegram. This lets an explicit `yes` reply resume the same pending
  review after a restart; normal policy validation still decides whether the
  revision can be created.
- Focused application coverage asserts the exact reference. The corresponding
  harmless Telegram restart exercise is in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: reviewed task–Calendar unlink

- An incorrect association between an existing Calendar event and a Steward
  task can now be removed through a separate review. Approval verifies the
  exact local relationship, removes only that SQLite row, and records Activity;
  Google Calendar is neither read nor changed.
- Repository and application coverage verify one-to-one safety, stale-event
  refusal, cross-chat refusal, action-card context, audit recording, and zero
  additional Calendar calls during unlink. The live task-card exercise is in
  `telegram-manual-test-checklist.md`.

## Post-session automated delivery: travel-record Calendar navigation

- A Travel record with an approved Calendar-event link now displays a
  **View calendar** action and identifies the relationship without duplicating
  Calendar fields locally. Opening it remains a fresh external Calendar read.
- Focused record-card and CLI-composition coverage passed. The live harmless
  travel-event exercise is in `telegram-manual-test-checklist.md`.

## Post-session automated delivery: paginated record browsing

- `/records [page]` now follows the same compact navigation pattern as Sources,
  Tasks, and Roots. It renders at most eight records per page while each action
  retains the exact record type and opaque local ID.
- Focused application coverage passes for nine records, page bounds, and the
  second-page record action. The optional harmless Telegram exercise is in the
  manual checklist.

## Post-session automated delivery: paginated pending-action browsing

- `/action_proposals [page]` now retains every pending review behind compact
  Next/Previous pages. Review buttons keep the exact proposal ID rather than
  treating a page-local ordinal as an authorization target.
- Focused application coverage passes for nine pending reviews, page bounds,
  and a second-page review action. The optional Telegram exercise is in the
  manual checklist.

## Post-session automated delivery: paginated research-source choices

- Research cards now expose more than eight cited sources through local,
  ephemeral Next/Previous pages. The provider is not rerun; source-retention
  buttons preserve each source's original bundle index.
- Focused application coverage passes for a nine-source bundle, page-two
  retention commands, and exactly one provider request. The live harmless
  exercise is in the manual checklist.

## Post-session automated delivery: resilient lexical filename search

- FTS5 now keeps valid advanced queries intact, but retries punctuation-heavy
  ordinary input as an escaped literal phrase when SQLite rejects its syntax.
  A filename-like search such as `COURSE_DETAILS.md` can therefore return a
  normal read-only result instead of failing an agent tool loop.
- Focused repository and read-only-tool coverage passed for that recovery path.
- The complete automated suite subsequently reached 100% with no stderr after
  this change.

## Post-session automated delivery: reviewed task deadline changes

- An open task can now collect a replacement offset-aware deadline from its
  Telegram card and present an explicit review. Approval compares the displayed
  old deadline against current local state, so stale reviews fail closed.
- The operation changes only the local task deadline. It intentionally leaves
  Telegram reminders and linked Calendar deadline markers unchanged, and its
  card/result says so instead of suggesting that external state moved with it.
- Focused task and application coverage passed for approval, UTC normalization,
  audit activity, and stale-review refusal.
- The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: reviewed task reminder changes

- A task card can now set or change a pending Telegram reminder through a
  separate stale-safe review. An existing reminder remains bound to its
  originating chat; a review from another chat cannot redirect its delivery.
- Reminder approval changes neither the canonical task deadline nor any
  Calendar relationship. Focused task, application, and Telegram-adapter
  coverage passed for the review, UTC normalization, stale refusal, and
  cross-chat boundary.
- The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: linked travel Calendar navigation

- A local `calendar_event_links` lookup now maps a reviewed
  Steward-created travel event back to its persisted travel record. The current
  Calendar card exposes **Linked trip** only for that exact opaque link.
- The follow-up opens the record through an ordinary local card and can return
  to the current externally authoritative event. Unlinked Calendar events
  explicitly report that no linked trip exists; they do not become travel
  records by title matching. This feature performs no Calendar write.
- Focused Calendar, application, and Telegram-adapter tests passed, followed
  by a complete suite run at 100% with no stderr.

## Post-session automated delivery: natural Travel-record Calendar review

- After an exact Travel record has been opened in a chat, the bounded request
  `put this flight in calendar` now creates the normal pending Calendar review.
  It cannot select an arbitrary trip, write an event, or start Calendar OAuth.
- The application test verifies that the review remains pending and no local
  Calendar-event link exists before an explicit later approval.
- Focused application/Calendar/Telegram coverage passed, followed by a complete
  suite run at 100% with no stderr.

## Post-session automated delivery: natural source privacy review

- An exact source card can now accept bounded phrases such as `keep this
  local`, which produces the existing reviewable `local_model_only` proposal.
  The phrase cannot select a source without the card context and never changes
  a policy directly.
- Automated application coverage verifies the source remains externally
  allowed until explicit approval, then changes only after that approval.
- The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: chat-bound privacy reviews

- New Telegram source-privacy proposals store the originating chat ID. A
  different authorized chat cannot approve or replace the pending decision.
  Legacy unbound proposals fail closed for approval and can be rejected to make
  room for a fresh bound review.
- Automated application coverage verifies cross-chat refusal, no pre-approval
  policy change, owner approval, and legacy-review recovery.
- The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: chat-bound Calendar event reviews

- Telegram-created travel and task Calendar proposals now store their
  originating chat. Another authorized chat cannot approve the external write;
  a legacy unbound proposal fails closed for approval and can be rejected for a
  fresh review.
- Automated application coverage verifies cross-chat refusal before the writer
  is called, owner approval, and legacy-review recovery.
- The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: chat-bound record reviews

- New Telegram record proposals retain the initiating chat ID for Travel,
  receipt, warranty, correction, and Travel-reference operations. Another
  authorized chat cannot approve the mutation; the proposal remains pending
  for the initiating chat to approve or reject.
- Focused application and record coverage verifies cross-chat refusal, no
  pre-approval record creation, and normal owner approval for a Travel record.
- The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: contextual record inspection

- After a chat explicitly opens a persisted local record, bounded inspection
  phrases such as `show details`, `when does this flight leave?`, and `where is
  it going?` reopen that exact record card. They do not invoke a model, query
  Calendar, or mutate state.
- Automated application coverage verifies the exact Travel-record route after
  restart. An unscoped phrase is deliberately not treated as a record lookup.
- The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: independent record and organization intake reviews

- Accepting record-classified intake now creates both a pending evidence-backed
  record review and the normal pending organization review for the one saved
  source. The record card links to the exact organization review instead of
  treating a record decision as a filing decision.
- Focused application coverage verifies that the original remains in Inbox,
  both reviews remain pending, and opening the organization card neither moves
  the source nor creates the record.
- The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: evidence-driven generic-file record routing

- After explicit Save and local extraction, a generic filename can receive one
  pending Travel, receipt, or warranty review when a unique record type has at
  least two labelled, source-backed fields. Filename hints still take priority.
- A single incidental label or an ambiguous highest score creates no record
  proposal. No extracted text is sent to a model and no record is created by
  this routing step.
- Focused application coverage verifies a generic receipt and the one-label
false-positive guard. The complete automated suite subsequently reached 100%
with no stderr.

## Post-session automated delivery: organization moves retain workspace meaning

- A reviewed move to an existing workspace now also creates the local,
  idempotent semantic source-to-workspace relationship. The physical folder and
  Workspace card therefore describe the same approved association.
- Focused end-to-end intake/organization coverage verifies the source is moved,
  linked once, and audited as both a move and a new workspace relationship.
- The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: transparent agent answer origins

- Production LangChain agent responses now identify whether the current turn
  was generated without a Steward lookup, used saved material, used Calendar,
  or used mixed read-only tools. The classification ignores tool messages from
  earlier conversation turns.
- Focused application coverage verifies generated and saved-material cards.
  The complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: natural curated-note retention

- Replying to a specific Telegram message with **keep this as a note** now
  stages the same editable curated-note review as `/curate`. The reply is not
  silently captured, and no source exists until the ordinary explicit approval
  is accepted.
- Focused application coverage verifies the selected text, pending state, and
  later approved Inbox source. The complete automated suite subsequently
  reached 100% with no stderr.

## Post-session automated delivery: chat-bound curated-note reviews

- Curated-note drafts now retain their originating Telegram chat in local
  proposal metadata. Another authorized chat cannot approve or edit a draft,
  create an Inbox source from it, or replace it. Older drafts without that
  metadata fail closed for approval and editing but can be discarded safely.
- Focused application coverage verifies cross-chat refusal, pending-state
  preservation, normal owner approval, and cross-chat edit refusal. The
  complete automated suite subsequently reached 100% with no stderr.

## Post-session automated delivery: chat-bound task creation

- A task proposal already records the originating Telegram chat; its approval
  path now enforces that boundary. Another authorized chat cannot create the
  task from the draft, while an older unbound review fails closed for approval
  and remains rejectable.
- Focused application coverage verifies the cross-chat refusal before normal
  owner approval. The complete automated suite subsequently reached 100% with
  no stderr.

## Acceptance wrap-up — 17 September 2026

The current guided validation batch is complete and passed.

- **Root recovery:** a renamed local root was relocated by its registered root
  name, then scanned with `new=0`, `updated=0`, `unchanged=19`, and `missing=0`.
  Strict health subsequently reported the root available. This preserved the
  existing tracked source identities rather than creating duplicates.
- **Drive:** metadata search pagination, explicit import, duplicate-import
  recovery, source reading, and generated summary were exercised successfully.
- **Gmail:** search and explicit import were exercised successfully.
- **Calendar:** an expired/revoked OAuth token was diagnosed, reauthorized, and
  then Calendar search, event cards, reply-context lookups, date formatting,
  location, and detail follow-ups worked in Telegram.
- **Privacy:** a source privacy-rule update was exercised successfully,
  including the Telegram-facing review flow.
- **Telegram:** source/record cards, page navigation, pending reviews,
  contextual source and Calendar follow-ups, and restart recovery were
  exercised successfully in the configured test chat.
- **Automated regression coverage:** the complete pytest suite reached 100%
  with an empty stderr stream after the latest automated deliveries.

This is an acceptance record, not a claim that the product has no future work.
Source-list pages now show each stable source ID alongside the filename and each
open button targets that same ID, so page-local numbering no longer obscures the
source to open. Remaining planned product improvements include more compact
action labels, richer guided next actions, and broader natural-language routing.
They are UX/delivery backlog items rather than failures found in this validation
batch.

## Deployment state: registered start-at-login task â€” 20 September 2026

- With explicit user authorization, Windows Task Scheduler now contains the
  local-only `Steward Telegram` task.
- Read-back verification confirmed the expected virtual-environment executable,
  project working directory, `telegram`-only arguments (no secrets),
  `IgnoreNew` duplicate-instance policy, and three bounded restart attempts.
- Before startup, a foreground-poller inspection found no running Steward
  Telegram process. The scheduled task was then started and remained **Running**
  across a short observation interval; Task Scheduler reported its normal
  running result and `steward health --strict` passed concurrently.
- Remaining live acceptance: send one harmless Telegram request and receive a
  reply from the scheduled process, verify an actual Windows logon trigger, then
  exercise a controlled provider-outage/recovery path.

## Post-session automated delivery: chat-bound maintenance reviews

- Telegram-originated workspace creation, derived-text re-extraction,
  semantic-index rebuilding, and metadata-unregistering proposals now retain
  the requesting chat ID. They are hidden from another authorized chat's
  `/pending` view and cannot be accepted or rejected from that chat.
- A guessed review ID that is denied in another chat also creates no Telegram
  review context, so a later `yes` cannot be interpreted as a confirmation of
  an inaccessible card.
- Proposals created by a local CLI operator intentionally retain no Telegram
  identity and retain their established behavior.
- Focused action-proposal and application coverage passed, including
  cross-chat invisibility and cross-chat approval refusal. The complete pytest
  suite subsequently reached 100% with an empty stderr stream.

## Post-session automated delivery: chat-bound organization cards

- Organization approvals were already executed through a chat-specific durable
  LangGraph thread. The generic Telegram review inbox now consults that same
  mapping, so a different authorized chat cannot list or render the pending
  organization card even by guessing its numeric review ID.
- Focused organization, CLI, and application coverage passed for owner access
  and cross-chat refusal. The complete pytest suite subsequently reached 100%
  with an empty stderr stream.
