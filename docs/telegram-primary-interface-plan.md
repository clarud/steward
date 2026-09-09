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

## Current Telegram baseline

Currently Telegram supports:

- grounded questions and follow-up conversation state;
- text capture through `/save <text>`;
- document/photo capture only when `/save` is the attachment caption;
- selected durable proposal-review replies;
- delivery deduplication, retries, backoff, history, and dead-letter
  inspection through the CLI.

Current observed limitations to address:

- ordinary requests without a trailing `?` can be classified as unknown;
- natural-language workspace and Inbox organization requests are not routed to
  their services;
- replying `/save` to an earlier attachment cannot capture it because the
  reply has no attachment payload;
- production retrieval must never include test-fixture sources;
- the CLI has many capabilities that Telegram cannot yet initiate or inspect.

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

### 3. Search, sources, and retrieval controls

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

### 4. Workspaces and Inbox organization

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

### 5. Knowledge and evidence workflows

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

### 6. Record workflows

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

### 7. Calendar integration

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

### 8. Research, Drive, and Gmail

Research results are Telegram cards with source links, an ephemeral synthesis,
and explicit `Keep` / `Discard` controls. Retaining material sends it through
the normal Source and organization pipeline.

Drive and Gmail search/import are selection workflows. OAuth remains local;
imports are always explicit. Telegram must never offer a broad background sync
without a separate deliberate product decision.

### 9. Privacy and operational controls

Expose source privacy inspection and changes to the authorized owner. Before a
cloud-model action on a sensitive source, show the model destination and ask
for explicit confirmation when the policy requires it.

Operational commands include delivery inspection, dead-letter inspection,
watcher/UI status, reindexing, embedding-model download, and evaluations.
These are owner-only administrative interactions. Commands that start a local
service, download a model, retry a failed delivery, or consume substantial
resources require confirmation and clear status feedback.

### 10. Reliability, observability, and evaluation

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

## Delivery order

Build in small, independently testable slices:

1. Interaction foundation: `/help`, `/status`, cards, pagination, callback
   validation.
2. Intent routing: no-punctuation questions, Inbox inspection, source search.
3. Workspaces and organization proposals/reviews.
4. Knowledge proposal/review flows.
5. Records and field-review flows.
6. Calendar read, then proposal/review/write flows.
7. Research retention, Drive import, and Gmail import.
8. Privacy settings and administrative operations.
9. Reliability/evaluation pass across all Telegram flows.

Each slice must preserve the invariants in `docs/invariants.md`, include unit
and integration tests, and be reviewed before the next slice begins.

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

- 
