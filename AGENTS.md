# Steward

## Master Product, Architecture & Learning Plan

**Working name:** Steward
**Product:** Local-first personal memory, knowledge and action assistant
**Primary interface:** Telegram
**Primary runtime:** Local Python service
**Primary orchestration framework:** LangGraph
**Primary human-readable knowledge store:** Existing files/Markdown
**Primary operational metadata store:** SQLite

---

# 1. Mission

Steward is a local-first personal assistant whose job is to:

> preserve what I deliberately give it, understand it, organize it as my life and work evolve, remember it better than I do, retrieve it even when my memory is vague, connect it to relevant knowledge and contexts, and safely take useful actions on my behalf.

Steward should eventually function as:

```text
personal memory
      +
knowledge system
      +
document/record organizer
      +
project context
      +
retrieval assistant
      +
tool-using personal assistant
```

It should not depend on me maintaining perfect organization.

The system should compensate for imperfect human memory and imperfect filing habits.

---

# 2. North-Star Experience

The ultimate test is not:

> Can Steward answer questions about my PDFs?

The test is:

> I vaguely remember something from six months ago. I don't remember the filename, exact terminology, where I saved it, or even whether it came from a PDF, project or Telegram conversation. Can Steward reconstruct what I'm thinking about?

Examples:

> What was that thing about CPUs invalidating translations on other CPUs?

> What did I conclude about using Kafka for the scheduler?

> Find the article someone sent me about SQLite being used as a queue.

> What time is my Tokyo flight?

> Put that flight in my calendar.

> Where did you get that explanation?

> I'm starting a compiler project. Keep these notes together.

> These things I've been sending about distributed systems probably belong together.

That is Steward.

---

# 3. Core Product Loop

The long-term product loop is:

```text
CAPTURE
   ↓
UNDERSTAND
   ↓
ORGANIZE
   ↓
INTEGRATE
   ↓
REMEMBER
   ↓
RETRIEVE
   ↓
CONNECT
   ↓
REASON
   ↓
ACT
```

Not every request goes through every stage.

For example:

```text
"Save this PDF"

CAPTURE
→ UNDERSTAND
→ ORGANIZE
→ INTEGRATE
```

While:

```text
"What did I decide about Redis?"

RETRIEVE
→ CONNECT
→ REASON
```

And:

```text
"Add my flight to my calendar"

RETRIEVE
→ VERIFY
→ ACT
```

---

# 4. Product Domains

Steward has five foundational domains.

## 4.1 Sources

Sources are original evidence.

Examples:

```text
Markdown
PDF
DOCX
webpage
Telegram message
image
source code
research paper
course notes
ticket
itinerary
email later
```

A Source answers:

> Where did this information originate?

Original sources are preserved.

AI-derived content must never silently replace them.

---

## 4.2 Knowledge

Knowledge is reusable information Steward builds from accumulated sources.

Examples:

```text
Virtual Memory
Kafka
Redis
Idempotency
B-Trees
LangGraph
OAuth
Distributed Consensus
```

Knowledge can contain:

```text
Concepts
Claims
Aliases
Relationships
Evidence
Contradictions
Qualifications
Derived explanations
```

Important:

```text
Knowledge != folder hierarchy
Knowledge != AI summaries
Knowledge != vector embeddings
```

Knowledge is an evidence-backed semantic representation over sources.

---

## 4.3 Workspaces

Workspaces represent ongoing contexts.

Examples:

```text
Steward
Compiler Project
Job Scheduler
Operating Systems Course
Internship
Japan Trip
Temporary research investigation
```

A Workspace answers:

> What am I doing, and what information matters in this context?

A workspace can reference global Knowledge.

For example:

```text
Workspace: Steward

related knowledge:
- LangGraph
- RAG
- embeddings
- Telegram bots
- information retrieval
- SQLite
```

The knowledge does not have to physically live inside the workspace.

---

## 4.4 Records

Records represent concrete information about my life.

Examples:

```text
Flight
Hotel reservation
Invoice
Receipt
Warranty
Contract
Ticket
Subscription
Certificate
Booking
```

A Record answers:

> What concrete thing/fact does this source describe?

Example:

```text
TravelRecord

flight_number: SQ638
departure: Singapore
arrival: Tokyo
departure_time: ...
booking_reference: ...
source: itinerary.pdf
```

---

## 4.5 Activity

Activity represents what happened over time.

Examples:

```text
captured source
created workspace
moved source
accepted organization proposal
rejected proposal
asked question
created calendar event
changed project decision
researched something
deleted something
```

Activity answers:

> What happened, when, and why?

This makes Steward capable of reconstructing personal history.

---

# 5. Important Relationship Between the Domains

Objects do not belong exclusively to one domain.

Example:

```text
redis-streams.pdf
       │
       ├── Source
       │
       ├── belongs to → Job Scheduler Workspace
       │
       ├── supports → Redis Knowledge
       │
       ├── supports → Consumer Groups Knowledge
       │
       ├── supports → Delivery Semantics Knowledge
       │
       └── capture → Activity Event
```

Similarly:

```text
tokyo-itinerary.pdf
       │
       ├── Source
       ├── belongs to → Japan Trip
       ├── creates → Travel Record
       └── capture → Activity
```

This means Steward must distinguish:

```text
WHAT IS THIS?
WHERE SHOULD ITS FILE LIVE?
WHAT DOES IT RELATE TO?
WHAT INFORMATION CAN BE EXTRACTED?
```

These are different questions.

---

# 6. Filesystem Philosophy

My filesystem should remain human-readable.

Possible layout:

```text
vault/
├── inbox/
├── knowledge/
├── projects/
├── records/
├── sources/
└── archive/
```

Steward-specific operational data:

```text
.steward/
├── steward.db
├── checkpoints.db
├── indexes/
├── cache/
├── logs/
└── config/
```

The filesystem provides:

```text
primary physical location
```

The database provides:

```text
semantic relationships
```

Therefore:

> One physical home, many conceptual relationships.

Do not duplicate a PDF simply because it relates to five concepts.

---

# 7. Inbox

Steward must be allowed to be uncertain.

If it cannot confidently organize something:

```text
vault/inbox/
```

is the correct destination.

This is not failure.

Later:

> Organize my inbox.

can run a dedicated organizational workflow.

This gives Steward a safe fallback instead of forcing incorrect decisions.

---

# 8. Organization Must Evolve

Steward should not only classify information into existing categories.

It should detect when the structure itself needs to change.

Example:

Over two weeks I send:

```text
LangGraph docs
Telegram bot notes
RAG paper
embedding benchmarks
memory architecture article
agent state notes
```

Steward sees no strong existing workspace.

Eventually:

> You appear to be working on a distinct project involving LangGraph, Telegram, retrieval and agent memory. Create a new workspace called **Steward**?

This is a core product capability.

The organization should evolve with my activities.

---

# 9. Canonical vs Derived Information

## Canonical / irreplaceable

Protect and back up:

```text
original source files
user-authored Markdown
explicitly retained external sources
workspace decisions
records
activity history
user approvals/rejections
external action IDs
```

## Derived / rebuildable

Can be regenerated:

```text
embeddings
chunks
semantic indexes
LLM summaries
concept syntheses
candidate tags
temporary classifications
derived organization scores
```

Important principle:

> If all AI-derived data disappears, my original information should still exist.

---

# 10. Knowledge Philosophy

Steward should not create:

```text
Kafka summary 1
Kafka summary 2
Kafka summary 3
Kafka summary 4
```

Instead:

```text
Concept: Kafka
│
├── Claims
│   ├── claim A
│   ├── claim B
│   └── claim C
│
├── Evidence
│   ├── source 1
│   ├── source 2
│   └── source 3
│
├── Relationships
│   ├── Consumer Groups
│   ├── Delivery Semantics
│   └── Event Streaming
│
└── Current Synthesis
    generated when needed
```

New information should be compared against existing knowledge.

Possible operations:

```text
CONFIRM
EXTEND
REFINE
CONTRADICT
QUALIFY
```

---

# 11. Provenance

Steward must be able to answer:

> Why do you believe this?

Every meaningful derived claim should retain supporting evidence where practical.

Conceptually:

```text
Claim
  ↓
ClaimEvidence
  ↓
SourceFragment
  ↓
Source
```

This is one of Steward's main defenses against AI corruption.

---

# 12. Trust Model

Avoid a fake:

```text
confidence = 0.92
```

Instead preserve several dimensions where useful:

```text
source type
source authority
evidence relevance
freshness
number of supporting sources
contradicting evidence
extraction certainty
scope
```

Knowledge status might eventually include:

```text
weakly_supported
supported
strongly_supported
disputed
stale
context_specific
```

These should not be implemented until useful.

---

# 13. External Information

External research is allowed.

However:

```text
retrieved from internet
!=
permanent personal knowledge
```

Lifecycle:

```text
EPHEMERAL
   ↓
CANDIDATE
   ↓
RETAINED
```

Example:

> How does Linux actually perform TLB shootdowns?

Steward:

```text
search local knowledge
       ↓
insufficient evidence
       ↓
external research
       ↓
answer with external evidence
```

Then:

> Keep those sources.

Only then do the selected sources enter the durable Source layer.

---

# 14. Conversation Memory

Conversation state answers:

> What are we talking about right now?

It should contain things like:

```text
recent messages
current workspace
current sources
recent concepts
recent records
resolved references
pending action
pending approval
```

Example:

> What do I know about Kafka?

Then:

> How is that different from Redis?

Conversation state resolves:

```text
"that" → Kafka
```

Conversation history should not automatically become permanent Knowledge.

---

# 15. Steward's Initial Tool Philosophy

A tool represents a meaningful capability the model may choose to use.

Not every Python function becomes a tool.

Examples:

```text
search_knowledge
read_source
search_records
search_workspaces
search_activity

search_calendar
get_calendar_event

create_calendar_event

research_web

create_workspace
move_source
delete_source
```

Internal implementation functions remain ordinary Python.

---

# 16. Deterministic vs LLM

## Deterministic

Use normal code for:

```text
hashing
filesystem access
file movement
database writes
permissions
deduplication
parser execution
index synchronization
Calendar API calls
audit logging
rollback
```

## LLM-assisted

Use models for:

```text
document interpretation
ambiguous intent
concept extraction
claim proposal
organization suggestion
workspace detection
query rewriting
answer synthesis
relationship discovery
research synthesis
reference resolution
```

Core rule:

```text
LLM
 ↓
PROPOSE

CODE
 ↓
VALIDATE

CODE
 ↓
EXECUTE
```

---

# 17. Architecture

Target architecture:

```text
                         Telegram
                            │
                            ▼
                     Telegram Adapter
                            │
                            ▼
                      Event Normalizer
                            │
                            ▼
                       LangGraph
                  state + orchestration
                            │
        ┌───────────────────┼──────────────────┐
        │                   │                  │
        ▼                   ▼                  ▼
   Retrieval            Knowledge           Actions
   Service              Service             Service
        │                   │                  │
        ├──────────────┐    │       ┌──────────┼─────────┐
        ▼              ▼    ▼       ▼          ▼         ▼
     Sources         SQLite Index Calendar    Web       Files
        │
        ▼
   Filesystem
```

LangGraph should orchestrate services.

It should not own the application's domain logic.

---

# 18. Normal Python Service Boundaries

Likely services over time:

```text
SourceService
ExtractionService
RetrievalService
WorkspaceService
OrganizationService
KnowledgeService
RecordService
ActivityService
ActionPolicyService
CalendarService
ResearchService
```

Each should be usable and testable without LangGraph.

---

# 19. Repository Target

The repository may eventually resemble:

```text
steward/
├── README.md
├── pyproject.toml
├── .env.example
│
├── docs/
│   ├── product.md
│   ├── architecture.md
│   ├── invariants.md
│   ├── data-model.md
│   ├── workflows/
│   ├── adr/
│   └── learning/
│
├── src/
│   └── steward/
│       ├── config/
│       ├── domain/
│       ├── storage/
│       ├── sources/
│       ├── extraction/
│       ├── retrieval/
│       ├── knowledge/
│       ├── organization/
│       ├── workspaces/
│       ├── records/
│       ├── activity/
│       ├── tools/
│       ├── graphs/
│       ├── telegram/
│       └── cli/
│
└── tests/
    ├── unit/
    ├── integration/
    ├── evaluation/
    └── fixtures/
```

Do not create all these modules immediately.

The project should grow into them.

---

# 20. Development Philosophy

Steward has two simultaneous goals:

## Product goal

Build something I genuinely use.

## Learning goal

Understand agentic AI well enough that I can explain:

```text
why state exists
where state lives
why a graph exists
why a node exists
why a tool exists
who controls side effects
what the LLM is deciding
what deterministic code is deciding
how persistence works
how retrieval works
how failures recover
```

Product velocity is secondary to understanding.

---

# 21. Phase 0 — Project Constitution

## Product progress

No assistant yet.

Create the project foundation.

## Implement

```text
Python project
configuration
logging
testing
README
docs
```

Write:

```text
docs/product.md
docs/invariants.md
docs/architecture.md
docs/adr/
docs/learning/
```

Initial ADRs:

```text
ADR-001 Originals remain authoritative
ADR-002 SQLite stores operational metadata
ADR-003 LangGraph orchestrates domain services
ADR-004 Filesystem access is restricted
ADR-005 Derived knowledge requires provenance
```

## You learn

```text
Python packaging
dependency management
configuration
architectural boundaries
ADRs
testing fundamentals
```

## Learning gate

Before continuing, explain:

1. What is an ADR?
2. What is canonical data?
3. What is derived data?
4. Why SQLite?
5. Why isn't LangGraph being added yet?

---

# 22. Phase 1 — Source Registry

## Capability

Steward can recognize and register local Markdown files.

CLI:

```text
steward scan ~/vault
steward sources
```

## Implement

```text
Source
SourceRepository
SQLite schema
file hashing
vault scanner
```

Source identity should rely primarily on content/path metadata rather than filenames alone.

## Important concept

Idempotency.

Running:

```text
steward scan
steward scan
steward scan
```

should not create three copies of everything.

## You learn

```text
SQLite
repository pattern
hashing
idempotency
filesystem traversal
database migrations
```

## Tests

```text
new file
unchanged file
modified file
deleted file
duplicate content
unsupported file
```

## Learning gate

Draw the entire lifecycle:

```text
filesystem
→ scanner
→ hash
→ repository
→ database
```

without looking at the code.

---

# 23. Phase 2 — Markdown Extraction

## Capability

Steward understands Markdown structurally.

Example:

```text
virtual-memory.md

# Virtual Memory
## Page Tables
## TLB
```

becomes source fragments.

## Implement

```text
SourceFragment
MarkdownExtractor
ExtractionResult
```

Fragments retain:

```text
source ID
heading
ordinal
text
location
```

## You learn

```text
parsing
chunking
provenance
interface design
content addressing
```

## Important exercise

Try multiple chunk strategies manually.

Understand the failure modes of:

```text
fixed token chunks
paragraph chunks
heading-based chunks
overlapping chunks
```

Do not immediately ask Codex to choose for you.

---

# 24. Phase 3 — Lexical Search

## Capability

```text
steward search "virtual memory"
```

## Implement

SQLite FTS5 search.

Do not add embeddings.

## You learn

```text
inverted indexes
BM25/lexical retrieval concepts
tokenization
ranking
search evaluation
```

## Build an evaluation set

Create:

```text
tests/evaluation/retrieval_cases.yaml
```

with 20–30 real questions.

Example:

```text
query:
"virtual address translation"

expected:
virtual-memory.md#Page Tables
```

## Gate

Know what lexical retrieval can and cannot do before adding semantic search.

---

# 25. Phase 4 — Semantic Retrieval

## Capability

Search by vague meaning.

Example:

> the little cache CPUs use for address translation

should find TLB notes.

## Implement

Interfaces:

```text
EmbeddingProvider
SemanticIndex
HybridRetriever
```

Combine:

```text
lexical retrieval
+
semantic retrieval
```

## Do not couple the application to one vector database.

The application depends on:

```text
SemanticIndex
```

not directly on a particular vendor/library.

## You learn

```text
embeddings
vector similarity
cosine similarity
chunk representation
hybrid retrieval
Recall@K
MRR
```

## Critical learning task

For at least ten queries:

1. inspect lexical results;
2. inspect semantic results;
3. inspect hybrid results;
4. explain why they differ.

---

# 26. Phase 5 — First LLM

## Capability

```text
steward ask "What do I know about virtual memory?"
```

## Flow

```text
question
   ↓
retrieval
   ↓
source fragments
   ↓
LLM
   ↓
answer
   ↓
citations/provenance
```

## Implement

```text
ModelGateway
AnswerService
ContextBuilder
```

Keep provider-specific code behind ModelGateway.

## You learn

```text
prompt construction
context windows
grounding
hallucinations
structured outputs
model abstraction
```

## Requirement

The model cannot access arbitrary files.

It only receives explicitly retrieved context.

## Gate

For any generated answer, you should be able to answer:

> Exactly which information was sent to the LLM?

---

# 27. Phase 6 — First LangGraph

Now LangGraph enters.

Do not use a prebuilt generic agent.

Build the smallest graph yourself.

```text
START
  ↓
retrieve
  ↓
answer
  ↓
END
```

State:

```text
question
retrieved_fragment_ids
answer
```

Then add one conditional route.

For example:

```text
needs_retrieval?
```

LangGraph's graph model explicitly centers around shared state, nodes and edges, so this phase should be about those primitives rather than agent abstractions.

## You learn

```text
StateGraph
state
nodes
edges
conditional edges
reducers
START / END
graph compilation
invoke
stream
```

## Personal exercise

Before Codex implements the graph:

Draw it yourself.

For every node write:

```text
INPUT STATE
WORK
OUTPUT STATE
SIDE EFFECTS
```

---

# 28. Phase 7 — Telegram

## Capability

Message Steward from Telegram.

Start with text-only questions.

Long polling is appropriate initially because the service can remain local without exposing a webhook endpoint; current `python-telegram-bot` versions still provide polling support.

## Architecture

Telegram code only does:

```text
Telegram Update
      ↓
normalize
      ↓
Steward application
      ↓
response
      ↓
Telegram
```

Do not place knowledge logic inside Telegram handlers.

## Internal event

```text
IncomingEvent

id
platform
chat_id
message_id
reply_to_id
timestamp
text
attachments
```

## You learn

```text
async Python
adapters
external event APIs
separation of transport and domain logic
```

---

# 29. Phase 8 — Conversation State

## Capability

```text
User:
"What do I know about page tables?"

User:
"How does that relate to TLBs?"
```

Steward understands `that`.

## LangGraph State

Add:

```text
messages
current_workspace_id
recent_source_ids
recent_concepts
recent_records
referents
```

Do not put full PDFs into graph state.

Use IDs.

## Persistence progression

First:

```text
in-memory checkpointer
```

Understand it.

Then:

```text
persistent local checkpointer
```

LangGraph persistence saves graph-state checkpoints into threads, which is precisely what makes durable conversation and resumable execution possible.

## You learn

```text
short-term memory
thread identity
checkpoints
persistent graph state
state history
restart recovery
```

## Gate

Restart Steward.

Continue the conversation.

Understand exactly why it works.

---

# 30. Phase 9 — Telegram Capture

## Capability

Send:

```text
text
Markdown
PDF
```

and say:

> Save this.

## Flow

```text
Telegram attachment
      ↓
download
      ↓
preserve original
      ↓
hash
      ↓
extract
      ↓
fragment
      ↓
index
      ↓
save to Inbox
      ↓
ActivityEvent
```

Do not organize intelligently yet.

Everything enters Inbox.

## You learn

```text
file handling
binary data
Telegram attachments
ingestion pipelines
partial failures
idempotency across external events
```

---

# 31. Phase 10 — Document Extraction Architecture

## Capability

Different file types use appropriate extraction.

Interface:

```text
DocumentExtractor

extract(path) -> ExtractionResult
```

Implement:

```text
MarkdownExtractor
PlainTextExtractor
PdfExtractor
```

Later:

```text
DocxExtractor
HtmlExtractor
ImageExtractor
```

For PDFs:

```text
native text extraction first
OCR only when needed
```

## You learn

```text
strategy pattern
parser reliability
structured extraction
page provenance
failure handling
```

---

# 32. Phase 11 — Intent Resolution

Now Steward begins interpreting requests.

Possible initial intents:

```text
ASK
CAPTURE
ORGANIZE
DELETE
INSPECT
ACTION
UNKNOWN
```

Do not build separate agents.

## Resolution order

Use deterministic information first.

Example:

```text
/document attachment
→ definitely contains incoming material

/delete command
→ deterministic intent

reply_to_message
→ strong reference signal
```

Only then use the LLM.

## Structured decision

```text
IntentDecision

primary_intent
secondary_intents
referenced_objects
confidence
```

## You learn

```text
structured model output
routing
classification
hybrid deterministic/LLM systems
```

---

# 33. Phase 12 — Workspaces

## Capability

```text
"I'm starting a compiler project."

"This PDF is for Steward."

"Show me files for my database project."
```

Implement:

```text
Workspace
WorkspaceRepository
WorkspaceService
WorkspaceSource
```

Initially creation can be explicit.

Example:

```text
/create-workspace Steward
```

Then natural language later.

## You learn

```text
domain modeling
many-to-many relationships
context scoping
lifecycle/status
```

---

# 34. Phase 13 — Intelligent Organization

Now Steward interprets uploaded material.

## Flow

```text
Source
  ↓
Understand
  ↓
Inspect Workspaces
  ↓
Retrieve related knowledge/files
  ↓
score likely organizational fits
  ↓
strong fit?
  ├── yes → proposal
  └── no
       ↓
    new workspace likely?
       ├── yes → proposal
       └── no → Inbox
```

## OrganizationProposal

```text
source_id
proposal_type
workspace_id
suggested_path
rationale
confidence
status
```

Initially:

```text
Steward proposes
User decides
```

---

# 35. Phase 14 — Human-in-the-Loop LangGraph

This is your first deliberately resumable workflow.

Example:

```text
analyze source
     ↓
create proposal
     ↓
interrupt
     ↓
Telegram:
"Move this into Steward?"
     ↓
user response
     ↓
resume
     ↓
execute
```

LangGraph interrupts are designed specifically to pause execution, persist graph state and resume from user input; nodes containing interrupts may re-execute from their start, so operations before an interrupt must be designed with idempotency in mind.

## You learn

```text
interrupt()
Command(resume=...)
durable execution
human-in-the-loop
idempotent node design
resuming graphs
```

## Gate

You should be able to explain what happens if Steward is shut down while awaiting approval.

---

# 36. Phase 15 — Activity Log

Before more automation, add auditability.

Implement:

```text
ActivityEvent
ActivityService
```

Events:

```text
SOURCE_CAPTURED
SOURCE_MOVED
SOURCE_DELETED
WORKSPACE_CREATED
ORGANIZATION_PROPOSED
ORGANIZATION_ACCEPTED
ORGANIZATION_REJECTED
```

## Capability

> What did I save yesterday?

> What happened to the PDF I sent earlier?

## You learn

```text
event logs
temporal queries
auditability
append-oriented records
```

---

# 37. Phase 16 — Undo and Safe Mutations

## Capability

> Don't keep that.

> Undo the last move.

Every mutation returns something like:

```text
ActionResult

status
object_id
rollback_data
activity_event_id
```

## You learn

```text
transactions
rollback
reversible actions
idempotent mutations
failure recovery
```

---

# 38. Phase 17 — Knowledge Concepts

Until now Steward primarily indexes Sources.

Now introduce semantic Knowledge.

Start small:

```text
Concept
ConceptAlias
```

Example:

```text
Concept:
Translation Lookaside Buffer

Aliases:
TLB
translation cache
```

## Extraction

A new document may produce:

```text
ConceptProposal

existing_matches
new_concepts
supporting_fragments
```

The model proposes.

Your KnowledgeService persists.

## You learn

```text
entity extraction
entity resolution
canonical naming
structured generation
```

---

# 39. Phase 18 — Claims and Evidence

Introduce:

```text
Claim
ClaimEvidence
```

Example:

```text
Claim:
"A TLB caches recently used address translations."

Evidence:
virtual-memory.md#TLB
textbook.pdf page 213
```

Do not extract every sentence.

Only create claims when they materially improve Knowledge.

## You learn

```text
provenance
claim modeling
evidence linking
epistemic uncertainty
```

---

# 40. Phase 19 — Knowledge Enrichment

When a new source arrives:

```text
new evidence
      ↓
retrieve related Knowledge
      ↓
compare
      ↓
CONFIRM
EXTEND
REFINE
QUALIFY
CONTRADICT
      ↓
knowledge proposal
      ↓
persist evidence-backed changes
```

Example:

```text
Existing:
Retries may duplicate operations.

New:
Payment systems often use idempotency keys.

Result:
EXTEND
```

## Current concept view

Generate on demand:

```text
generate_concept_view("idempotency")
```

This synthesis is not canonical.

## You learn

```text
knowledge consolidation
comparison prompts
contradiction handling
derived vs canonical state
```

---

# 41. Phase 20 — Records

Start with one useful domain.

Recommended:

```text
TravelRecord
```

because itineraries exercise rich extraction and eventually Calendar integration.

Upload itinerary:

```text
PDF
 ↓
Source
 ↓
extract text
 ↓
interpret document
 ↓
TravelRecord proposal
 ↓
persist
```

Possible fields:

```text
flight number
departure
arrival
departure time
arrival time
booking reference
passenger
```

Every important extracted field should retain source provenance.

## You learn

```text
schema extraction
structured LLM output
domain records
field-level provenance
```

---

# 42. Phase 21 — Agent Tool Calling

Only now introduce an actual tool-choosing agent loop.

Initial tools are read-only:

```text
search_sources
read_source
search_knowledge
search_records
search_workspaces
search_activity
```

Graph:

```text
             ┌──────────────┐
             │     LLM      │
             └──────┬───────┘
                    │
             wants a tool?
               /        \
             no          yes
             │            │
             ▼            ▼
           answer       ToolNode
                          │
                          └────→ LLM
```

Current LangGraph/LangChain documentation exposes `ToolNode` specifically as the lower-level building block for tool execution inside custom graphs, which makes it a better teaching target for Steward than jumping immediately to a prebuilt agent abstraction.

## Do not start with

```text
create_agent(...)
```

until you understand the tool loop yourself.

## You learn

```text
tool schemas
tool binding
tool calls
ToolMessage
ToolNode
agent loop
conditional routing
termination
```

## Gate

Explain exactly how:

```text
model → tool call → tool execution → tool result → model
```

works.

---

# 43. Phase 22 — Tool Risk Model

Define:

```text
READ_ONLY
SAFE_WRITE
SENSITIVE_WRITE
DESTRUCTIVE
```

Each tool declares:

```text
side_effects
risk
idempotency
external_system
requires_approval
```

Examples:

```text
search_records
risk = READ_ONLY

create_calendar_event
risk = SAFE_WRITE or SENSITIVE_WRITE

delete_source
risk = DESTRUCTIVE
```

## You learn

```text
capability security
least privilege
policy enforcement
agent safety architecture
```

---

# 44. Phase 23 — Google Calendar Read Integration

First external personal system.

Tools:

```text
calendar_search
calendar_get_event
```

Google's current Python quickstart uses OAuth user authorization and demonstrates reading upcoming events, so this is a good first external authenticated integration.

Important rule:

> Google Calendar is authoritative about current Calendar state.

Steward may retain external IDs/activity, but should query Calendar when current truth matters.

## Example

> Do I have anything scheduled when I land in Tokyo?

Agent:

```text
search_records("Tokyo")
        +
calendar_search(...)
        ↓
reason
        ↓
answer
```

## You learn

```text
OAuth
scopes
credentials
refresh tokens
external APIs
tool adapters
API failures
```

---

# 45. Phase 24 — Calendar Write Integration

Add:

```text
create_calendar_event
```

The Calendar API supports explicit event insertion, which should remain encapsulated inside CalendarService rather than exposing generic HTTP to the model.

Example:

> Put my flight on my calendar.

Flow:

```text
resolve flight
    ↓
TravelRecord
    ↓
supporting source
    ↓
calendar search
    ↓
duplicate detection
    ↓
construct event
    ↓
approval/policy
    ↓
CalendarService
    ↓
external event ID
    ↓
ActivityEvent
```

## Important requirement

Repeating the same request should not produce duplicate calendar events.

## You learn

```text
side effects
idempotency keys
external IDs
partial failure
tool approval
exactly-once-ish behavior
```

---

# 46. Phase 25 — External Research

Add a research capability.

Flow:

```text
question
  ↓
retrieve personal evidence
  ↓
sufficient?
  ├── yes → answer
  └── no
       ↓
   external research
       ↓
   evidence bundle
       ↓
   synthesized answer
```

External evidence remains:

```text
EPHEMERAL
```

unless retained.

## User

> Keep those sources.

Then external material goes through normal:

```text
Source
→ Extraction
→ Organization
→ Knowledge integration
```

## You learn

```text
research agents
search tools
source ranking
freshness
evidence synthesis
agent stopping criteria
```

---

# 47. Phase 26 — Emerging Workspace Detection

Steward begins discovering new structure.

Initially make it explicit:

> Review my Inbox.

or:

> Check whether my recent files should be reorganized.

Flow:

```text
recent sources
      ↓
representation
      ↓
cluster
      ↓
existing workspace comparison
      ↓
new coherent group?
      ↓
workspace proposal
```

Do not make this fully autonomous.

## You learn

```text
clustering
recommendation systems
threshold design
feedback loops
human approval
```

---

# 48. Phase 27 — Knowledge Connector

Generate useful cross-domain connections.

Candidate generation:

```text
shared concepts
semantic similarity
shared mechanisms
workspace overlap
co-occurrence
graph paths
```

Proposal must include:

```text
connection
why useful
supporting evidence
where analogy breaks
confidence
```

Feedback:

```text
useful
obvious
stretch
wrong
```

## You learn

```text
graph reasoning
candidate generation
ranking
LLM evaluation
user feedback signals
```

---

# 49. Phase 28 — File Watching

Manual Markdown edits must stay first-class.

Flow:

```text
filesystem event
      ↓
debounce
      ↓
hash
      ↓
actual change?
      ↓
extract
      ↓
update fragments
      ↓
re-index
      ↓
reconsider affected knowledge
```

Do not rely purely on filesystem watcher events.

Hashes remain authoritative for change detection.

## You learn

```text
event-driven architecture
debouncing
incremental indexing
cache invalidation
```

---

# 50. Phase 29 — More Tools

Add integrations one at a time.

Potentially:

```text
Calendar
↓
Gmail search/read
↓
Gmail actions
↓
GitHub
↓
task system
↓
other personal services
```

Each follows:

```text
External API
   ↓
Adapter
   ↓
Domain Service
   ↓
Tool
   ↓
Policy
   ↓
Activity
```

Do not create a generic god-level external tool.

---

# 51. Phase 30 — Privacy Policy

Introduce per-source privacy rules.

Possible:

```text
EXTERNAL_ALLOWED
EXTERNAL_REDACTED
LOCAL_MODEL_ONLY
NO_MODEL
```

Example:

```text
personal financial records
→ LOCAL_MODEL_ONLY

public course notes
→ EXTERNAL_ALLOWED
```

Policy is evaluated before model routing.

## You learn

```text
data boundaries
privacy architecture
authorization vs authentication
data minimization
```

---

# 52. Phase 31 — Local Models and Model Routing

Only introduce model routing once there is a genuine reason.

Decision:

```text
task
 ↓
privacy policy
 ↓
complexity
 ↓
capability
 ↓
latency
 ↓
cost
 ↓
model
```

Possible:

```text
local model:
classification
simple extraction
embeddings

cloud model:
complex synthesis
difficult reasoning
when privacy policy permits
```

## You learn

```text
local inference
routing
fallbacks
model evaluation
latency/cost tradeoffs
```

---

# 53. Phase 32 — Reliability Engineering

Deliberately break Steward.

Test:

```text
kill process during capture
kill process during interrupt
duplicate Telegram event
bad PDF
invalid model output
LLM timeout
Calendar timeout
SQLite lock
missing file
manually moved source
corrupted index
```

Determine how each failure should recover.

## You learn

```text
fault tolerance
retry policies
persistent workflows
transaction boundaries
idempotency
graceful degradation
```

---

# 54. Phase 33 — Observability

You should be able to inspect:

```text
Incoming request
    ↓
normalized event
    ↓
initial graph state
    ↓
node
    ↓
state update
    ↓
route
    ↓
retrieval
    ↓
model input
    ↓
model output
    ↓
tool call
    ↓
tool result
    ↓
final response
```

This is critical for your learning.

Start with structured local logs.

Do not hide everything behind a visual agent platform.

---

# 55. Phase 34 — Evaluation Framework

Maintain evaluation sets for different subsystems.

## Retrieval

```text
query
expected sources
```

Metrics:

```text
Recall@K
MRR
```

## Organization

```text
source
expected workspace
acceptable alternatives
```

Measure:

```text
acceptance rate
confident wrong rate
Inbox rate
```

## Record extraction

```text
itinerary
expected flight number
expected dates
expected airports
```

## Knowledge integration

Expected:

```text
CONFIRM
EXTEND
REFINE
QUALIFY
CONTRADICT
```

## Tool calling

```text
request
expected tool
forbidden tools
expected arguments
```

## Agent safety

```text
request
should require approval?
should refuse tool?
```

---

# 56. Data Model Target

Do not implement all at once.

Eventually:

```text
Source
SourceFragment

Workspace
WorkspaceSource

Concept
ConceptAlias

Claim
ClaimEvidence

ConceptRelation

Record

ActivityEvent

OrganizationProposal

ExternalAction
```

The schema should emerge phase by phase.

---

# 57. Security Invariants

Before Steward gains serious write abilities:

```text
configured filesystem roots only
no arbitrary shell tool
path traversal protection
secrets excluded from Git
tool arguments validated
limited OAuth scopes
audit mutations
approval for destructive actions
attachment size limits
sanitized filenames
backup strategy
```

The model never directly receives unrestricted access to your computer.

---

# 58. What Not to Build Early

Explicitly avoid:

```text
Supervisor Agent
Manager Agent
Research Agent
Knowledge Agent
File Agent
Calendar Agent
```

unless there later becomes a genuine architectural need for independent sub-agents.

Also postpone:

```text
full knowledge graph database
Neo4j just because it looks graph-like
spaced repetition
mastery scores
automatic email sending
fully autonomous reorganization
continuous autonomous research
dozens of tools
complex ontology
dashboard/UI
multi-user support
microservices
Kubernetes
```

Steward should remain one understandable local application for a long time.

---

# 59. Learning Protocol With Codex

This may be the most important part of the entire project.

Never prompt:

> Build Phase 13.

Instead break phases into tiny tasks.

For every task:

## Step A — Design without code

Prompt Codex:

```text
We are implementing this specific Steward task:

<task>

Do not write code yet.

Explain:
1. the requirement;
2. where it belongs architecturally;
3. the data flow;
4. files you propose changing;
5. abstractions needed;
6. dependencies needed;
7. alternatives we could choose;
8. important failure modes.

Keep the scope strictly limited to this task.
```

Read it.

Challenge anything you do not understand.

---

# 60. Step B — Predict the implementation

Before allowing code, you should personally write down:

```text
files that will change
classes/functions expected
input/output
state changes
database changes
side effects
tests
```

Then compare your prediction against Codex's proposal.

---

# 61. Step C — Implement Small Slice

Prompt:

```text
Implement only the agreed Steward task.

Constraints:
- do not implement future phases;
- do not introduce abstractions that are not needed yet;
- do not perform unrelated refactors;
- preserve domain/transport separation;
- include tests;
- keep LangGraph-specific code out of domain services;
- explain any new dependency;
- prefer explicit readable code over clever code.
```

---

# 62. Step D — Diff Review

After implementation:

```text
Explain this diff to me as its future maintainer.

For every changed file explain:
- why it exists;
- what changed;
- control flow;
- important functions/classes;
- state mutation;
- failure cases;
- design alternatives;
- what I should understand before continuing.

Do not modify code.
```

---

# 63. Step E — Learning Test

Ask Codex:

```text
Quiz me with 5-10 technical questions about the implementation.

Do not give me the answers initially.

Questions should test whether I understand:
- architecture;
- control flow;
- state;
- persistence;
- failure modes;
- design tradeoffs.
```

You answer independently.

Only then ask it to evaluate you.

---

# 64. Step F — Rebuild Small Pieces Yourself

For important concepts, deliberately delete/rebuild tiny examples separately.

Examples:

```text
write a tiny SQLite repository from scratch
write cosine similarity manually
create a 3-node LangGraph without Steward
write one simple LangGraph interrupt example
make a toy tool-calling loop yourself
call Calendar API from a standalone script
```

This prevents framework magic from becoming a black box.

---

# 65. Learning Documents

At the end of each phase create:

```text
docs/learning/phase-XX.md
```

Without AI first, answer:

```text
What did I build?

Why is it designed this way?

What alternatives existed?

What information is canonical?

What is derived?

Where does state live?

What side effects occur?

What happens after process restart?

What can fail?

What does the LLM decide?

What does deterministic code decide?

Why is LangGraph useful here?

Could ordinary Python have worked?
```

This becomes your personal technical textbook.

---

# 66. Special LangGraph Learning Track

Treat LangGraph as its own curriculum.

## LG-1

```text
State
Node
Edge
START
END
```

## LG-2

```text
Conditional routing
Reducers
Commands
```

## LG-3

```text
Messages
Threads
Checkpointing
Persistence
```

## LG-4

```text
Interrupt
Resume
Human-in-the-loop
```

## LG-5

```text
Tool calling
ToolNode
ToolMessage
Agent loops
```

## LG-6

```text
Subgraphs
Retries
Failure recovery
Time travel/debugging
```

Do not progress to the next until you can implement a minimal toy version yourself.

---

# 67. Special Retrieval Learning Track

Similarly:

```text
grep/simple matching
↓
FTS/BM25
↓
chunking
↓
embeddings
↓
vector search
↓
hybrid retrieval
↓
metadata filtering
↓
query rewriting
↓
reranking
↓
retrieval evaluation
```

Avoid immediately installing a RAG framework that hides these concepts.

---

# 68. Special Agent Learning Track

Learn agentic systems in this order:

```text
deterministic workflow
↓
LLM classification
↓
conditional graph
↓
persistent state
↓
human interruption
↓
single tool
↓
multiple read tools
↓
side-effectful tools
↓
external research
↓
adaptive planning
```

This teaches what makes an agent different from an ordinary LLM call.

---

# 69. First Major Product Milestone — Steward Memory

Steward is useful when:

```text
I manually edit Markdown.

I send PDFs/text through Telegram.

Steward preserves them.

I ask vaguely:
"What was that thing about X?"

Steward retrieves the right source.

I ask follow-ups.

Steward maintains context.

I ask:
"Where did that come from?"

Steward shows evidence.

I say:
"Don't keep the last thing."

It behaves correctly.
```

Stop here and actually use it.

Collect frustrations before designing more.

---

# 70. Second Major Milestone — Steward Organizer

Steward becomes organizationally intelligent.

```text
Incoming material
      ↓
understand
      ↓
existing workspace?
      ↓
organization proposal
```

It can say:

> This looks like part of Steward.

or:

> This doesn't fit your current workspaces. It seems like the beginning of a new compiler project.

User approves.

Organization changes safely.

---

# 71. Third Major Milestone — Steward Knowledge

Steward now remembers information across sources.

Example:

```text
Redis source
+
distributed systems note
+
job scheduler design
```

Steward understands shared concepts:

```text
retries
idempotency
queues
delivery semantics
```

Questions can traverse Workspaces and global Knowledge.

---

# 72. Fourth Major Milestone — Steward Records

Upload an itinerary.

Later:

> What time is my flight?

Steward answers from the structured record while retaining the ticket as evidence.

---

# 73. Fifth Major Milestone — Steward Acts

> Add that flight to my calendar.

Steward:

```text
resolves reference
retrieves record
checks source
checks Calendar
constructs event
executes allowed tool
stores external ID
logs activity
```

This is when Steward becomes a true personal assistant rather than only a memory system.

---

# 74. Sixth Major Milestone — Steward Connects

Steward begins identifying:

```text
related projects
reusable concepts
previous decisions
contradictory sources
emerging workspaces
interesting connections
```

But it proposes rather than silently rewrites.

---

# 75. Suggested First Coding Sprint

Do not touch LangGraph yet.

Tasks:

```text
1. Initialize Steward repository.
2. Configure application paths.
3. Define Source model.
4. Initialize SQLite.
5. Implement SourceRepository.
6. Implement SHA-256 hashing.
7. Scan configured Markdown root.
8. Implement SourceFragment.
9. Implement MarkdownExtractor.
10. Persist fragments.
11. Add FTS5.
12. Add CLI scan.
13. Add CLI search.
14. Create retrieval evaluation cases.
15. Test re-scanning modified files.
```

That is the first sprint.

---

# 76. Suggested Second Sprint

```text
1. Define SemanticIndex interface.
2. Define EmbeddingProvider interface.
3. Generate embeddings.
4. Implement semantic search.
5. Compare semantic vs lexical retrieval.
6. Implement HybridRetriever.
7. Evaluate Recall@5.
8. Tune chunking.
```

Still no LangGraph.

---

# 77. Suggested Third Sprint

```text
1. Add ModelGateway.
2. Build grounded AnswerService.
3. Add source citations.
4. Evaluate unsupported claims.
5. Create smallest LangGraph.
6. Rewrite Q&A workflow using graph.
7. Inspect graph state manually.
8. Add persistent checkpointer.
```

Now you are learning LangGraph with a system whose components you already understand.

---

# 78. Decisions I Would Keep Open Initially

Do not lock these prematurely:

```text
vector database
embedding model
cloud LLM vendor
local LLM runtime
full folder taxonomy
knowledge ontology
claim granularity
record taxonomy
reranker
knowledge graph database
```

Create interfaces only when there are at least two plausible implementations you actually expect to swap.

Don't abstract merely because something could theoretically change.

---

# 79. Decisions I Would Lock Initially

Defaults:

```text
single-user
local Python process
Telegram long polling
SQLite metadata
existing Markdown remains where it is
Inbox exists
original files preserved
LLM provider hidden behind gateway
filesystem roots whitelisted
no arbitrary shell access
no automatic destructive mutation
pytest
Git
```

---

# 80. Open Questions Before Starting

These do not block the overall architecture, but they should be decided during Phase 0.

## Q1 — Operating system

Steward will primarily run on:

```text
Windows
macOS
Linux
```

This affects path handling, background execution and filesystem watching.

Default assumption: write cross-platform Python and avoid OS-specific features initially.

## Q2 — Existing Markdown vault

Determine:

```text
one root?
multiple roots?
approximate file count?
approximate total size?
attachments mixed with notes?
```

This affects retrieval experiments.

## Q3 — File mutation philosophy

Recommended initial policy:

```text
Steward may freely create files inside its own Inbox.

Moving/renaming existing user-authored files requires approval.

Steward never rewrites existing Markdown without explicit request.
```

## Q4 — LLM providers

Keep Steward provider-neutral.

During development use whichever model you already have reliable access to.

Do not design model routing yet.

## Q5 — Embeddings

Prefer local embeddings if performance is acceptable, because retrieval may expose large parts of your knowledge.

But benchmark before committing.

## Q6 — Telegram privacy

Remember that Telegram itself is an external system.

The local-first guarantee applies to Steward's storage/runtime, not necessarily transport.

Later consider whether extremely sensitive sources should be captured through a local CLI/UI instead.

---

# 81. Definition of Done for Every Feature

A feature is not finished merely because it works once.

Every feature should answer:

```text
Does it have tests?

Can I explain the control flow?

Can I explain its state?

Can I explain its failure behavior?

Is provenance preserved?

Is it idempotent where necessary?

Can it be debugged?

Are side effects controlled?

Does it violate any Steward invariant?
```

---

# 82. Steward's Core Invariants

These belong in `docs/invariants.md`.

### 1. Originals are sacred.

Steward never silently replaces original evidence with AI-generated interpretations.

### 2. Provenance is preserved.

Important derived information should be traceable to evidence.

### 3. The assistant may be uncertain.

Inbox and “I don't know” are valid outcomes.

### 4. Filesystem and semantic organization are different.

One physical home can have many conceptual relationships.

### 5. Organization evolves.

Steward may propose new structure when existing structure no longer fits.

### 6. LLMs propose; code executes.

Consequential side effects happen through controlled deterministic services.

### 7. External retrieval is not automatically permanent.

Research must deliberately cross the retention boundary.

### 8. Conversation is not automatically knowledge.

Short-term context remains separate from durable Knowledge.

### 9. Mutations are auditable.

Important changes produce Activity.

### 10. Destructive actions should be reversible where practical.

### 11. LangGraph orchestrates Steward.

Steward's domain logic must not depend unnecessarily on LangGraph.

### 12. External systems remain authoritative about their live state.

Calendar state comes from Calendar when freshness matters.

### 13. Derived state should be rebuildable whenever practical.

### 14. Agent capabilities follow least privilege.

No unrestricted filesystem or shell tools.

### 15. Complexity must earn its existence.

Every agent, node, database, abstraction and service should solve a real requirement.

### 16. Understanding the implementation is part of the definition of done.

If I cannot explain an important subsystem, Steward is not finished.

---

# 83. Final Product Vision

Eventually Steward should understand four questions about almost everything it encounters:

```text
WHAT IS THIS?

WHERE DOES IT BELONG?

HOW DOES IT RELATE TO WHAT I ALREADY HAVE?

WHAT CAN I SAFELY DO WITH IT?
```

And it should answer four questions for me:

```text
WHAT DO I HAVE?

WHAT DID I KNOW / DECIDE / DO?

WHERE DID IT COME FROM?

WHAT SHOULD I DO WITH IT NOW?
```

That is the end-state.

Not an autonomous swarm of AI agents.

Not a giant RAG demo.

Not merely an academic knowledge tracker.

**Steward is a persistent, local-first information steward that gradually becomes a tool-using personal assistant because it understands the information, contexts and history that I have intentionally entrusted to it.**
