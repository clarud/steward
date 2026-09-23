# Source-Centric Product Pivot Plan

## Decision

Steward will shift from a broad personal knowledge-and-action assistant toward
a **local-first personal source memory and retrieval system**.

Its primary promise becomes:

> Connect existing folders, preserve and track their sources over time, accept
> new information through Telegram, and recover information by meaning rather
> than by filename, folder, or exact wording.

The target is not to replace Codex as a general filesystem or coding agent.
Instead, the two products have deliberately different responsibilities:

```text
Steward
  source registry, change tracking, extraction, provenance, retrieval,
  Telegram Inbox, privacy boundaries, and bounded Codex handoff

Codex
  explicitly authorized directory creation, rename/move batches, repository-
  specific instruction following, document edits, tracker updates, and code
  work
```

The immediate reference workflow is the existing `Y4S1` course folder:

```text
existing course tree
→ Steward indexes it in place
→ Telegram retrieves grounded answers from it
→ new files/notes arrive in Steward Inbox
→ Codex organizes a selected bounded batch according to local instructions
→ Steward reconciles the filesystem and retains source history
```

## Why pivot

The existing source, extraction, retrieval, provenance, Telegram, privacy, and
root-management foundations already form a useful daily product. Broad action
domains introduce substantial surface area without being the strongest answer
to the main user problem:

```text
“I have information somewhere, but I do not remember its name, location,
format, or exact wording. Help me find and understand it.”
```

Codex is currently better suited to free-form filesystem operations because it
can examine a repository's local instructions and execute a user-authorized,
explicitly scoped change. Steward should remain dependable even when Codex is
not open, and should make a later Codex handoff more informed rather than
competing with it.

## Product boundary

### In scope and enabled by default

```text
authorized source roots
source registration and content hashes
all-format reconciliation scans and change tracking
extraction, fragment provenance, and extraction recovery
lexical, semantic, and hybrid retrieval
grounded Telegram/CLI questions and source reading
Telegram Inbox for saved files, messages, links, photos, and later voice
source privacy rules and selected-provider model gateways
source activity/audit history
selected Drive/Gmail imports as explicit source capture
bounded local Codex handoff manifests
```

### Retained but disabled by default

These existing domains are not deleted. Their schema, history, and read-only
inspection remain available so the pivot does not destroy user data. New public
Telegram and agent entry points are hidden or return a short explanation that
the feature is not enabled in source-centric mode.

```text
workspace creation, source linking, and organization/move proposals
knowledge concepts, claims, enrichment, and conflict reviews
Travel, receipt, warranty, hotel, task, and reminder workflows
Google Calendar reads, proposals, associations, and writes
external research/retention workflows
agent tools outside source search/read, Inbox, root health, and activity
```

Existing pending write proposals must never remain executable through a legacy
button or command after the pivot. They remain inspectable and are marked as
disabled/superseded with an Activity record, or require an explicit later
feature re-enable and fresh review. No existing source, record, claim,
workspace, token, or historical Activity is deleted merely because its feature
is disabled.

### Explicit non-goals for this delivery program

- unrestricted shell or arbitrary filesystem access through Telegram;
- autonomous moves, renames, directory creation, or tracker edits;
- automatic interpretation of an arbitrary `AGENTS.md` file as executable
  authority for Steward;
- a hosted multi-user service, cloud filesystem mirror, or background sync;
- replacing Codex's coding, repository-editing, or general task capability.

## Current-state assessment

Steward already has a strong base for this pivot:

| Capability | Current position | Pivot decision |
| --- | --- | --- |
| Source roots, hashes, scans, missing-source detection | Implemented | Strengthen |
| Markdown/PDF/DOCX/HTML/text/image extraction | Implemented | Broaden |
| FTS5/BM25, embeddings, hybrid retrieval, cited answers | Implemented | Make primary interaction |
| Telegram conversations, cards, source browsing, privacy | Implemented | Simplify around sources and Inbox |
| Inbox capture and explicit import | Implemented | Make primary capture path |
| Workspaces and organization approval | Implemented | Disable as a default write surface |
| Knowledge/claims/enrichment | Implemented in part | Retain data, disable default surface |
| Records/tasks/Calendar | Implemented in part | Disable default surface |
| Drive/Gmail import | Implemented | Retain as explicit source import only |
| File watcher | Markdown-only | Replace with all-format source synchronization |
| External rename/move identity preservation | Not implemented | Highest source-lifecycle priority |
| PPTX, code, spreadsheet extraction | Not implemented | Add progressively |

## Lean runtime unwiring policy

“Disabled” in this pivot means more than hiding a Telegram button. An inactive
domain must not be composed into the normal user-facing application, offered to
a model, initialized on startup, or documented as an available capability.
The implementation and data remain in the repository so that no user history is
destroyed and a later deliberate reintroduction remains possible.

### Active composition in source-centric mode

The normal composition root should instantiate only:

```text
configuration and logging
authorized roots and source lifecycle services
supported extractors and retrieval indexes
source privacy and model gateways
retrieval-answer graph and source-only read tools
Inbox capture/import services
source activity, root health, and local handoff services
Telegram adapter/cards for the active source surface
```

### Unwired composition in source-centric mode

The following implementations remain testable in isolation but are not passed
into `StewardApplication`, Telegram handlers, the agent tool registry, or
normal CLI parser/help construction:

```text
organization/workspace mutation services and approval graphs
knowledge enrichment services and review cards
record, task, reminder, and Calendar services/adapters
research execution/retention services
Drive/Gmail capabilities other than explicit selected-file source import
write-capable action proposal routes
```

Feature-gate checks are defense in depth, not the primary architecture. The
absence of a disabled service from composition means a malformed Telegram
command, stale callback, model tool call, or accidental startup path has no
live dependency with which to perform that action.

### Feature-mode rules

- Introduce one explicit product-mode setting, with `source_centric` as the
  normal mode after the migration. Avoid a collection of undocumented booleans
  whose combinations are hard to reason about.
- Normal CLI help, Telegram `/help`, Home cards, natural-language routing, and
  model tool schemas list only active source-centric capabilities.
- Legacy write commands are not registered in the normal parser. If temporary
  engineering access is necessary, expose it only through a clearly named,
  local developer-only legacy mode; it is off by default and never enabled by
  a Telegram request.
- Disabled external adapters do not make OAuth/API requests, load credentials
  into operational flows, or expose integration controls. Existing credentials
  and configuration files are left untouched.
- Existing pending proposals cannot be accepted in either mode without a fresh
  review. Source-centric mode renders an explanatory historical card rather
  than an executable action.
- Remove inactive dependencies only after the active composition, regression
  suite, documentation, and migration path no longer require them. “Unused” is
  established by runtime and test evidence, not assumed from a hidden button.

## Target architecture

```text
existing local roots                    Telegram
        │                                  │
        ▼                                  ▼
source observer / full reconciliation   Inbox capture
        │                                  │
        └───────────────┬──────────────────┘
                        ▼
              Source Lifecycle Service
      identity · locations · hashes · versions · status
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
     Extraction      Privacy       Activity
     + fragments     policy         history
          │
          ▼
  lexical + semantic retrieval
          │
          ▼
 Telegram/CLI grounded answer
   citations + source cards + reads
          │
          ▼
 optional bounded Codex handoff
```

LangGraph remains useful only for bounded retrieval/tool loops and durable
conversation reference state. It must not become a broad action agent. Normal
Python services retain ownership of scanning, extraction, privacy validation,
source reconciliation, and any local manifest creation.

## Source identity and reconciliation design

The current registry is path-oriented: an external rename or move is observed
as a missing old path plus a new source path. That is safe but loses the
continuity needed for a source-memory product.

The target model keeps a stable `Source` identity while treating filesystem
locations and content hashes as time-varying observations:

```text
Source
  stable source ID
  canonical content identity/history
  source type and extraction health
  privacy policy

SourceLocation
  source ID
  authorized root ID
  normalized path
  first seen / last seen
  current or historical status
  observed content hash

SourceVersion (only when source bytes actually change)
  source ID
  content hash
  observed time
  extraction/index status
```

The migration can retain `sources.path` as a denormalized current-location
convenience field while adding a location-history table. Existing source IDs,
fragments, citations, and Activity must remain valid.

### Reconciliation rules

On a full root scan, Steward compares discovered paths with active locations.

1. Same path, same hash: update last-seen metadata only.
2. Same path, changed hash: create a new source version; rebuild derived
   fragments and indexes.
3. New path plus one missing path with the same hash in the same root: create
   a **reviewable move/rename reconciliation** that preserves the existing
   source ID only after confirmation.
4. Multiple matching paths or hashes: do not merge. Preserve separate physical
   observations and ask the user/Codex handoff to resolve duplicate content.
5. Missing path with no unambiguous matching new path: mark the location
   missing; preserve history and never fabricate a replacement.
6. Restored path: reactivate its existing location/source where unambiguous.

This remains deterministic. Models never decide file identity.

## Root profiles and source authority

A root may optionally have a local, user-approved profile. This is metadata,
not executable instructions.

For `Y4S1`, a profile can record:

```text
purpose: Semester course materials
local guidance documents: AGENTS.md, COURSE_WORKFLOWS.md
Inbox location: UPLOADS/
authority tiers:
  1. official lectures, handouts, announcements, briefs
  2. raw transcripts
  3. cleaned transcripts
  4. personal learning notes and scratchpads
Codex handoff rule: include relevant local guidance documents
```

Steward may use this profile for source-card labels, retrieval ranking, and
Codex handoff context. It must not independently execute guidance, alter a
course tracker, or treat a personal note as official evidence.

## Telegram product surface

The primary Telegram experience should become:

```text
Home
  Ask Steward
  Search sources
  Browse roots
  Open Inbox
  Sync status
  Prepare Codex handoff
```

Natural language remains supported, but it should route primarily to:

```text
question → retrieve/read sources → grounded reply
attachment or deliberate note → provisional intake → save/discard Inbox
“what changed?” → root/source lifecycle status
“prepare this for Codex” → local handoff preview
```

Source cards need concise name, root-relative path, type, current status,
extraction health, authority tier where configured, last indexed time, and
actions such as **Read**, **Find related**, **Privacy**, **Open folder**
(local-only handoff), and **Add to Codex handoff**.

Inbox is a source state/location, not a forced Workspace. A capture can carry
optional user context such as `likely Y4S1` or `CS4226`, but that context does
not move a file. It helps retrieval and later Codex review.

## Codex handoff design

Steward must not invoke unrestricted Codex actions from Telegram. Instead it
prepares a local, reviewable handoff manifest for a selected Inbox batch or
root scope:

```text
handoff ID
selected source IDs and current paths
root-relative paths and hashes
file types / extraction health
user-supplied routing context
retrieved similar sources, if requested
applicable root guidance document paths
suggested questions for Codex
```

The manifest contains source metadata and user-approved excerpts only. It does
not silently transmit source text to an external provider. The user opens the
local manifest or explicitly provides it to Codex. After Codex completes a
move/rename batch, the user runs a root reconciliation scan and reviews any
identity-preservation proposals.

## Delivery phases

### Phase P0 — Freeze broad action surfaces and establish a baseline

**Outcome:** Source-centric mode is explicit and no dormant action feature can
run accidentally.

- Add a `source_centric` product-mode configuration, enabled by default for new
  installations after the migration.
- Gate Telegram commands, natural routing, buttons, and agent tools for
  Workspaces, organization writes, Knowledge, Records, Tasks, Calendar, and
  research retention.
- Preserve read-only historical inspection where safe; make disabled responses
  clear and non-alarming.
- Mark existing pending write proposals disabled/superseded rather than leaving
  them executable.
- Update `/help`, `/home`, status, README, architecture, developer guide, and
  manual checklist to describe the new centre of gravity.

**Tests:** disabled callback/command cannot mutate state; existing source
retrieval remains available; no old pending approval can bypass a feature gate;
model tool schemas omit disabled tools.

### Phase P1 — Reliable all-format source synchronization

**Outcome:** Steward is trustworthy after normal Codex edits.

**Status (23 September 2026):** in progress. The watcher now debounces and
refreshes every currently supported source type using content hashes; root scans
remain the authoritative fallback. Successful root scans record a local last
scan timestamp for CLI/Telegram visibility. Extraction-failure reporting and
move reconciliation are still outstanding.

- Replace Markdown-only watch handling with a generic supported-source event
  queue and debounced refresh path.
- Add scheduled/explicit full-root reconciliation as the authoritative fallback
  for missed file events, network drives, and large batch changes.
- Distinguish metadata-only touches from content changes by hash.
- Show root health, last scan, pending reconciliation candidates, extraction
  failures, and unsupported-file counts in Telegram/CLI.

**Tests:** edit, write-in-place, delete, restore, missed watcher event,
duplicate event, interrupted scan, locked SQLite database, and root outage.

### Phase P2 — Stable source identity and external move reconciliation

**Outcome:** A Codex-organized rename/move does not silently lose history.

**Status (23 September 2026):** started. Root scans create reviewable pending
proposals only for a one-to-one same-root content-hash match. Local CLI review
can preserve the old source ID at the new path; duplicate-content ambiguity is
left unresolved. Telegram review cards, location history, cross-root refusal,
and rollback/restart coverage remain outstanding.

- Migrate from current-path-only semantics to location history/version records.
- Produce reviewable one-to-one hash-based move/rename candidates.
- Preserve source ID, citations, privacy rules, and historical Activity only
  after the user confirms an unambiguous candidate.
- Surface duplicate-content ambiguity rather than merging it.
- Keep root relocation separate from individual move reconciliation.

**Tests:** single rename, cross-directory move, identical-copy ambiguity,
content edit plus rename, deletion, restoration, approval/rejection, restart
while pending, cross-root refusal, and rollback after a failed update.

### Phase P3 — Extraction coverage and quality

**Outcome:** Existing personal folders are useful without format workarounds.

- Add PPTX extraction with slide-number provenance.
- Add source-code extraction with language/path provenance and conservative
  chunking; do not treat generated dependencies as user knowledge by default.
- Add XLSX extraction with sheet/cell-range provenance, then notebook support
  if actual usage justifies it.
- Improve OCR and parser recovery cards; retain the original even when derived
  extraction fails.
- Let roots configure exclusions for generated output, build folders, archives,
  and large binary artifacts.

**Tests:** representative good, blank, corrupt, scanned, oversized, and
unsupported fixtures; source/page/slide/sheet citation accuracy; idempotent
re-extraction.

### Phase P4 — Retrieval-first Telegram experience

**Outcome:** Telegram is the fastest way to recover information from a known
or vaguely remembered source.

- Simplify Home/help to source search, Inbox, root status, and handoff.
- Improve exact-name, vague-semantic, path, root, file-type, date, and
authority-tier filters.
- Make answer cards state whether they used local saved sources, no evidence,
or selected external material.
- Preserve source and fragment references across replies/restarts so “open that
PDF” and “read the next section” are reliable.
- Add retrieval evaluation cases based on real, non-sensitive Y4S1 questions.

**Tests:** lexical/semantic/hybrid retrieval quality, citations, empty results,
privacy denial, reference resolution, pagination, restart, and provider error
fallback.

### Phase P5 — Inbox and bounded Codex handoff

**Outcome:** New information can enter immediately without forcing Steward to
guess a destination.

- Associate each Inbox capture with an optional intended root, not a required
  Workspace.
- Support saved files, notes, links, images/OCR, and selected Drive/Gmail
  imports in one source-oriented Inbox list.
- Add source context fields: user description, intended root/course/project,
  capture origin, and extraction status.
- Build a local reviewable Codex handoff manifest with selected source IDs,
  metadata, guidance paths, and optional user-approved excerpts.
- After a Codex batch, guide the user through reconciliation and any pending
  rename/move matches.

**Tests:** capture/discard/idempotency, inbox pagination, root selection,
provider privacy, handoff manifest omission of unapproved content, unsupported
attachment, reconciliation after an external move, and Telegram restart.

### Phase P6 — Root profiles and Y4S1 acceptance

**Outcome:** Steward supports the real course-folder workflow without taking
over its file-management policy.

- Add a user-reviewed `Y4S1` root profile with guidance paths and authority
  tiers.
- Scan the folder in place; ensure supported files are searchable and PPTX/code
  coverage works once Phase P3 is complete.
- Validate representative questions against official material, cleaned
  transcripts, notes, progress trackers, and the 14-day tracker.
- Capture several new files through Telegram into the intended Y4S1 Inbox;
  create a Codex handoff; perform a disposable move/rename batch; reconcile.

**Tests:** all acceptance interactions are run against a non-sensitive copied
fixture first. The real Y4S1 root is only scanned/read after backup and explicit
user approval; no automated move or tracker edit runs against it.

## Migration and compatibility rules

- Database migrations are additive and reversible where practical; do not drop
  source, workspace, knowledge, record, Calendar, or Activity tables during
  the pivot.
- Existing user files are never moved, renamed, or rewritten by migration.
- Existing embeddings/fragments remain rebuildable derived data; originals and
  source history remain canonical.
- Existing configurations and OAuth tokens remain untouched. Disabled
  integrations are not called and their credentials are not displayed.
- Existing Workspaces and other domain objects remain readable behind explicit
  legacy inspection, but are omitted from normal Telegram navigation and model
  tools.
- Each phase is a separate cohesive commit with focused tests, full regression
  testing, documentation updates, and a manual Telegram acceptance record.

## Documentation migration

Documentation is part of the product surface. A source-centric runtime paired
with a README that advertises Calendar writes or automatic knowledge workflows
would be misleading and could cause users to expect unavailable behavior.
Documentation updates are therefore a required part of Phase P0, not cleanup
for later.

| Document | Required pivot change | Historical material to retain |
| --- | --- | --- |
| `README.md` | Rewrite the opening, feature list, quick start, Telegram examples, CLI examples, privacy explanation, and architecture summary around roots, scans, Inbox, retrieval, and Codex handoff. Remove active how-to sections for Calendar, records, workspace organization, knowledge enrichment, and research. | Keep a short “retained legacy capabilities” note; do not present them as enabled. |
| `docs/product.md` | Replace the broad knowledge-and-action mission with the source-memory mission, core loop, user stories, non-goals, and Codex boundary. | Preserve the original principles of local ownership, provenance, and human control. |
| `docs/architecture.md` | Redraw the active architecture around source lifecycle, extraction, retrieval, Telegram, Inbox, and local handoff. Mark action-domain modules as retained but unwired. | Preserve the decision that LangGraph orchestrates bounded stateful flows rather than domain logic. |
| `docs/invariants.md` | Add source-location history, deterministic reconciliation, no autonomous filesystem action, model-boundary, and Inbox invariants. Remove assumptions that active knowledge/record actions are required. | Keep canonical-original, SQLite metadata, provenance, and restricted-filesystem invariants. |
| `docs/developer-guide.md` | Split into **active product** and **retained legacy modules**. Document composition, source reconciliation, extractor contracts, retrieval, privacy, Telegram references, and handoff. Clearly label historical graphs/workflows. | Retain implementation explanations as a learning/reference appendix rather than deleting them. |
| `docs/telegram-primary-interface-plan.md` | Keep it as an historical Telegram roadmap, but point all new work to this pivot and mark inactive action goals as deferred. | Existing acceptance and design rationale remain useful history. |
| `docs/telegram-manual-test-checklist.md` | Replace active acceptance sections with root onboarding, all-format sync, move reconciliation, Inbox, retrieval/citations, privacy, and handoff tests. Move Calendar/record/workspace tests to a legacy appendix. | Retain completed live-test evidence; do not rewrite history. |
| `docs/testing.md` | Keep chronological results intact. Add a dated pivot baseline, active acceptance status, and separate legacy coverage label so old successful tests are not mistaken for current enabled behavior. | All past test results and known defects. |
| `docs/windows-operations.md` | Focus health, scheduled scanning/watching, Telegram startup, root recovery, backups, and safe reconciliation. Remove active operational instructions for disabled integrations. | Existing recovery rehearsal evidence. |
| `.env.example` | Make root, Inbox, Telegram, selected model provider, and privacy settings primary. Move Calendar/research/legacy integration variables to a clearly marked disabled legacy section or remove them only after code removal. | Secret-handling comments and local data-directory guidance. |
| `docs/adr/` | Do not rewrite existing ADRs: they record historical decisions. Add a new ADR documenting the source-centric active surface and retained-but-unwired domain policy. | ADR-001 through ADR-005 unchanged. |
| `docs/resume-interview-guide.md` | Update only with the user's separate approval because it is an intentionally uncommitted personal artifact. Reframe claims toward source-memory/retrieval and explain retained legacy work accurately. | Detailed historical implementation notes remain interview value. |

Documentation acceptance criteria:

1. A new user reading README, product, architecture, and `.env.example` sees
   the same enabled product surface that Telegram and CLI present.
2. No enabled-path documentation instructs a user to create a Calendar event,
   accept a record/workspace/knowledge proposal, or invoke a broad action agent.
3. Historical tests, ADRs, and developer learning material are retained with
   an explicit historical/legacy label rather than silently deleted.
4. Every active command and Telegram interaction in documentation is exercised
   in a source-centric acceptance run before release.

## Definition of success

The pivot is complete when a user can:

1. Add an existing folder such as `Y4S1` without changing its structure.
2. See a transparent inventory of indexed, unsupported, missing, and
   extraction-failed files.
3. Ask Telegram a vague question and receive a grounded answer with source
   location and fragment/page provenance.
4. Send a new document or note to Telegram, save it to Inbox, and retrieve it
   immediately without deciding its folder first.
5. Let Codex rename or reorganize a selected local batch, then reconcile
   Steward without losing source identity when the match is unambiguous and
   user-approved.
6. Understand exactly what data was used for an answer and what, if anything,
   was sent to a configured model provider.
7. Confirm that disabled action domains cannot make a filesystem, Calendar, or
   other external mutation.

## Deferred reconsideration

Workspaces, knowledge, records, tasks, Calendar, research, and richer routing
may return later only if they strengthen the source-memory loop. A future
proposal must answer:

```text
Does this improve source capture, provenance, retrieval, reconciliation, or
bounded handoff more than it increases action complexity and trust risk?
```

If not, it remains outside Steward's active product surface.
