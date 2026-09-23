# Steward manual testing ledger

## Source-centric pivot baseline — 2026-09-23

The default runtime is now `STEWARD_PRODUCT_MODE=source_centric`. Its active
surface is authorized source roots, scan/reconciliation, document extraction,
lexical/semantic/hybrid retrieval, grounded answers, Telegram Inbox capture,
per-source privacy, source/activity tool calls, and explicit Drive/Gmail
imports. Workspace, Knowledge, Record, Task, Calendar, research, and broad
action flows remain retained legacy code and are intentionally unwired from the
default CLI, Telegram, and tool agent.

Automated baseline completed locally:

- `tests/test_config.py`, `tests/test_read_only_tools.py`, and `tests/test_cli.py`: 58 passed.
- `tests/test_application.py`: passed.
- The supported-file watcher refresh and root-scan telemetry tests pass in
  `tests/test_file_watching.py`, `tests/sources/test_service.py`,
  `tests/test_roots.py`, and `tests/test_cli.py`.
- `tests/sources/test_moves.py` proves an accepted one-to-one rename preserves
  the original source ID, retains its prior location history, and that duplicate
  content creates no move proposal.
- Full automated regression suite: passed locally on 23 September 2026 after
  the source-centric runtime, all-format watcher, root telemetry, and reviewed
  move-reconciliation changes.
- `tests/sources/test_handoff.py` proves a Codex handoff contains selected
  metadata and root guidance paths but omits original source text.
- `tests/sources/test_service.py` covers deterministic source-code extraction
  and stable line-range provenance for Python source files, plus PPTX
  slide-number and XLSX sheet/row provenance.
- Notebook extraction is covered with deterministic markdown/code cell
  provenance in `tests/sources/test_service.py`.
- Full regression suite passed again locally on 23 September 2026 after the
  PPTX, XLSX, notebook, and code-line extraction additions.
- Full regression suite passed again locally after append-only source location
  history was added to accepted move reconciliation.

Still required before declaring the pivot acceptance-complete: run the active
source-centric Telegram checklist with a harmless multi-format root and
demonstrate one external edit/rename followed by scan reconciliation. The full
test suite has passed; historical entries below remain evidence for retained
subsystems, not default-product promises.

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
- The full suite also passed after a Telegram-adapter regression test for an
  older research card: replying after unrelated research and a local restart
  restores only that card's opaque, short-lived research token. This is
  automated transport coverage, not a live external-research acceptance claim.
- The full suite passed after reminder-owner proposal hardening. A different
  authorized Telegram chat is refused before it can create a reminder-change
  review or see the current reminder timestamp; the owner path and stale-review
  protections remain covered.
- Follow-up task-card and list coverage passed with the same boundary: a
  non-owner chat cannot see another chat's reminder in a task card or task
  list, open its edit prompt, or retrieve its timestamp through a reminder
  question. The task remains readable and no reminder/task/Calendar state is
  changed by the refusal.

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

## Post-session automated delivery: exact task follow-ups

- After opening a task, Telegram now understands bounded questions such as
  `when is the deadline?`, `do I have a reminder?`, and `show the linked
  calendar event`. The replies show only the selected task's current local
  state and, for a link, offer a button that re-fetches Google Calendar.
- These questions never infer a Calendar event from a task and never create or
  change a task, reminder, or Calendar event. Focused application coverage
  passed for deadline, reminder, and exact linked-event navigation. The
  complete pytest suite subsequently reached 100% with an empty stderr stream.

## Post-session automated delivery: casual explicit text capture

- Deliberate Telegram capture phrases no longer require punctuation: `remember
  buy milk`, `thought improve task routing`, and `note review CS3210 chapter 4`
  enter the same reversible Intake review as their colon-prefixed forms.
- They stage text locally and still require explicit **Save**; ordinary text
  beginning with a different word (for example `remembering`) remains outside
  this capture rule. Focused application coverage passed. The complete pytest
  suite subsequently reached 100% with an empty stderr stream.

## Post-session automated delivery: exact record-field follow-ups

- After a user deliberately opens a Travel, receipt, or warranty card, bounded
  record questions can return one useful current field rather than making the
  user scan the whole card. Examples include `what is the booking reference?`,
  `how much was it?`, and `when does the warranty end?`.
- The response is local and read-only. It is scoped to the chat's selected
  record, identifies current fragment evidence only when the value can still
  be verified there, and exposes the existing evidence/original actions. It
  neither invokes a model nor searches or writes Calendar.
- Focused application coverage verifies receipt-total and warranty-coverage-end
  answers with source provenance. The full regression suite passed at 100% with
  an empty stderr stream after this delivery.

## Post-session automated delivery: exact Calendar-field follow-ups

- After a user opens a Calendar event, `where is it?` and `what is the
  description?` fetch the selected opaque event ID again and show only the
  requested current field. Missing data is stated plainly instead of inferred.
- These are read-only external refreshes: they retain the narrow Calendar
  reference, offer a Full event action, and never create, edit, link, or delete
  an event. Focused application coverage verifies both location and description
  replies and the three expected current-event reads (open plus two follow-ups).
  The full regression suite passed at 100% after this delivery.

## Post-session automated delivery: Calendar OAuth-scope readiness

- The metadata-only Telegram integrations card now verifies that Calendar token
  metadata declares either Calendar read access or the separately approved
  event-write access. It does not assume that every local Calendar token file
  is usable.
- A token that proves neither presents the existing secret-free local
  reauthorization guidance. The card still makes no provider call, launches no
  browser, and displays no scope string, token, or client path. Focused tests
  cover read/write alternatives and insufficient Calendar metadata. The full
  regression suite passed at 100% after this delivery.

## Post-session automated delivery: source-card derived-text refresh

- Active source cards now expose a compact **Refresh text** action, so an owner
  can initiate the existing reviewed re-extraction workflow without locating a
  source ID or typing an administrative command.
- The button is only a shortcut to the same pending proposal: derived text does
  not change until explicit approval, the original remains unchanged, and the
  existing chat-bound proposal and parser-recovery protections still apply.
  Focused source-card coverage and the full regression suite passed at 100%.

## Post-session automated delivery: natural selected-source refresh

- After an explicit source-card selection, the bounded phrase `refresh this
  text` now stages the same chat-bound re-extraction review as the source-card
  button. It does not invoke the parser and cannot select a source from an
  unscoped request.
- Focused application coverage verifies the exact source ID, pending proposal,
  and unchanged derived text before approval. The full regression suite is
  passed at 100% after this delivery.

## Post-session automated delivery: reviewed standalone Calendar events

- A structured Telegram request beginning `calendar:` can now propose a
  standalone appointment with an explicit title and timezone-aware start/end
  interval. It is distinct from a task deadline marker and a Travel record:
  neither local object is created or inferred.
- The proposal is chat-bound, uses an opaque deterministic idempotency key, and
  cannot contact Google until a visible approval. Focused Calendar and Telegram
  tests verify pending-state safety, repeat-proposal reuse, authorization refusal,
  and the exact writer request. The full regression suite passed at 100% after
  this delivery.

## Post-session automated delivery: chat-bound knowledge reviews

- New Telegram knowledge-enrichment proposals now persist their originating
  chat identity. `/pending` and `/knowledge_proposals` show them only to that
  chat, and guessed review, conflict-resolution, draft, model-suggestion, or
  claim-revision approval commands from another authorized chat refuse before
  changing canonical evidence or claims.
- Migration 55 adds the nullable local-only `chat_id` field without assigning
  an identity to older CLI/tool reviews. Those legacy rows stay inspectable but
  fail closed for Telegram mutations. Focused repository, application, and
  schema coverage verifies owner completion and cross-chat refusal.

## Post-session automated delivery: unified Telegram action boundary

- The shared action-review inbox now shows only reviews explicitly created by
  the current Telegram chat. It no longer treats an absent `chat_id` from a
  local CLI workflow as permission for any authorized Telegram chat to inspect
  or approve that action.
- Telegram delivery-recovery and workspace-link proposal producers now add the
  originating chat ID as well. Legacy unbound actions fail closed for approval
  but can be declined and recreated deliberately. Focused application coverage
  verifies pagination, owner review, cross-chat refusal, and legacy recovery.

## Post-session automated delivery: Hotel Reservation record foundation

- Steward now has a durable local Hotel Reservation record schema with fields
  for property, booking reference, guest, and timezone-aware check-in and
  check-out times. Each saved value must retain a direct source-fragment
  reference; a record with no evidenced field is refused.
- This delivery is deliberately the persistence and provenance foundation only.
  Telegram capture cards, proposal acceptance, record browsing, and search
  integration will use this same proposal object in the next delivery rather
  than duplicating parsing logic.
- Focused record and migration tests passed, followed by the full pytest
  regression suite at 100%.

## Post-session automated delivery: Hotel Reservation Telegram workflow

- A source named as a hotel or reservation can now produce a chat-bound Hotel
  Reservation review, and `/propose_hotel_record SOURCE_ID` provides the same
  explicit route. The preview exposes property, booking reference, guest, and
  check-in/out fields only when extraction directly found them.
- Accepting the review rebuilds the proposal from current local fragments and
  rejects stale evidence before a single transactional local write. Hotel cards
  support source/evidence reads and `/records`; `search_records` can retrieve
  permitted saved hotel fields for a read-only agent. No Calendar event is
  inferred or created from a hotel reservation.
- Focused Telegram, record, and read-only tool coverage passed, followed by
  the full pytest regression suite at 100%.

## Post-session automated delivery: existing-directory onboarding

- `steward onboard-root NAME PATH` now combines the existing local-only root
  authorization and in-place scan into a single first-run command. Optional
  root-relative exclusions are preserved, and a repeat invocation with the
  same configuration reuses the authorization instead of duplicating it.
- The command refuses conflicting names/configurations, never gives Telegram
  filesystem browsing ability, and never moves, copies, renames, or rewrites
  the chosen directory's originals. Focused CLI coverage verifies indexing,
  exclusion handling, and safe repeat onboarding, followed by the full pytest
  regression suite at 100%.

## Post-session automated delivery: record-save next actions

- After accepting a receipt, warranty, or existing Hotel Reservation review,
  Telegram now returns a compact saved-record card rather than an opaque ID in
  plain text. The card opens that exact record, its authoritative source, the
  record list, or Home.
- This is navigation only: it does not create a Calendar event, alter the
  original, or make a record correction. Focused approval coverage and the
  full pytest regression suite passed at 100%.

## Post-session automated delivery: record-correction next actions

- Accepted Travel, receipt, and warranty corrections now return an exact-record
  card with source, record-list, and Home actions rather than a plain success
  sentence. The card makes clear that a correction is user-supplied and does
  not rewrite the original or invent source evidence.
- Focused correction coverage and the full pytest regression suite passed at
  100%.

## Post-session automated delivery: task-completion navigation

- The existing exact-task completion card now also offers Home, alongside the
  completed-task list and exact completed task. Focused restart/reference
  coverage and the full pytest regression suite passed at 100%.

## Post-session automated delivery: knowledge-conflict navigation

- Knowledge conflict review cards retain their explicit keep/disputed/revision
  choices and evidence/source actions, and now include Home. Resolved conflict
  cards also offer Home after the concept/evidence next actions.
- Focused conflict evidence and resolution coverage and the full pytest
  regression suite passed at 100%.

## Post-session automated delivery: local root setup handoff

- When `/roots` finds no authorized roots, Telegram now shows the exact
  `steward onboard-root NAME PATH` command to run on the Steward computer.
  It explicitly states that onboarding indexes the existing directory in place
  and that Telegram cannot choose or browse local folders.
- Focused root-navigation coverage verifies the card, its safe Home action,
  and that no local path is disclosed. The full pytest regression suite passed
  at 100%.

## Post-session automated delivery: organization-decision next actions

- Accepting or declining an organization proposal now returns a compact result
  card rather than ending on a plain sentence. The card opens the exact source,
  then its selected workspace after a move or Inbox after a keep/decline;
  Home is always available.
- This is navigation after the existing reviewed decision. It does not change
  the proposal's LangGraph approval, move policy, workspace selection, or
  original-file preservation guarantees. Focused and full pytest regression
  verification passed at 100%.

## Post-session automated delivery: durable organization-result references

- An older Telegram result card for an approved organization move now restores
  the exact selected workspace when the user replies to it, even after other
  navigation or a restart. Keep-Inbox and declined cards instead restore the
  exact source, which is the only object their outcome identifies.
- The stored reference is opaque and chat-scoped. It adds navigation context
  only; it cannot authorize another move or expose a local filesystem path.
  Focused and full pytest regression verification passed at 100%.

## Post-session automated delivery: Calendar task-link result navigation

- Reviewed task-to-existing-Calendar-event link and unlink result cards now
  retain the exact opaque Calendar event reference and include Home. A reply
  to an older result card can therefore reopen the current provider event,
  rather than using a newer event selected elsewhere in the chat.
- The task/event relationship remains local-only. The result card neither
  creates nor edits Google Calendar content; the reference is navigation state
  only. Focused and full pytest regression verification passed at 100%.

## Post-session automated delivery: retained-research navigation

- Retaining an explicitly reviewed research note or one selected research
  source now produces a source card with **Read content**, **Source details**,
  **Inbox**, and **Home**. The card identifies the exact retained Inbox source
  and persists a chat-scoped source reference for later replies.
- Retention remains explicit and does not rerun research. A duplicate request
  reopens the existing source rather than saving another copy. Focused and full
  pytest regression verification passed at 100%.

## Post-session automated delivery: privacy-review result navigation

- Applied or declined source-privacy reviews now return to the exact source
  with **Open source**, **Privacy options**, and **Home** actions. The card
  persists only the opaque, chat-scoped source reference, allowing ordinary
  source follow-ups after a restart without exposing a local path.
- A fresh privacy option still creates a separate review; this result card does
  not silently change the rule again. Focused and full regression verification
  passed at 100%.

## Post-session automated delivery: approved Calendar-event navigation

- An approved Calendar proposal now preserves the opaque ID returned by the
  provider in its reviewed local proposal. When the writer returns that ID,
  Telegram's success card exposes **Open event**, restores the exact Calendar
  reference for replies, and still offers the general Calendar list and Home.
- This is not a local Calendar mirror: opening the event performs the normal
  current-provider read. Writers that cannot return an ID retain the existing
  safe generic success card. Focused and full pytest regression verification
  passed at 100%.

## Post-session automated delivery: intake-discard recovery

- Choosing **Do not keep** for a staged Telegram item now produces an explicit
  discard card with Inbox, Pending, and Home actions. It states that the local
  staged copy was removed and nothing reached Inbox.
- The corresponding pending intake context is cleared, so a later ordinary
  message cannot accidentally be treated as added context for a discarded
  item. Focused and full pytest regression verification passed at 100%.

## Pending live Telegram acceptance additions

- The manual Telegram checklist now includes the new discard result/context
  cleanup, approved-Calendar-event card, organization-result workspace reply,
  retained-research source card, and privacy-result source-card journeys.
- These are not recorded as live-passed yet: each needs the configured local
  Telegram process and, where applicable, a harmless provider-backed Calendar
  or research result. Automated coverage is recorded with each implementation
  delivery above.

## Post-session automated delivery: workspace-creation navigation

- Accepting a chat-bound **Create workspace** review now returns a workspace
  receipt with **Open workspace**, **Workspaces**, and **Home**. The receipt
  keeps the opaque workspace ID as its Telegram reply reference, so replying
  to it can restore that exact workspace rather than relying on a list position.
- Focused workspace-review coverage and the full pytest regression suite passed
  at 100%. Live Telegram acceptance remains open.

## Post-session automated delivery: curated-note review navigation

- A staged curated note now has a titled, durable review card with **Save
  note**, **Edit**, **Discard**, and **Home**. Its opaque action reference lets
  a reply to that exact card select the intended review after later navigation
  or an adapter restart.
- Once approved, the receipt links to the exact saved Inbox source through
  **Read note** and **Source details**, then offers Inbox and Home. This is
  navigation only: the note is still saved solely by its explicit approval.
- Focused curated-note coverage and the full pytest regression suite passed at
  100%. Live Telegram acceptance remains open.

## Post-session automated delivery: durable pending-review references

- Opened pending **action**, **organization**, **intake**, and **knowledge**
  review cards now preserve their own opaque proposal reference for Telegram.
  A reply to an older card can therefore restore that exact pending decision
  after other navigation or an adapter restart; it never selects an item by
  page position or inferred text.
- This only restores the existing chat-bound review context. Approval,
  rejection, source access, model boundaries, and external-write policies are
  unchanged. Focused review-routing coverage and the full pytest regression
  suite passed at 100%. Live Telegram acceptance remains open.

## Post-session automated delivery: task review-decline navigation

- Declining a reviewed deadline/reminder change, deadline clear, or reminder
  cancellation now offers **Open task** and **Home** and retains the exact
  task as the Telegram reply reference. The accepted and declined outcomes now
  have the same safe return path without implying that Calendar changed.
- Focused task scheduling coverage (including an explicit declined deadline
  review) and the full pytest regression suite passed at 100%. Live Telegram
  acceptance remains open.

## Post-session automated delivery: travel-record receipt navigation

- An approved Travel record now opens its exact provenance-backed record first,
  retains the typed record reference for later Telegram replies, and still
  offers its separate **Add to calendar** review action, Records, and Home.
  No Calendar event is created by saving the record.
- Focused travel-record/Calendar-navigation tests and a full pytest retry
  passed at 100%. One initial full run encountered a transient Windows
  subprocess SQLite-lock release race in `test_runtime`; its isolated rerun
  passed before the successful complete retry.

## Post-session automated delivery: Windows Telegram-runtime recovery

- Telegram runtime ownership now waits for at most one second when SQLite is
  briefly busy/locked after an abrupt local owner exit. It continues to require
  the same exclusive transaction, so an actually live competing polling
  process still fails safely rather than permitting duplicate workers.
- The runtime suite covers normal exit, abrupt exit, live-owner exclusion, and
  bounded abrupt-owner recovery. All four focused runtime tests and the full
  pytest regression suite passed at 100%.

## Post-session automated delivery: durable direct-intake references

- Fresh attachment/text intake cards now preserve their exact staged intake ID
  for Telegram replies, as does the separate **Add context** prompt. A user
  can therefore return to an older capture card or context prompt after other
  navigation/restart without attaching guidance to a different staged item.
- This only restores staged review context; it does not save the item, permit
  model analysis, or make an organization decision. Focused intake tests and
  the full pytest regression suite passed at 100%.

## Post-session automated delivery: Travel-reference provenance navigation

- Accepting a reviewed, fragment-backed additional Travel reference now returns
  a receipt with **Open record**, **Open source**, and Home, retaining the
  exact Travel record reference for later Telegram replies. Declining it also
  returns safely to that record.
- The original source is never modified. Focused Travel record/reference tests
  and the full pytest regression suite passed at 100%.

## Post-session automated delivery: rejected-record source navigation

- Declined Travel, receipt, warranty, and hotel record reviews now provide a
  source/Home receipt with the exact source preserved for later Telegram
  follow-up. A declined review creates no record and never changes the source.
- Focused record coverage and the full pytest regression suite passed at 100%.

## Post-session automated delivery: workspace-link receipt navigation

- Reviewed source-to-workspace links now return exact workspace/source cards on
  approval and an exact source card on rejection. The link remains semantic:
  neither outcome moves the original file.
- Focused workspace-link coverage and the full pytest regression suite passed
  at 100%.

## Post-session acceptance finding: intake context and pending workspace links

- Live Telegram acceptance on 23 September 2026 found two routing defects:
  an explicit second `note:` was consumed as context for an older staged note,
  and replying **show that workspace** to a pending workspace-link review did
  not open the workspace named by that review.
- Both are corrected. Explicit capture prefixes now start a separate staged
  intake instead of being treated as pending context. Pending workspace-link
  cards remain safely bound to their exact action for approval/rejection, but
  their narrow workspace-navigation phrases now resolve to the workspace in
  that same proposal.
- Focused regressions and the complete pytest suite passed at 100%. The two
  corrected paths require a short live Telegram retest before this acceptance
  item is closed.

## Post-session automated delivery: pending action-review navigation

- A reply to an exact pending action-review card can now use narrowly scoped
  follow-ups to reopen the concrete object named by that review: a task,
  existing Calendar event, Travel/receipt/warranty record, or linked workspace.
  The route is derived only from the review's persisted, chat-bound payload;
  it never guesses an ID from text.
- Approval remains independent. `yes` and `no` still target the pending action
  review, while navigation merely opens current read-only detail cards. Another
  chat cannot use the reference, and proposals without an existing object do
  not manufacture one.
- Focused action-navigation regressions and the complete pytest suite passed at
  100%. Live Telegram acceptance remains open.

## Post-session automated delivery: guided record correction

- Travel, receipt, warranty, and hotel detail cards now expose **Correct**. The user
  selects a supported field, supplies a replacement (or explicitly clears it),
  and receives a reviewable correction proposal rather than a direct record
  update.
- The selected record/field is stored as an opaque chat-bound input context, so
  a Telegram restart between field selection and replacement still stages the
  intended correction. `cancel` returns to the current record without changing
  it. The original source is never rewritten, and the record remains unchanged
  until proposal approval.
- Focused restart/picker coverage and the complete pytest suite passed at 100%.
  Live Telegram acceptance remains open.

## Post-session automated delivery: type-specific pending-record navigation

- An active, chat-bound record review now accepts exact read-only follow-ups
  such as **show that receipt**, **show that warranty**, **show that hotel**,
  and **open that reservation**. These phrases reopen only the record ID held
  by that review; a phrase for the wrong record type is refused.
- This does not decide, approve, or alter the pending review. The generic
  **show that record** route remains available, while the existing flight/trip
  phrases remain restricted to Travel records.

## Post-session automated delivery: Responses-API compatibility recovery

- The SoCLaaS/OpenAI-compatible tool adapter now accepts equivalent SDK-object
  and JSON-shaped Responses output items. It also recovers a final message from
  documented message content when a gateway omits the optional `output_text`
  convenience field.
- Malformed function arguments remain ignored rather than becoming a local tool
  call. Tool execution still happens only in Steward's allowlisted `ToolNode`.

## Post-session automated delivery: compact research retention actions

- External research cards now use **Keep note** and numbered **Keep N** actions.
  The full source title and URL remain in the card body, so compact Telegram
  buttons do not obscure which external source the user is explicitly retaining.
