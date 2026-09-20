# Telegram as Steward's Primary Interface

## Goal

Make every user-facing Steward capability currently available through the CLI
available through Telegram, while preserving the same privacy, provenance,
approval, idempotency, and audit guarantees.

Telegram becomes the primary interface for normal use. It must not become an
unrestricted remote shell: operational maintenance, OAuth, large downloads,
and consequential actions retain explicit local or human-approval boundaries.

## Product principles

- A Telegram feature may be a command, natural-language request, button flow,
  uploaded-file workflow, or a local-browser handoff. It does not need to be a
  one-to-one text copy of a CLI command.
- Telegram remains a transport adapter. Domain services and LangGraph graphs
  continue to own application behavior.
- Original sources remain authoritative. Telegram may capture an original but
  must not silently rewrite it.
- Models propose; deterministic services validate and execute. A model never
  receives filesystem, shell, SQLite, OAuth-token, or unrestricted network
  access.
- Every write remains auditable. Sensitive and destructive operations require
  explicit confirmation and must be idempotent where practical.
- Source privacy rules are checked before information is sent to an external
  model or integration.

## Architecture and infrastructure direction

Keep Steward as one understandable local Python application. The target is not
a fleet of remote services:

```text
Telegram / CLI / local UI
        ↓
application use cases
        ↓
LangGraph only for stateful routing, model/tool loops, and human pause/resume
        ↓
ordinary domain services
        ↓
filesystem + SQLite + selected model/API adapters
```

The following responsibilities remain separate:

- Telegram normalizes updates and renders responses; it does not own domain
  logic or execute filesystem/API operations itself.
- LangGraph owns shared workflow state, conditional routing, tool loops, and
  durable human approval interruptions. It does not become the domain model.
- Services own capture, extraction, retrieval, organization, record,
  Calendar, privacy, and Activity behavior, and remain callable without a
  graph or Telegram.
- The filesystem remains the human-readable home for originals; SQLite holds
  operational metadata, semantic links, proposals, indexes, activity, and
  graph checkpoints.

### Source-root model

Evolve from implicitly scanned absolute paths to explicit approved roots:

```text
SourceRoot

id
name
path
enabled
exclusions
scan/watch settings
allowed operations
default workspace hints
created_at
```

Examples:

```text
School Notes       → C:\Users\clare\Documents\School Notes
Projects           → C:\Users\clare\Documents\Projects
Personal Archive   → D:\Archive\Records
Shared Inbox       → <Steward Inbox path>
```

A root is a physical-location and permission boundary, not a workspace. One
source keeps one physical path but can link to many workspaces and concepts.
Root selection/authorization remains a local action; Telegram can display root
status and request a local setup handoff, but cannot browse arbitrary paths.

### Local runtime requirements

For a single-user Windows installation, retain the current local runtime:

```text
one persistent Steward process
├── Python virtual environment
├── Telegram long polling
├── .steward/steward.db
├── .steward/checkpoints.db
├── .steward/logs/
├── local shared Inbox
└── optional Ollama and authenticated external adapters
```

Before relying on Steward daily, add:

- start-at-login or Windows Task Scheduler/service-wrapper setup;
- restart-on-failure behavior and a local/Telegram health status;
- log rotation and safe diagnostics that exclude source text and credentials;
- backup/restore instructions for **both** originals/roots and `.steward/`;
- periodic SQLite backup or snapshot strategy;
- root-specific exclusion rules, especially the Steward repository, test
  fixtures, `.steward/`, caches, and generated output;
- explicit handling of moved/missing roots and unavailable network drives;
- tests for SQLite locks, interrupted capture, interrupted approval, and
  restart recovery.

Do not add microservices, a cloud database, Redis, Kubernetes, or a separate
queue until a real single-process limitation demonstrates the need.

### Telegram update delivery strategy

Continue using **long polling** for the current local-first, single-user
deployment. It is the best fit now because the local machine makes an outbound
HTTPS request to Telegram; it does not need a public IP address, inbound port,
TLS certificate, reverse proxy, or a hosted server.

Long polling is not busy polling. The request waits at Telegram for an update
for a configured timeout, then immediately opens the next wait. Telegram's
`getUpdates` and webhook delivery are mutually exclusive, and Telegram stores
unreceived updates for only up to 24 hours. Steward's local delivery table,
deduplication, retry/backoff, and dead-letter records therefore remain
necessary under either transport.

Use webhooks later only when Steward has a stable, always-on HTTPS endpoint or
is deliberately deployed to a server and needs lower latency/greater concurrent
throughput. A webhook then requires a public HTTPS URL, webhook secret-token
validation, firewall/reverse-proxy operation, monitoring, and the same
idempotent update handling. It is not automatically more reliable for a laptop
that may sleep or be offline.

Consider a local Telegram Bot API server only if default cloud Bot API file
limits become a demonstrated blocker. It adds its own server to operate and is
not required for normal document intake.

## Current Telegram baseline

Currently Telegram supports:

- grounded questions and follow-up conversation state;
- text capture through `/save <text>`;
- document/photo provisional intake by default: an attachment is staged locally
  until `Save to Inbox` or `Do not keep`; `/save` as its caption remains the
  explicit immediate-capture shortcut. Replying with bare `/save` to a staged
  attachment also accepts that exact pending item without downloading it again;
- provisional capture of substantial text notes and explicit note/thought
  prefixes, plus short record-like material such as hotel confirmations,
  tickets, passports, appointments, contracts, and certificates; all requiring
  a save/discard decision. A record candidate is not a claimed record type:
  after saving, Steward proposes only an implemented, evidence-backed record
  review when extraction supports it;
- `/intake_context ID ...` records additional user guidance and revises a
  pending intake proposal without saving or moving the original;
- `/help`, `/status`, Inbox/source/workspace/activity inspection, lexical
  search, and safe workspace-creation proposals;
- durable, chat-scoped pagination callbacks for source and Inbox lists;
- selected durable proposal-review replies;
- compact source, record, task, workspace, and root cards, with explicit
  `open/show that …` follow-ups for the last opened source, record, task, or
  workspace after a local process restart;
- delivery deduplication, retries, backoff, history, and dead-letter
  inspection through the CLI.

Current observed limitations to address:

- the CLI has many capabilities that Telegram cannot yet initiate or inspect.

## Existing-vault onboarding

Steward can start from an existing human-maintained folder rather than requiring
a new folder hierarchy. For example:

```powershell
steward scan "C:\Users\clare\Documents\School Notes"
```

Scanning registers supported files at their existing paths, hashes and extracts
them, and builds derived indexes. It does **not** copy, move, rename, or rewrite
the original notes. SQLite stores Steward's operational metadata separately in
`.steward/`.

Telegram should eventually make this discoverable as a locally confirmed setup
flow:

```text
Choose an existing local folder in the local setup UI
→ display its file count and supported types
→ confirm the allowed root
→ scan in place
→ report results in Telegram
```

The folder picker and root authorization stay local because Telegram must not
gain arbitrary filesystem access. The scanner must exclude the Steward
repository, test fixtures, `.steward/`, and other configured exclusions unless
the user deliberately chooses them.

## Capability catalogue

Before each implementation slice, maintain a command-to-interaction mapping:

| CLI capability group | Telegram interaction | Safety boundary |
| --- | --- | --- |
| `sources`, search, semantic/hybrid search, inspect source | `/search`, `/semantic_search`, `/hybrid_search`, source cards, pagination, natural language | read-only; privacy filter before model use |
| capture, reextract, indexing | attachment capture, `/save`, explicit reprocess action | preserve original; confirm expensive work |
| workspaces and source links | `/workspace`, natural language, proposal cards | creation/linking is audited; writes may require review |
| Inbox review and organization proposals | `/inbox`, proposal cards, evidence buttons | no move until approval |
| concepts, claims, enrichment, connectors | `/knowledge`, `/connect_knowledge`, and evidence comparison cards | derived changes stay pending until review |
| travel, receipts, warranties, references | record cards, field-correction, and travel-reference review flow | fields retain fragment provenance; Telegram reviews stay bound to their initiating chat |
| Calendar read and travel-event proposal | scheduling queries, review cards | Google remains authoritative; writes require approval |
| research and retention | research result cards and retain buttons | external evidence is ephemeral until retained |
| Drive/Gmail search and import | result selection and import buttons | OAuth occurs locally; import is explicit |
| privacy controls | `/privacy` and source settings cards | admin/owner only; explain external-model impact |
| delivery state and dead letters | `/status` and admin diagnostics | metadata only; retry/replay requires confirmation |
| watcher, UI, model download, evaluation | status/local-link/explicit admin handoff | never silently start a service or download |

## Implementation plan

### 1. Telegram interaction foundation

Create reusable transport-level components instead of adding unrelated handler
logic per feature:

- command registry and `/help` output;
- `/status` summary of configured model, database, Telegram delivery health,
  and pending reviews without exposing secrets;
- pagination for long result sets;
- source, record, workspace, and proposal cards;
- callback buttons for `Show evidence`, `Accept`, `Reject`, `Next`, and
  `Back`;
- callback identifiers that are signed/validated, scoped to a chat and user,
  and safely expire;
- a consistent long-result strategy: concise Telegram summary plus source IDs,
  optional detail actions, and local-UI link where appropriate.

Acceptance criteria:

- `/help` describes available commands and their risk/approval behavior;
- callback updates are idempotent under Telegram redelivery;
- invalid or expired callbacks produce a safe explanatory response;
- domain services remain usable without Telegram.

### 2. Intent and conversational routing

Expand the deterministic-first intent resolver and add LLM assistance only for
genuinely ambiguous requests. Support:

```text
ASK
CAPTURE
SEARCH
INSPECT
ORGANIZE
CREATE_WORKSPACE
LINK_SOURCE
REVIEW_PROPOSAL
REVIEW_ACTION
CALENDAR
RESEARCH
PRIVACY
IMPORT
ADMIN
```

Examples that must work without a trailing question mark:

```text
organize my inbox
what is in my inbox
create a workspace for jobs
show my recent activity
find my CS3210 notes on OpenMP
```

Reference resolution should use conversation state for phrases such as `that
PDF`, `the last source`, and `that flight`. It should ask a clarifying question
when the referent is ambiguous rather than guessing.

Acceptance criteria:

- punctuation alone does not decide whether a normal request is handled;
- deterministic commands and attachments override model classification;
- an unknown request receives useful help or clarification, not a generic
  failure;
- routing tests cover text, caption, reply, duplicate update, and restart.

### 3. Provisional capture and guided routing

Replace the command-first `/save` experience with a provisional intake flow for
attachments and substantial information dumps. A normal question remains an
ordinary conversation; an attachment or candidate fact is analyzed without
becoming durable until the user decides.

```text
incoming attachment or substantial text
→ local/allowed provisional analysis
→ classify: document, thought, task, deadline, record, decision, knowledge,
  reference, question, or uncertain
→ show concise explanation and proposed next steps
→ user saves, asks for more analysis, supplies context, or discards
→ only approved material enters Inbox/Source/Record/Task state
```

Example when context is insufficient:

```text
Steward: This looks like a technical PDF, but I cannot confidently place it.
         It may relate to CS3210 or a new project.

         [Save to Inbox] [Tell me what it relates to] [Show extracted summary]
         [Do not keep]

You: It is for my CS3210 assignment on OpenMP.

Steward: Updated proposal: save to Inbox and link to CS3210 Revision.
         [Accept] [Edit] [Keep in Inbox]
```

Supplementary context is an input to a new or updated proposal, not a hidden
command to change a file. Steward records the rationale, supplied guidance,
supporting evidence, and final human decision. If it remains uncertain, Inbox
is still the correct outcome.

Sensitive documents must offer an explicit analysis boundary before any cloud
model sees extracted text:

```text
[Analyze locally] [Save without model analysis]
[Allow configured external model] [Do not keep]
```

Acceptance criteria:

- `/save` remains a supported explicit shortcut but is no longer required for
  ordinary attachment intake;
- no attachment or text dump is retained permanently without a clear user
  choice, except where the user configured a deliberate auto-capture policy;
- the user can add context and receive an updated routing proposal;
- every proposal revision and final decision is auditable;
- temporary downloaded files are cleaned up when the user chooses not to keep
  them.

### 4. Search, sources, and retrieval controls

Expose source listing, lexical search, semantic search, hybrid search, source
inspection, and re-extraction through Telegram.

Planned interactions:

```text
/search page tables
/search semantic "CPU translation cache"
/source 18
/sources
/propose_reextract 18
/propose_unregister_source 18
```

Search cards show filename, location, heading/page, short excerpt, score where
useful, and `Read more` / `Show evidence` actions. Ensure the production
registry excludes repository fixtures and that test suites always use isolated
temporary data directories.

Acceptance criteria:

- results never disclose a source prohibited by the applicable privacy rule;
- test fixtures cannot enter a production registry;
- expensive reprocessing and unregistering source metadata require confirmation
  and record Activity; unregistering never deletes the original file.

### 5. Workspaces and Inbox organization

Expose workspace listing/creation, source linking, Inbox inspection, emerging
workspace review, organization proposals, and proposal decisions.

Example flow:

```text
You: Organize my inbox

Steward: I found a possible CS3210 Revision workspace.
         Evidence: 4 related Inbox sources.
         [Create proposal] [Show sources] [Keep in Inbox]
```

Proposals must display the source, target workspace/path, rationale, evidence,
and what will physically change. Approval resumes a durable workflow; rejection
is also stored as feedback. Existing user-authored files must not move without
approval.

Acceptance criteria:

- Telegram produces the same pending proposal state as CLI;
- approval remains safe across process restart and duplicate callback delivery;
- all decisions produce Activity events.

### 6. Knowledge and evidence workflows

Expose concept lookup, claim inspection, knowledge connectors, enrichment
proposals, proposal lists, and accept/reject review.

A knowledge-review card shows:

```text
existing claim
new supporting fragment
proposed operation: CONFIRM / EXTEND / REFINE / QUALIFY / CONTRADICT
rationale
[Show source] [Accept] [Reject]
```

Derived synthesis remains non-canonical. Canonical claims and evidence links
change only through the deterministic knowledge service after approval.

### 7. Record workflows

Expose travel, receipt, warranty, and travel-reference capabilities in
Telegram. An uploaded document can create an extraction proposal, followed by
a field-review card:

```text
Flight: SQ638
Departure: Singapore
Arrival: Tokyo
Departure time: ...
[Correct field] [Accept record] [Reject]
```

Every accepted field keeps a pointer to the source fragment/page that supports
it. Corrections must be explicit user inputs and audited.

### 8. Calendar integration

Expose Calendar search, event inspection, travel-event proposals, review, and
event creation.

OAuth authorization remains a local-browser operation. Telegram may initiate
the handoff, display its state, and tell the user where to complete it, but it
must never request client secrets or tokens in chat.

Calendar write flow:

```text
resolve travel record
→ retrieve supporting source
→ query Calendar for possible duplicate
→ create pending proposal
→ Telegram review
→ approved deterministic Calendar write
→ store external event ID and Activity
```

### 9. Research, Drive, and Gmail

Research results are Telegram cards with source links, an ephemeral synthesis,
and explicit `Keep` / `Discard` controls. Retaining material sends it through
the normal Source and organization pipeline.

Drive and Gmail search/import are selection workflows. OAuth remains local;
imports are always explicit. Telegram must never offer a broad background sync
without a separate deliberate product decision.

### 10. Privacy and operational controls

Expose source privacy inspection and changes to the authorized owner. Before a
cloud-model action on a sensitive source, show the model destination and ask
for explicit confirmation when the policy requires it.

Operational commands include delivery inspection, dead-letter inspection,
watcher/UI status, reindexing, embedding-model download, and evaluations.
These are owner-only administrative interactions. Commands that start a local
service, download a model, retry a failed delivery, or consume substantial
resources require confirmation and clear status feedback.

### 11. Reliability, observability, and evaluation

For every Telegram capability, test:

- normal success;
- invalid input and expired callback;
- duplicate Telegram update;
- process stop/restart while a review is pending;
- provider failure, OAuth failure, SQLite lock, missing source, and corrupted
  derived index where relevant;
- privacy enforcement;
- audit event creation;
- equivalence with the underlying CLI/service behavior.

Extend structured traces to include safe identifiers for incoming Telegram
event, route, proposal, tool, policy decision, callback, and final outcome.
Never log full sensitive source text or credentials.

## Prioritized delivery goals

The plan is deliberately divided into goals with independently useful outcomes.
Do not start a later goal merely because it is interesting; complete its stated
verification gate first. Every goal preserves `docs/invariants.md`, keeps
domain logic outside Telegram, and includes a diff/learning review before the
next goal begins.

### Goal 0 — Restore production-source integrity

**Why first:** Telegram retrieval has already surfaced repository test fixtures
as personal knowledge. No assistant behavior is trustworthy until production
and test state are separated.

Implement:

- remove fixture registrations from the current production registry without
  deleting fixture files;
- make test database and data-root selection explicit and isolated;
- prevent configured source roots from including the Steward repository, test
  fixtures, `.steward/`, caches, and generated output by default;
- add a safe inspection/pruning path for accidentally registered sources.

Done when:

- a real Telegram answer cannot cite test fixtures;
- test execution cannot mutate the production `.steward/` database;
- existing user vault sources remain intact and searchable;
- regression tests cover source-root exclusion and cleanup behavior.

### Goal 1 — Telegram interaction and routing foundation

**User outcome:** Telegram becomes understandable instead of returning “I do
not yet know how to safely handle that request.”

Implement:

- `/help` and `/status` with owner-safe, secret-free information;
- routing for ordinary questions without requiring `?`;
- deterministic command routing before any model classification;
- basic Inbox, source, workspace, activity, and search read interactions;
- reusable result formatting, pagination, callback identifiers, and callback
  validation.

Done when:

```text
what is in my inbox
organize my inbox
create a workspace for jobs
find my CS3210 notes on OpenMP
show my recent activity
```

all receive a useful routed response or a safe clarification. Tests cover text,
caption, reply, duplicate update, expired callback, unauthorized chat, and
restart.

### Goal 2 — Provisional Telegram intake and guided routing

**User outcome:** You can send a document, image, link, or substantial thought
without remembering `/save`; Steward analyzes it provisionally and you decide
what becomes durable.

Implement:

- attachment and substantial-text intake classification;
- temporary download/extraction lifecycle and cleanup on discard;
- `Save`, `Ask first`, `Add context`, `Keep in Inbox`, and `Do not keep`
  interactions;
- local-only/external-model/no-model analysis choices for sensitive material;
- proposal revision after the user provides context, such as course, project,
  purpose, or privacy preference;
- Activity events for intake, proposal revision, acceptance, rejection, and
  discard.

Done when a user can send a CS3210 PDF, résumé, flight detail, task-like text,
or sudden thought and see a correct, reviewable next-step proposal. Tests cover
accepted, rejected, uncertain, private, malformed, oversized, duplicate, and
restart-during-review paths.

### Goal 3 — Workspaces and organization review in Telegram

**User outcome:** Material can be safely connected to ongoing work without
automatic incorrect moves.

Implement:

- workspace listing/creation proposal, source linking, and Inbox inspection;
- organization proposal cards with evidence, target root/path, and rationale;
- durable accept/reject/edit/context-supplement flows;
- explicit handling for “new workspace likely” versus “leave in Inbox.”

Done when `Organize my inbox` produces the same safe pending proposal state as
the CLI, survives process restart, and logs every decision. A user must be able
to understand exactly what file movement would occur before approving it.

### Goal 4 — Telegram read tools and safe agent access

**User outcome:** Telegram can use the same information capabilities as
`steward agent` without exposing unsafe powers.

Implement:

- model/provider-neutral Telegram path to the existing tool-agent graph;
- read tools for sources, knowledge, records, workspaces, activity, and
  Calendar reads where configured;
- tool-call traces, budgets, validation, provider-failure responses, and source
  privacy filtering;
- safe proposal-only tools for workspace, knowledge, and Calendar actions.

Done when a Telegram request can demonstrably follow:

```text
message → model → allowlisted local tool → validated result → model → reply
```

with no raw filesystem, shell, token, or unrestricted external-tool access.
Test each model adapter, invalid arguments, tool loops, privacy denial, and
tool-policy denial.

### Goal 5 — Tasks, records, deadlines, and Calendar proposals

**User outcome:** Steward can turn natural commitments and documents into
reviewable life/work objects rather than losing them in chat.

Implement:

- a deliberately small `Task`/reminder domain before treating every commitment
  as a Calendar event;
- travel, receipt, warranty, task, deadline, and decision extraction proposals;
- Telegram field review and correction;
- Calendar search, duplicate detection, event proposal, approval, and write;
- reminders/Calendar actions only after explicit user review.

Done when “remind me to compare OpenMP scheduling before Tuesday” creates a
reviewable task/reminder proposal, and an itinerary can produce a reviewed
TravelRecord plus non-duplicating Calendar proposal. Test field provenance,
time zones, duplicate events, OAuth failure, rejection, and restart.

### Goal 6 — Curated knowledge conversations and research retention

**User outcome:** You can learn through discussion, correct or extend your
understanding, and deliberately preserve the best curated result.

Implement:

- conversation-to-curated-note proposal;
- local evidence, external research, and model reasoning kept distinguishable;
- claim/evidence comparison and enrichment review cards;
- retained research sources entering normal capture/organization flows;
- explicit editing before a generated note becomes a durable source.
- a reply-selected discussion can be synthesized with the explicitly chosen
  local or external model, but its draft remains a reviewable note proposal.

Done when a discussion about TLBs can produce a user-reviewed note with
traceable local/external evidence, and external research is discarded unless
you choose to retain it. Test unsupported claims, conflicting sources, privacy
rules, retain/discard, and provenance links.

### Goal 7 — Multi-root vault management and operational reliability

**User outcome:** Steward can safely operate over existing folders across the
machine and remain dependable as a daily service.

Implement:

- `SourceRoot` persistence, local root authorization, exclusions, scan status,
  root health, and root/workspace distinction;
- Telegram-visible, paginated root status and a local handoff for root
  selection; root cards expose health only, never filesystem paths or remote
  root-management actions;
- file watching/debouncing where justified, with hashes authoritative;
- Windows start-at-login/service guidance, health reporting, log rotation,
  backup/restore, and SQLite snapshot procedures;
- fault-injection tests for missing/moved roots, process kill, SQLite lock,
  provider outage, corrupt derived index, and Telegram outage.

Done when multiple existing folders can be registered and scanned in place,
their sources remain distinct and searchable, excluded directories cannot leak
into production retrieval, and recovery procedures are documented and tested.

### Goal 8 — Explicit external imports and administration

**User outcome:** Drive/Gmail imports, privacy controls, operational status,
and maintenance tasks are available from Telegram without giving the bot broad
machine authority.

Implement:

- explicit Drive/Gmail search-result selection and import;
- local OAuth handoff and status reporting;
- source privacy inspection/change flows;
- delivery history/dead-letter inspection and confirmed retry;
- admin-only reindex, model download, watcher/UI status, and evaluation
  controls.

Done when each action is authorized by chat/user, explains its effect, emits
Activity, and has tests for OAuth/configuration failure, unauthorized access,
duplicate import, and confirmation refusal.

Dead-letter recovery must not fabricate a replay. Steward retains delivery
metadata, not Telegram message bodies, so a future confirmed recovery action
can only reopen an update for a genuine Telegram redelivery while recording an
audit event and a new retry budget. It cannot rerun an unavailable original
payload locally.

The local `telegram-recover-dead-letter UPDATE_ID --confirm` command and the
Telegram `/recover_dead_letter UPDATE_ID` review card implement this boundary.
They reset only a retry budget, record Activity, and cannot replay an
unavailable message.

### Goal 9 — End-to-end evaluation and daily-use hardening

**User outcome:** Steward is dependable enough to use as the main personal
interface, rather than merely a collection of working demos.

Implement:

- end-to-end Telegram evaluation cases for every completed goal;
- retrieval, extraction, routing, proposal, tool, Calendar, and privacy
  evaluation sets using realistic but non-sensitive fixtures;
- a manual test checklist for your real vault and Telegram bot;
- measurement of failed routing, false organization confidence, tool-loop
  exhaustion, extraction mistakes, and user rejection reasons;
- prioritized fixes from actual daily use.

Done when every Telegram feature has normal, failure, restart, duplicate, and
privacy test coverage proportionate to its risk, and the product can be used
for a sustained trial without unexplained loss or unsafe mutation.

## Program-wide test matrix

Every implementation goal contributes to this matrix:

| Concern | Required evidence |
| --- | --- |
| Domain behavior | unit tests of the underlying service without Telegram or LangGraph |
| Transport behavior | normalized update, command/caption/reply parsing, response rendering |
| Persistence | SQLite state is correct before/after restart and duplicate delivery |
| Safety | privacy, root boundary, authorization, policy, confirmation, and audit tests |
| Model behavior | deterministic fake-adapter tests plus provider integration tests when authorized |
| External APIs | adapter contract tests, failure handling, and a separately authorized live smoke test |
| User experience | manual Telegram checklist with expected messages/cards and recovery paths |

## Delivery order

## Delivery status and next priorities

Knowledge contradictions now have a complete non-destructive decision path.
The evidence comparison is first flagged or rejected; a flagged conflict remains
visibly unresolved until the user keeps the current claim, marks it disputed, or
marks it as needing revision. The durable resolution and audit event never rewrite
claim text or discard either side's provenance. Concept and evidence-review cards
show the outcome. A `needs_revision` outcome now opens a durable ordinary-text
prompt, creates a separate generic action proposal containing the exact user
wording, and applies an approved replacement atomically with evidence and
`claim_revisions` lineage. The old claim remains visible as superseded. A
configured model may now make one privacy-gated wording suggestion from the
saved evidence snapshot; it is explicitly labelled as model-generated and uses
the same separate approval card. It cannot write knowledge directly. Automated
migration, rollback, restart, repository, application, navigation, privacy, and
provider-failure tests cover the flow.

Task details now expose the existing local task-to-Calendar relationship without
turning tasks into events. A precise, open, unlinked task offers a separate
**Add to calendar** review action. After an approved marker is linked, reopening
the task displays the relationship and offers **View calendar**, which fetches
current provider state. Tasks without precise deadlines and completed unlinked
tasks do not acquire Calendar actions. `CalendarLinkRepository` centralizes the
local opaque-ID lookup without contacting Google. An opened reviewed deadline
marker can now answer `what task is this for?` with a local linked-task card and
an **Open task** action; ordinary Calendar events explicitly remain unlinked.
After an explicit task card, bounded natural completion phrasing resolves only to
that durable task context and does not alter any linked Calendar marker.
Automated repository, application, Calendar, and composition tests pass; live
Calendar link navigation remains open.

Organization edit-target UX now exposes a paginated existing-workspace picker
and a durable new-workspace naming prompt. Compact numbered choices use local
workspace IDs only inside callback commands; selecting or typing a target rejects
the superseded proposal and displays a replacement proposal without moving the
original. Ambiguous targeted natural guidance preserves the current proposal and
opens the picker rather than selecting a target or discarding the review. Missing
targets leave the proposal pending. The new-workspace input
survives restart and creates neither workspace nor move until the replacement is
explicitly approved. These correction actions are available from both a direct
organization card and the unified `/pending` review card. Focused application/Telegram tests pass; live Telegram
picker layout and correction acceptance remain open.

Telegram replies can now resolve exact previously delivered source, workspace,
task, typed-record, Calendar, concept, knowledge-evidence, and ephemeral
research detail cards instead of relying only on the newest
chat-wide selection. Each single-object card carries a narrow pointer; after
Telegram confirms delivery, migration 47 persists its outbound message-ID mapping
without rendered text or object content. A reply restores that exact object before
deterministic routing, survives restart, and remains chat-scoped. Calendar IDs stay
opaque and are used to refetch current provider state. Storage is bounded to 500
mappings per chat. Failures after Telegram delivery do not cause duplicate replies.
Focused repository, migration, adapter, and application tests pass. Actionable
action/organization/intake/knowledge review cards also persist their exact proposal
reference, while `/pending` navigation cards neither select nor authorize their
first item. Live Telegram reply acceptance remains open; all list cards
intentionally never imply a selected item.

Calendar object context is no longer consumed by review explanation routing.
After opening or replying to an event card, bounded phrases such as `what is
this?`, `when is it?`, `where is it?`, and `show details` refetch the selected
event without entering the model or implying a write. Calendar list, detail,
empty-result, and failure cards now provide the applicable compact Home, Upcoming,
Refresh, Retry, and Integrations next actions. Automated application coverage
passes; real Telegram wording and button-layout acceptance remains open.

Moved-root recovery is now an explicit local `relocate-root NAME PATH --confirm`
operation. It requires the old root to be missing and the replacement to contain
every tracked source at the same relative path with the registered SHA-256 hash.
One SQLite transaction rebinds the root, preserves exclusions/IDs/derived links,
and marks verified sources active; any mismatch or path/authorization collision
rolls back everything. It never moves files and is intentionally absent from
Telegram. Repository and CLI tests cover success, confirmation, rescan identity,
hash/missing mismatch, available roots, and destination collisions. Real moved
OneDrive/mount recovery with all writers stopped remains a manual acceptance gate.

Local operational readiness now has an opt-in `health --strict` exit contract.
It checks expected database tables, enabled-root availability and a nonblank
Telegram token; it permits Inbox-only setups and disabled roots. Schema reads
replace `SELECT 1` so malformed SQLite files are not reported available merely
because a constant expression succeeds. This is not remote-service health or
full database integrity validation. The Windows preflight uses the strict check.

Windows start-at-login now has a concrete operator runbook in
`docs/windows-operations.md`: absolute virtual-environment executable and working
directory, ordinary interactive-user task, bounded restart policy, explicit
enable/disable, readiness caveats and quiet-period maintenance. No scheduled task
has been registered or launched by this work. Machine logon/outage acceptance
remains open; documented commands are not evidence of a deployed service.

Verification checkpoint: all six runbook PowerShell blocks parsed successfully
without execution, and Task Scheduler command signatures were inspected locally.
The full pytest suite passed again after the import cards and durable import
selection changes. This does not establish scheduled-task or live Telegram health.

Drive/Gmail result browsing now requests five metadata results per provider page,
with explicit More results and Restart search actions. Numbered import buttons
select exact external IDs; browsing does not import content. Query/cursor pairs
use the existing durable, chat-scoped callback storage. Automated coverage checks
empty intermediate pages, restart reconstruction, malformed continuations, and
secret-free provider failure/retry. The affected application, Drive/Gmail, CLI and
Telegram suites and the full pytest suite passed on 2026-09-12 for this release.
Live OAuth pagination acceptance remains open; no live account content was read
or imported by these tests.

Additional configured-service integration coverage traverses twelve synthetic
results over three pages for each provider. It rebuilds the application between
clicks, resolves persisted callbacks, asserts one list request per click and exact
ordered result IDs, and verifies Gmail requests metadata only. Both cases passed
in `tests/test_external_search_navigation.py`; OAuth is replaced with a fake API,
so this strengthens wiring verification without claiming live-account acceptance.

Import completion now offers Read content, Source details, and Link workspace
for the exact retained local source. Duplicate imports link to the existing source
without claiming a new copy or an Inbox location. These cards do not automatically
run a model or organize files. New and duplicate result cards have automated
coverage; real-chat presentation and end-to-end follow-up acceptance remain open.

Successful and duplicate imports now persist the selected source in chat-scoped
reference context. Failed imports retain the prior selection; a context-write
failure after capture still returns source buttons and a warning, not an import
failure. Tests cover both providers, new/duplicate results, context reconstruction,
chat isolation, and failures. This is the existing bounded reference resolver,
not arbitrary conversational anaphora resolution.

Reviewed re-extraction failures now preserve pending status and offer explicit
retry/read/dismiss recovery without exposing parser diagnostics. The card warns
about partially updated derived state; atomic parser/index refresh is not claimed.
Recovery cards now distinguish PDF native/OCR requirements, image OCR quality and
languages, genuine DOCX packages, static UTF-8 HTML, email body parts, UTF-8 text,
and unsupported binary conversion. Focused tests verify secret-free failure and
deliberate retry. Live validation with failing parser/OCR dependencies remains open.

Empty extracted-content views now provide reviewed re-extraction navigation and
extractor-specific recovery guidance without claiming the original is empty. No
parser/model runs merely to display the card. Persisted exception-category
diagnosis and live attachment-recovery acceptance remain unfinished.

Representative failure paths are now covered for every configured provider.
SoCLaaS, Ollama, and Gemini adapter failures produce bounded model-gateway errors;
Ollama also rejects valid JSON with an invalid top-level/message shape instead of
leaking `AttributeError`. SoCLaaS local warnings omit raw provider diagnostics.
Calendar tests cover factory/request/projection failures, while Drive/Gmail tests
cover search/import/cursor recovery cards. All use synthetic diagnostics and no
real account. Live provider outage and recovery remain operator rehearsals.

The 2026-09-13 local operations rehearsal exercised temporary root relocation,
CLI backup/restore with a safety copy, restored state in a fresh process, runtime
lock recovery after a killed owner, and a real refused loopback request through
the Ollama adapter. The installation also passed `steward health --strict`. A
read-only Windows check found no registered `Steward Telegram` task, so actual
start-at-login, one-poller, and live external-provider outage acceptance remain
open and are not represented as completed.

The agent tool node now converts handled validation/execution failures into
fixed secret-free error results. Graph tests verify model continuation without
exception diagnostics and preserve intentional LangGraph interrupts. Successful
tool payloads still need their existing per-tool privacy boundaries.

Calendar model-tool failures no longer interpolate provider exceptions. Fixed
errors cover factory, request, and projection failures; wrapper-level tests
verify synthetic tokens, paths, and email diagnostics do not enter tool results.
This closes the identified Calendar boundary leak, not an audit of every provider.

Calendar detail cards now display provider location and description when present,
with labeled display limits. They do not expand the model-facing Calendar tool
payload or persist a stale event mirror. Live Telegram rendering remains pending.

Calendar reads now provide explicit retry/integration navigation and retain the
selected event ID across transient failure. Event cards offer Refresh and
Upcoming. Automated outage/recovery coverage verifies a fresh provider lookup
after reconstruction; actual provider-outage Telegram acceptance remains open.

Restore now refuses recognized operational/checkpoint role swaps before writing
the destination or safety copy. Tests use actual LangGraph saver tables in both
directions. This is not installation identity validation; unreadable destinations
and same-role backup selection still require operator care.

Paired stopped-writer recovery is now covered for an interrupted organization
workflow using real SQLite checkpoints: restore returns both proposal and graph
to pending, reconstructing/inspecting state does not move the original, and only
explicit acceptance resumes the move. Both safety copies preserve the later
rejected state. Actual Telegram callback and operator deployment recovery remain
separate acceptance gaps; this does not make live sequential backups atomic.

CLI backup now explicitly reports partial sets, preserves completed copies, and
requires a new destination for retry. A corrupt second database regression
verifies this behavior. Successful paired copies warn that writers must be
stopped for coordination; paired live-database atomicity is not implemented.

A synthetic backup/restore workflow now passes across a fresh Python process:
a pending enrichment is restored and accepted with one audit event while the
safety copy preserves the later rejection and the original file stays unchanged.
This is one operational-database recovery case, not the full deployment gate.
Paired checkpoint recovery, external-side-effect reconciliation, and real
Telegram restart/restore acceptance remain unverified.

Backup output now uses exclusive reservation rather than only an existence
check. Fault injection verifies a competing destination is preserved, and a
failed SQLite copy removes its own reserved output. Source access is read-only.
These checks do not replace the outstanding end-to-end recovery rehearsal.

Restore now rejects empty, non-SQLite, and truncated snapshots through read-only
preflight before writing a safety copy or destination. Automated tests verify
the active database remains byte-identical. This does not validate backup identity
or replace the outstanding operator-led backup/restore rehearsal.

Runtime coordination now has verified live-subprocess contention coverage:
a confirmed lock owner excludes another process for the same temporary data
directory, an independent directory remains usable, and killing the test owner
releases ownership without deleting the coordination database. Normal and abrupt
exit tests also pass. No actual bot process or user database is used. Telegram
polling restart, deployment startup, and cross-machine operator validation
remain separate, unfinished acceptance requirements.

### Acceptance evidence from 11 September 2026

Long-document provider check: a synthetic 66,500-character Cedar document
was processed in four evidence batches and one combination call using the
configured gateway and normal environment loading. The successful diagnostic
run produced batch notes of 156, 207, 210, and 421 characters with permitted
keys; its 686-character final answer contained the queue, idempotency, and
manual-review facts and cited F1, F2, and F3. No vault content was used.
An earlier live attempt failed a `DocumentSynthesisError` check; its failing
stage was not recorded. Thus one successful run is verified, not consistent
provider reliability. Failed-batch recovery, representative non-repetitive
document evaluation, and real Telegram long-document acceptance remain open.

A live smoke test using the configured model gateway and normal `.env` loading
returned a nonempty summary of a synthetic two-section Cedar-project document.
It cited both supplied keys (`F1`, `F2`), with no unknown citation keys. No vault
content was sent. This verifies one provider request and citation membership;
it does not prove factual entailment, long-document behavior, Telegram rendering,
or the remaining end-to-end acceptance cases.

The user's real Telegram transcript verifies status/root browsing, source-list
pagination, source detail and `show that PDF`, workspace inspection, empty task
and review screens, and Calendar search/detail delivery. It does not demonstrate
restart recovery, approval execution, document-content reading, or a sustained
trial. Do not treat a passing automated suite as proof of those live behaviors.

Follow-up releases implement extracted-section reading with provenance and
navigation, readable Calendar date ranges and upcoming defaults, a useful Home
screen, exact external-ID persistence, active-review approval scoping, detailed
reopened note/correction/task reviews, and complete pending-inbox pagination.
Their automated checks pass; live acceptance of these releases remains pending.

Selected-source summarization and questions now have citation-checked model
paths and durable question prompts. Record previews retain reviewed snapshots;
record approval, evidence validation, persistence, and audit commit together.
Calendar proposals now show event details and bind approval to the reviewed
record/task snapshot, refusing changed values before calling the writer.
Active source cards now offer a paginated workspace picker leading to the existing
explicit link review. Selection alone does not create membership or move files;
already-linked workspaces offer a View action. Live Telegram acceptance remains open.
Source cards also expose a paginated actual-membership view. Exact follow-ups
such as “Which workspace is this in?” resolve the durable selected source and
read SQLite without a model call. Missing context asks for source selection;
semantic links are explicitly distinguished from physical folders.
Knowledge-enrichment review status and audit now commit atomically across CLI
and Telegram. Acceptance rechecks that the supporting fragment belongs to an
active source. Injected audit failure leaves the proposal pending and retryable.
Migration 46 binds new reviews to saved claim text, fragment text/location,
source identity, and content hash. Acceptance compares that snapshot within the
write transaction; changed content requires a fresh proposal with a new ID.
Reopened cards show the saved version. Legacy reviews retain their IDs/status
without invented snapshots; pending legacy reviews cannot be accepted, but can
be rejected and replaced. Outdated/legacy accepted reviews remain in review
history but are excluded from current accepted-review lookup. This protects
version identity, not factual correctness or model interpretation quality.
Stale knowledge acceptance now returns an actionable recovery card: create a
fresh preview, inspect the saved review, or explicitly dismiss the old proposal.
Refreshing does not approve either version or silently remove the older review.
Current accepted-review lookup now reads proposal/evidence/source state through
one joined query and excludes unavailable sources or absent referenced rows.
Regression coverage checks missing/restored sources and legacy orphan rows;
historical approval status is not rewritten by a lookup.
Telegram concept summaries now use current reviews only; accepted history stays
browsable with explicit current/historical labels and revalidation counts.
Availability/version labels are not claims of factual correctness.
Workspace-link review now rechecks the proposal and active objects inside a
SQLite write transaction. Membership, review status, and audit events commit
together. An injected audit failure verifies rollback to a pending proposal
without a link; retry records one link and duplicate approval adds no audit events.

Remaining implementation work includes broader conversational reference
resolution, representative long-document evaluation, and the broader task, knowledge, administration,
and recovery items in the table below. The program is therefore still in
progress for both implementation and live validation; root configuration alone
is no longer a blocker, and manual testing is not the only remaining work.

This is a delivery program, not a claim that every item listed below already
works. Status is deliberately conservative: a goal is only **complete** when
its acceptance criteria and proportionate automated tests are satisfied.

| Goal | Current status | What is available now | Important remaining work |
| --- | --- | --- | --- |
| 0. Production-source integrity | Complete | Production fixture cleanup, isolated tests, explicit source removal | Keep enforcing root exclusions as roots evolve |
| 1. Routing foundation | Substantially complete | Commands (including unknown-command fallback), deterministic natural-language reads and exact source-card privacy routing, callbacks, pagination, path-redacted progressively disclosed Activity cards, secret-free `/status` runtime readiness, and persisted source, workspace, record, task, Calendar, root, Activity, concept, and evidence-review reference contexts | Broader reply/reference-resolution cases |
| 2. Provisional intake | Substantially complete | Attachments and substantial text are staged, classified locally, contextualized, then accepted/discarded; model use defaults to none and can be explicitly selected as local or external before capture; direct and `/pending` review cards expose the same Save, model-boundary, Add context, and Do not keep controls; post-save local evidence can route generic filenames to one unambiguous record review, and accepted record-classified intake creates independent record and organization reviews; restart-persistent staging diagnostics and recovery cards explain each supported extractor category | Live recovery acceptance |
| 3. Organization review | Substantially complete | Inbox review, durable organization decisions, original-source inspection before a decision, reviewed moves that create an idempotent source-to-workspace semantic link, reviewable source-to-workspace links without file movement, paginated existing-workspace correction, safe ambiguous-guidance recovery, persistent user-context rationale on replacement proposals, durable new-workspace naming, reviewed replacement proposals, and explicit keep-Inbox target revision | Live picker acceptance |
| 4. Read tools | Substantially complete | Read-only source/knowledge/record/workspace/activity tool agent, visible per-turn generated-versus-saved-material/Calendar answer origin, filename-only provenance at the Telegram/model boundary, Calendar reads in the agent only when local OAuth is already configured, safe recursion-limit replies, generic secret-free graph-boundary recovery, and synthetic failure matrices for SoCLaaS/Ollama/Gemini/Calendar/Drive/Gmail | Live provider outage and recovery acceptance |
| 5. Tasks, records, Calendar | In progress | Reviewable Tasks with explicit completion, natural task phrasing, offset-aware deadlines, chat-bound explicit Telegram reminders with durable retry, reviewable stale-safe local deadline and reminder changes, chat-bound reviewable idempotent Calendar deadline markers, travel-event writes, and explicit standalone appointment proposals; Calendar cards with task-list navigation and bidirectional task/Calendar card navigation for explicit links, including durable references on linked-task and no-link follow-up cards; travel/receipt/warranty record proposal/review and correction cards with original-source actions and current-field evidence-section navigation, including labelled passenger provenance for travel records, exact-record inspection follow-ups, and bidirectional Travel-record/Calendar navigation only for reviewed Steward-created event links | Richer scheduling semantics and record/Calendar navigation |
| 6. Curated knowledge/research | In progress | Concept/claim enrichment review with explicit non-destructive conflict outcomes, direct original-source inspection, durable reviewed user-authored claim revisions, and privacy-gated model draft suggestions that remain separately reviewable; explicit or reply-selected curated-note proposals; local/external model synthesis of a selected reply; reviewable note edits; and short-lived restart-safe research review cards that retain exact reviewed material only after approval, including an explicit exact-card `keep/save that research` follow-up | Richer synthesis and broader conflict evaluation |
| 7. Multi-root and reliability | In progress | Locally authorized roots, root health, enforced exclusions, root watches, delivery diagnostics, bounded local log rotation, write-once local SQLite snapshot/confirmed restore, corrupt-derived-index recovery, bounded SQLite-busy scan recovery, and disposable moved-root/backup/restore/process-outage rehearsal; the authorized local Windows start-at-login task is registered and has passed an immediate running/strict-health check | Verify a future Windows logon trigger, one scheduled-process Telegram reply, and live Telegram/provider outage recovery |
| 8. Imports and administration | In progress | Explicit Drive/Gmail search/select/import, audited chat-bound source privacy controls, source inspection before source-affecting reviews, delivery inspection/status, and reviewed single-source re-extraction, metadata unregistering, and semantic-index rebuild | Broader confirmed maintenance flows |
| 9. Daily-use hardening | In progress | Unit/integration coverage, safe aggregate `/metrics`, and a Telegram-shaped attachment intake → model-boundary choice → durable organization review → audited move acceptance flow | Real-vault/Telegram checklist, restart/outage evaluation and sustained trial |
| 10. Telegram companion experience | In progress | Durable callbacks; escaped, styled cards; contextual review confirmations; compact buttons; source, record, task, workspace, root, and Calendar browsing; durable explicit follow-ups (including review-to-original and record-to-original provenance), natural reviewable `put this flight in calendar` routing from an exact Travel card, and exact-card task completion; and a read-only tool agent for ordinary read requests | Broaden contextual follow-ups and next-action cards across remaining review/Calendar workflows; validate the full daily-use checklist on Telegram |

### Goal 10 — Telegram companion experience

**User outcome:** Steward feels like a safe personal assistant rather than a
remote CLI. A user can send ordinary language or material, understand what
Steward is proposing, and complete the next safe step without memorising an
internal command or proposal ID.

Implement in these deliberately separate layers:

1. **Presentation.** A Telegram-specific presenter renders escaped HTML,
   concise cards, source-aware citations, consistent visual status markers,
   pagination, and action labels short enough to fit on buttons. Domain
   services continue to return facts and proposals, never Telegram markup.
2. **Unified review inbox.** `/home` and `/pending` present pending intake,
   organization, action, and knowledge decisions as human-readable review
   items. Each card identifies the affected item, proposed effect, rationale,
   evidence where available, risk, and next actions. Opaque callback tokens
   remain chat-scoped; raw domain IDs remain an advanced fallback only.
3. **Contextual decisions.** `what is this?`, `why?`, `yes`, `no`, and a
   correction such as `put it in CS3210` resolve against the active review in
   that chat. Ambiguous language may revise a proposal but never authorizes a
   consequential action without a visible explicit confirmation.
4. **Agent-first routing.** Normal non-command read requests (including
   “show me”, “tell me”, “list”, “summarize”, and “explain”) use the existing
   allowlisted read-only tool loop when configured. Deterministic attachment,
   reply-reference, active-review, and explicit command routes take priority.
   Write-capable requests become deterministic review proposals; the model
   never receives filesystem, shell, token, or unrestricted network access.
5. **Progressive disclosure.** Keep a small command menu (`/home`,
   `/pending`, `/search`, `/calendar`, `/tasks`, `/records`, `/workspaces`, `/help`). Retain the
   wider command surface for recovery and local administration, but surface
   normal actions through cards and buttons.

Done when a user can complete capture → explain → refine → approve, search →
inspect → follow up, and calendar/task proposal flows from Telegram without
looking up an ID or command. Automated coverage must include escaped dynamic
text, short button labels, callback expiry/redelivery, restart during review,
cross-domain proposal IDs, ambiguous follow-ups, provider failure, and proof
that no write occurs before explicit approval.

### Current Goal 10 delivery notes

- An attachment, shared link, or substantial/personal text is first staged as
  a readable review card. It is never saved merely because a classifier
  recognized a flight, deadline, booking, receipt, note, or reference.
- A clearly time-bound commitment such as “I need to submit the report by
  Friday” produces a **Save task** proposal. The due phrase remains visible;
  Steward does not guess an instant, schedule a reminder, or create a Task
  until the user accepts it.
- Accepting a staged travel item preserves its original in Inbox and opens an
  evidence-backed Travel Record review. After that record is accepted, an
  **Add to calendar** action creates only a separate Calendar proposal; the
  Google Calendar write remains explicitly reviewed and duplicate-protected.
- Choosing **Add context** switches the chat into a small, durable input step.
  The next ordinary message supplies course/project/purpose context for that
  same pending item, then Steward redraws its card. This survives a service
  restart because only the opaque pending-review reference is stored. Once
  saved, that explicit context can select one existing workspace for the
  first organization proposal; it never moves the original automatically.
- Every organization card offers **Change workspace** and **New workspace**.
  Existing targets are paginated with compact numbered buttons and can still be
  supplied by name. New-workspace naming is a durable chat-scoped input step.
  Either choice supersedes the prior proposal and shows a new approval card
  before creating a workspace or moving an original.
- Saved records can be opened with `/record travel ID`, `/record receipt ID`,
  or `/record warranty ID`. Each current field identifies its supporting
  fragment only when the current value still occurs there; an explicit
  correction is intentionally shown as not source-evidenced instead of being
  attributed to stale extraction evidence.
- Results that invite another action should offer it directly: source and
  Calendar lists use compact **Open 1**-style buttons; pending decisions use
  **Review 1**-style buttons. Internal IDs remain a recovery interface, not
  the normal user journey.
- Source, record, task, and workspace detail cards now persist a narrow,
  chat-scoped reference. After a local restart, explicit phrases such as
  **open the last source**, **show that flight**, **show that task**, and
  **open that workspace** reopen only the matching card. They do not infer a
  write, broaden a search, reveal a local path, or replace ordinary questions
  with navigation.
- Calendar detail cards use the same chat-scoped mechanism for their opaque
  external event ID. **Show that event** refetches current Calendar state, so
  external Calendar data is never treated as a stale local record.
- A **Linked task** or **No linked task** follow-up card retains the same opaque
  Calendar event reference. Replying to an older card with **show that event**
  therefore reopens that exact current event after unrelated navigation or a
  restart; it never treats a local task relationship as a Calendar write.
- Root cards intentionally stop at health and exclusion-count information:
  root selection, enablement, and scans remain local-only operations. This
  preserves the multi-root boundary even while Telegram makes its state
  discoverable. An exact **show/open that root** follow-up may reopen the same
  health-only card after a restart, but does not add any Telegram root mutation
  or filesystem capability.

### Priority sequence from here

1. **Finish root boundaries and recovery before adding more autonomous intake.**
   A local-first system is only trustworthy when every scan is confined to an
   approved root and unavailable/moved roots recover predictably.
2. **Build a small task/deadline workflow.** This closes the highest-value gap
   in the "send Steward anything" experience without conflating tasks with
   Calendar events.
3. **Complete record review.** Add correction and provenance views before
   adding new record types, then add receipt/warranty proposals.
4. **Make curated knowledge and research deliberate.** A conversation or web
   result becomes durable only through a visible proposed note/source and an
   accept/discard decision.
5. **Complete selected imports and administration.** OAuth remains local;
   Telegram chooses individual imports but never grants broad background sync.
6. **Run the end-to-end hardening program.** Test actual polling, restart,
   duplicate deliveries, SQLite contention, provider failure and privacy on a
   non-sensitive test vault before daily reliance.

Goals run in this order:

```text
0 integrity
→ 1 routing foundation
→ 2 provisional intake
→ 3 organization
→ 4 tool agent
→ 5 tasks/records/calendar
→ 6 curated knowledge/research
→ 7 roots/reliability
→ 8 imports/administration
→ 9 hardening
```

### Active execution goals

The numbered product goals describe capability areas. The following delivery
goals turn their remaining work into small, independently testable releases.
They are ordered by the risk of getting them wrong and by their value in daily
Telegram use. A goal is only checked off after its automated tests, relevant
restart/duplicate cases, and manual Telegram checklist cases pass.

1. **Operational trust and root recovery (Goal 7).** Rehearse recovery from a
   missing or moved root, an interrupted scan, a SQLite lock, a provider
   outage, and a Telegram-delivery outage. Add a documented Windows
   start-at-login/health procedure. The test boundary is that roots stay
   explicit, source content is never lost, derived state can be rebuilt, and
   an operator receives a useful, secret-free diagnosis.
2. **Conversation-aware Telegram routing (Goals 1 and 4).** Complete
   reply/reference-resolution cases and provider-failure behavior for the
   allowlisted read-tool loop. The test boundary is text, caption, reply,
   duplicate update, expired callback, unauthorised chat, invalid tool
   arguments, tool-budget exhaustion, and restart all produce either a useful
   answer or a safe explanation.
3. **Intake and organization refinement (Goals 2 and 3).** Add an explicit
   local-only/external/no-model analysis choice, extractor-specific review
   feedback, and editable organization targets. The test boundary is that
   accept, discard, revise, uncertain-Inbox, existing-workspace, and
   new-workspace paths preserve originals, remain idempotent, and survive a
   restart while awaiting a decision.
4. **Daily commitments and records (Goal 5).** Add reminder scheduling and
   richer task/Calendar linkage without treating every task as an event. Test
   time zones, duplicate prevention, approval/rejection, OAuth failure,
   Calendar outage, provenance, and restart before enabling any action.
5. **Curated knowledge with evidence (Goal 6).** Add conversation synthesis,
   selected external-source retention, and a conflict-review experience. Test
   that local evidence, external evidence, and model inference remain visibly
   distinct; no research result becomes durable without an explicit decision.
6. **Administration and selected imports (Goal 8).** Add local OAuth status,
   confirmed maintenance operations, and complete the select-before-import
   Drive/Gmail flows. Test authorization, configuration failure, cancellation,
   duplicate import, audit history, and refusal of unconfirmed maintenance.
7. **Daily-use acceptance program (Goal 9).** Run the full automated suite,
   a non-sensitive real-vault scan, Telegram polling/restart/duplicate tests,
   provider-outage tests, and the manual Telegram checklist. Record observed
   routing, extraction, organization, and tool-loop failures as the next
   prioritized fixes.

### Planned explicit task–Calendar association

The existing `calendar_task_event_links` table records a narrow case: a
reviewed Steward task deadline marker that Steward created in Google Calendar.
It is intentionally not a general relationship model. In particular, its
idempotency key is tied to the creation workflow, and reusing it for arbitrary
existing Calendar events would make it unclear whether Steward owns the event.

`task_calendar_associations` provides separate local one-to-one storage. Its
Telegram selection flow creates a review proposal before calling the repository.

The next relationship capability must therefore use a separate local,
reviewed association with these invariants:

- Selecting an existing Calendar event and an existing Task creates only a
  pending association proposal. It does not create, edit, delete, or claim
  ownership of the Google Calendar event.
- Approval writes an opaque local task/event association and an audit event;
  rejection writes neither. Completing the task still does not alter Calendar.
- The proposal shows the current Calendar event summary/time and task title,
  then asks for explicit approval. If either local task disappears or Calendar
  cannot re-fetch the selected event before approval, it fails closed.
- A task or event already associated through this new relationship cannot be
  silently reassigned. The user must inspect and explicitly replace/remove a
  relationship in a later dedicated workflow.
- The new association remains distinct from a Steward-created deadline marker
  and from record-to-Calendar links. Calendar remains authoritative for event
  content; SQLite stores only the opaque relationship and audit history.

Required tests: selection pagination, restart while pending, stale/deleted
task, Calendar read outage, duplicate approval, cross-chat isolation, and proof
that neither acceptance nor task completion performs a Calendar write.

### Implemented reviewed task–Calendar association

Calendar event cards now offer **Link task** only when the event has no existing
Steward task relationship. The picker shows eligible open local tasks, excludes
tasks that already have a deadline marker or an existing-event association, and
paginates the selection. Selecting a task creates a durable review card with
the refreshed event summary/time and task title.

Approval re-fetches the current Calendar event, verifies the task remains open,
then writes only the opaque one-to-one association in SQLite plus audit events.
It does not create, edit, or delete a Google Calendar event. Rejection writes no
association. A Calendar read failure or a conflicting link leaves the proposal
pending and fails closed. Task and event cards expose the resulting relationship
in both directions; completing the task still leaves Calendar untouched.

An existing-event association can be removed through its Task card only as a
separate reviewed local unlink. Approval verifies the exact stored opaque event
ID, records Activity, and changes neither the event nor any deadline marker.
The proposal fails closed if a newer association has replaced the selected one.

Automated application coverage verifies the proposal/approval and unlink paths,
reverse navigation, local-only behavior, Calendar-outage refusal, picker pagination,
and cross-chat approval refusal. Each association proposal is owned by the
Telegram chat that created it; another chat cannot accept or reject it. Live
Telegram acceptance remains required for restart while pending and actual
Calendar display behavior.

## Explicitly local-only or browser-handoff operations

Some capabilities must not become unauthenticated chat commands:

- OAuth client-secret configuration and browser consent;
- inspecting/editing `.env`, tokens, or database files directly;
- arbitrary filesystem access;
- arbitrary shell execution;
- deleting original sources without a deliberate approval workflow;
- background synchronization or autonomous external actions.

Telegram may initiate a safe handoff or report status for these operations.

## Future additions

Append new ideas below. For each, record:

```text
user need
Telegram interaction
underlying service/CLI capability
data sent to external systems
risk level and approval rule
canonical state changed
Activity event
tests and failure cases
```

### Ideas backlog

- Guided routing conversation after uncertain intake: user supplies project,
  course, purpose, or privacy context; Steward updates a reviewable proposal.
- Existing-folder onboarding: select a local directory, scan it in place, and
  receive scan status and retrieval readiness through Telegram.
