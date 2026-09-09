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
  explicit immediate-capture shortcut;
- provisional capture of substantial text notes and explicit note/thought
  prefixes, also requiring a save/discard decision;
- `/intake_context ID ...` records additional user guidance and revises a
  pending intake proposal without saving or moving the original;
- `/help`, `/status`, Inbox/source/workspace/activity inspection, lexical
  search, and safe workspace-creation proposals;
- durable, chat-scoped pagination callbacks for source and Inbox lists;
- selected durable proposal-review replies;
- delivery deduplication, retries, backoff, history, and dead-letter
  inspection through the CLI.

Current observed limitations to address:

- natural-language workspace and Inbox organization requests are not routed to
  a full organization-review workflow yet;
- replying `/save` to an earlier attachment cannot capture it because the
  reply has no attachment payload;
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
| `sources`, search, semantic/hybrid search, inspect source | `/search`, source cards, pagination, natural language | read-only; privacy filter before model use |
| capture, reextract, indexing | attachment capture, `/save`, explicit reprocess action | preserve original; confirm expensive work |
| workspaces and source links | `/workspace`, natural language, proposal cards | creation/linking is audited; writes may require review |
| Inbox review and organization proposals | `/inbox`, proposal cards, evidence buttons | no move until approval |
| concepts, claims, enrichment, connectors | knowledge cards and evidence comparison | derived changes stay pending until review |
| travel, receipts, warranties, references | record cards and field-correction flow | fields retain fragment provenance |
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
/reextract 18
```

Search cards show filename, location, heading/page, short excerpt, score where
useful, and `Read more` / `Show evidence` actions. Ensure the production
registry excludes repository fixtures and that test suites always use isolated
temporary data directories.

Acceptance criteria:

- results never disclose a source prohibited by the applicable privacy rule;
- test fixtures cannot enter a production registry;
- expensive reprocessing requires confirmation and records Activity.

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
- Telegram-visible root status and a local handoff for root selection;
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

This is a delivery program, not a claim that every item listed below already
works. Status is deliberately conservative: a goal is only **complete** when
its acceptance criteria and proportionate automated tests are satisfied.

| Goal | Current status | What is available now | Important remaining work |
| --- | --- | --- | --- |
| 0. Production-source integrity | Complete | Production fixture cleanup, isolated tests, explicit source removal | Keep enforcing root exclusions as roots evolve |
| 1. Routing foundation | Substantially complete | Commands, deterministic natural-language reads, callbacks, pagination | Broader reply/reference-resolution cases |
| 2. Provisional intake | Substantially complete | Attachments and substantial text are staged, classified locally, contextualized, then accepted/discarded | Explicit external-analysis choice and more extractor-specific review |
| 3. Organization review | Substantially complete | Inbox review, durable organization decisions, reviewable source-to-workspace links without file movement, and context revision to an existing workspace | Edit-target and emerging-workspace proposal UX |
| 4. Read tools | Substantially complete | Read-only source/knowledge/record/workspace/activity tool agent; Calendar reads in the agent only when local OAuth is already configured | Broader provider-failure evaluations |
| 5. Tasks, records, Calendar | In progress | Reviewable Tasks with explicit completion, natural task phrasing, offset-aware deadlines, and reviewable idempotent Calendar deadline markers; travel/receipt/warranty record proposal/review; approved, duplicate-protected Calendar-event proposal/write; reviewed travel/receipt/warranty corrections | Reminder scheduling and richer task/Calendar integration |
| 6. Curated knowledge/research | In progress | Concept/claim enrichment review, explicit or reply-selected curated-note proposals, ephemeral research cards with retain-to-Inbox | Conversation synthesis, external-source retention beyond a labeled note, conflict-review UX |
| 7. Multi-root and reliability | In progress | Locally authorized roots, root health, enforced exclusions, root watches, delivery diagnostics, and write-once local SQLite snapshot/confirmed restore | Recovery rehearsal, start-at-login/log rotation, and fault-injection coverage |
| 8. Imports and administration | In progress | Explicit Drive/Gmail search/select/import, audited source privacy controls, delivery inspection and status | Local OAuth status, confirmed maintenance flows |
| 9. Daily-use hardening | Not started | Unit/integration coverage has grown with each slice | Real-vault/Telegram checklist, metrics, restart/outage evaluation and sustained trial |

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
