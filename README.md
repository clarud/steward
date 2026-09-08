# Steward

Steward is a local-first personal memory, knowledge, and action assistant. It preserves original sources, builds evidence-backed knowledge, and only takes consequential actions through controlled services.

## Current status

Phases 0–34 are implemented as a local foundation. Steward can capture text,
Markdown, plain text, DOCX, HTML, images with optional local OCR, and native-text PDFs into an Inbox; extract and retrieve
fragments; create workspaces and organization proposals; retain activity,
concept, claim, and travel-record provenance; and answer Telegram questions
with persistent per-chat LangGraph state. The human approval and external
action layers are still intentionally narrow. It can now also run an explicit
Gemini-powered, read-only LangGraph tool loop over sources, knowledge, records,
workspaces, and activity.
Every model-callable tool now declares its risk and approval requirements;
only read-only tools are currently exposed to the agent.
Google Calendar can now be connected through local OAuth for current read-only
event search and lookup.
Travel records can be created as idempotent, audited Calendar events after
explicitly invoking the write command.
External research is available as an explicitly invoked, non-retaining flow.
It can also review Inbox filenames and propose possible new workspace themes
without creating or moving anything automatically.
It can propose evidence-backed connections between concepts without turning
co-occurrence into permanent knowledge automatically.
Manual Markdown edits can be watched and incrementally refreshed locally.
Model privacy now routes restricted evidence to a configured local Ollama model
or refuses it safely when none is available. Calendar creation reconciles a
prior remote event by its Steward idempotency key after an interrupted local
write, avoiding a duplicate event on retry.
Structured local logs make retrieval graph routes, result IDs/counts, model
calls, and tool requests inspectable without recording source text or prompts.
The repository also includes versioned retrieval and product evaluation cases
for regression checks across retrieval, organization, records, knowledge, and
agent safety.

## Local setup

Requires Python 3.12 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
steward scan path\to\your\vault
```

Copy `.env.example` to `.env` only when you need local configuration. Never commit `.env`.

## Search a vault

Lexical search needs only the scan:

```powershell
steward scan path\to\your\vault
steward search "address translations"
```

Semantic and hybrid search use a local embedding model. Download it explicitly
once, then build the rebuildable local vector index. Subsequent indexing and
search run from the local model cache.

```powershell
steward download-embedding-model
steward index path\to\your\vault
steward semantic-search "the little cache CPUs use for address translation"
steward hybrid-search "the little cache CPUs use for address translation"
```

## Ask from local evidence

Gemini is the default provider. Set its API key and a Gemini model available to
your account in your PowerShell session, then ask a question. The answer request
uses only retrieved fragments and asks the API not to store the interaction.

```powershell
$env:GEMINI_API_KEY = "your-api-key"
$env:STEWARD_GEMINI_MODEL = "your-selected-model"
steward ask "What do I know about address translation?"
```

## Ask with read-only tools

`steward agent` is Steward's first tool-calling loop. Gemini or a configured
local Ollama model can decide whether to search or read local Steward data.
LangGraph executes supplied read-only tools plus narrowly defined proposal-only
tools, then returns their results to the same model before it answers.

For an explicit workspace-creation request, the agent may use its one
proposal-only write tool. That creates a pending proposal, never the workspace
itself. Inspect or decide it explicitly:

```powershell
steward action-proposals
steward review-action-proposal 1 accepted
```

```powershell
steward agent "What did I save about address translation?"
```

For Ollama, install and run Ollama, pull a model that supports tool calling,
then set the following private `.env` values before starting a new terminal:

```text
STEWARD_MODEL_PROVIDER=local
STEWARD_LOCAL_MODEL=qwen3
# Optional when Ollama uses its default local endpoint:
STEWARD_LOCAL_MODEL_URL=http://127.0.0.1:11434
```

Tool support is a model capability, not a guarantee of reliable planning. A
small local model may answer directly, repeat a lookup, or emit invalid
tool-like text; Steward executes only valid calls to its allowlisted tools and
enforces its tool-call budget regardless of provider.

Use `--thread-id` to continue a tool-agent conversation through the local
LangGraph checkpoint store:

```powershell
steward agent --thread-id research:tlb "What sources discuss TLBs?"
```

## Connect Google Calendar for reads

Create a Google OAuth **desktop application** client, download its client JSON
outside the repository, then authorize it locally. The resulting refreshable
token is stored under `.steward/config/`, not in Git.

```powershell
steward calendar-authorize C:\private\google-oauth-client.json
$env:STEWARD_GOOGLE_CLIENT_SECRETS = "C:\private\google-oauth-client.json"
steward calendar-search "Tokyo"
steward calendar-get GOOGLE_EVENT_ID
```

## Search Google Drive metadata

Authorize the separate, read-only Drive metadata scope once. This searches
current Drive file metadata and links; it does not download or retain content.

```powershell
steward drive-authorize C:\private\google-oauth-client.json
$env:STEWARD_GOOGLE_CLIENT_SECRETS = "C:\private\google-oauth-client.json"
steward drive-search "itinerary"
```

Use Calendar reads in the tool agent only when requested explicitly:

```powershell
steward agent --include-calendar "What is on my calendar when I arrive in Tokyo?"
```

## Create a Calendar event from a travel record

This requests the broader Google Calendar event scope. Re-run authorization if
your existing token was read-only. Repeating the command for the same record
returns the existing linked event rather than creating a duplicate.

```powershell
steward calendar-create-travel-event 1
```

## Add and inspect source-backed travel references

Keep additional booking identifiers or links traceable to an extracted source
fragment. The reference is not accepted unless that fragment exists locally.

```powershell
steward add-travel-record-reference 1 booking_portal "https://example.com/booking/ABC" 12
steward travel-record-references 1
```

## Research external sources without retaining them

Use Gemini's search grounding only when local evidence is insufficient. Results
remain ephemeral and are not copied into your vault automatically.

```powershell
steward research "How does Linux perform TLB shootdowns?"
```

To deliberately retain the resulting answer and its external URLs in your
Inbox as a labeled Markdown research note, use:

```powershell
steward research-retain "How does Linux perform TLB shootdowns?"
```

This preserves a research note, not copies of the cited webpages.

## Review potential new workspaces

This intentionally reviews rather than changes your structure. It clusters
repeated meaningful filename terms among Inbox sources and prints pending
candidates for you to evaluate.

```powershell
steward review-inbox-workspaces
```

## Ask for an organization proposal using source content

This remains a proposal: the configured model can choose only an existing
workspace, while Steward validates the JSON response and derives any target
path itself. You still review the proposal before a file moves.

```powershell
steward propose-organization SOURCE_ID --model-assisted
```

## Review knowledge connections

Connections are candidates supported by shared source fragments. Steward shows
the evidence IDs and explicitly notes that shared evidence is not causation.

```powershell
steward connect-knowledge
```

## Watch a vault for Markdown edits

Filesystem events are debounced, then Steward re-hashes the file before doing
any extraction work. Press `Ctrl+C` to stop the foreground watcher.

```powershell
steward watch path\to\your\vault
```

## Set source privacy before model use

```powershell
steward set-source-privacy 12 local_model_only
steward source-privacy 12
```

The cloud-answer and cloud tool-agent paths enforce this boundary before they
place source text or source-derived travel record fields in a model prompt.
`external_redacted` is deliberately withheld until Steward has an actual,
auditable redaction feature; a label alone cannot protect data.

If you run a local Ollama model, Steward can route `local_model_only` and
`external_redacted` source evidence to it instead of a cloud provider:

```dotenv
STEWARD_LOCAL_MODEL=llama3.2
STEWARD_LOCAL_MODEL_URL=http://127.0.0.1:11434
```

Set `STEWARD_MODEL_PROVIDER=local` to make Ollama the default answer model;
otherwise it is selected only when retrieved evidence requires local handling.

## Ask through Telegram

Create a bot with BotFather, put its token in your private `.env`, and start
the local polling process. Send `/save` as a message to capture its text, or
use `/save` as the caption on a Markdown, text, DOCX, HTML, image, or PDF attachment. Captured
material is preserved in the configured Inbox before extraction and indexing.
If an uploaded filename strongly matches an existing workspace, Steward sends an
organization proposal and waits for an explicit `accept` or `reject` reply
before moving the original file. Uncertain captures remain in Inbox without
blocking the chat.

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
```

```powershell
steward telegram
```

Stop the local process with `Ctrl+C`. Long polling means this initial version
does not need a public webhook endpoint. Steward records successfully replied
Telegram update IDs in its local SQLite database, so a redelivered update is
not handled twice; a failed delivery is left eligible for retry.

An allowlisted Telegram chat can review pending workspace proposals created by
`steward agent`:

```text
/action_proposals
/approve_action 1
/reject_action 1
```

These commands are intentionally exact and explicit. An approved proposal is
executed by normal deterministic services, not by the model. Treat every chat
in `STEWARD_TELEGRAM_ALLOWED_CHAT_IDS` as an administrator while this first
single-user approval model is in place.

OpenAI remains available by explicitly selecting its provider:

```powershell
$env:STEWARD_MODEL_PROVIDER = "openai"
$env:OPENAI_API_KEY = "your-api-key"
$env:STEWARD_OPENAI_MODEL = "your-selected-model"
steward ask "What do I know about address translation?"
```

## Documentation

- `docs/developer-guide.md` — implementation, data flow, limitations, and next steps

- `docs/product.md` — product intent
- `docs/architecture.md` — architectural boundaries
- `docs/invariants.md` — rules every feature must preserve
- `docs/adr/` — records of foundational decisions
