# Steward manual testing ledger

This document records live local acceptance results. It complements automated
tests; it does not replace them. Do not record tokens, credentials, private
source content, or local absolute paths beyond what is necessary to reproduce a
safe test.

## Session: 2026-09-16

### Automated verification

- Full test suite: passed after the source privacy-picker change.
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

**Observed:** `/sources` numbers results from `1` on each page. Those numbers
are page-local navigation positions, but typed commands such as `/source`,
`/privacy`, and reviewed source-maintenance commands require the stable source
ID. A user viewing page two therefore cannot reliably infer the ID to type.

**Current workaround:** Use the item's **Open** button, then the source card's
**Privacy** action; this carries the stable ID internally.

**Required product fix:** Show a compact stable identifier on every source list
and source-detail card (for example, `S42`), while keeping list-position
buttons for navigation. Commands should accept the documented stable identifier
or provide a copy-friendly action. Add pagination tests proving that page-local
positions never masquerade as source IDs.

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
