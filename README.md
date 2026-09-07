# Steward

Steward is a local-first personal memory, knowledge, and action assistant. It preserves original sources, builds evidence-backed knowledge, and only takes consequential actions through controlled services.

## Current status

Phases 0–23 are implemented as a local foundation. Steward can capture text,
Markdown, plain text, and native-text PDFs into an Inbox; extract and retrieve
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

`steward agent` is Steward's first tool-calling loop. Gemini can decide whether
to search or read local Steward data, LangGraph executes only the six supplied
read-only tools, then Gemini receives the tool results before answering.

```powershell
steward agent "What did I save about address translation?"
```

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

## Research external sources without retaining them

Use Gemini's search grounding only when local evidence is insufficient. Results
remain ephemeral and are not copied into your vault automatically.

```powershell
steward research "How does Linux perform TLB shootdowns?"
```

## Ask through Telegram

Create a bot with BotFather, put its token in your private `.env`, and start
the local polling process. Send `/save` as a message to capture its text, or
use `/save` as the caption on a Markdown, text, or PDF attachment. Captured
material is preserved in the configured Inbox before extraction and indexing.

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
```

```powershell
steward telegram
```

Stop the local process with `Ctrl+C`. Long polling means this initial version
does not need a public webhook endpoint.

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
