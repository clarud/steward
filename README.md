# Steward

Steward is a local-first personal memory, knowledge, and action assistant. It preserves original sources, builds evidence-backed knowledge, and only takes consequential actions through controlled services.

## Current status

Phase 7 is complete. Steward can register and structurally extract Markdown,
retrieve its fragments, generate grounded answers from explicitly retrieved
evidence, and orchestrate the retrieve-to-answer workflow with LangGraph. It
can also answer isolated text questions sent to a local Telegram bot through
long polling.

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

## Ask through Telegram

Create a bot with BotFather, put its token in your private `.env`, and start
the local polling process. This phase supports text questions only; it does not
yet capture Telegram messages or attachments as durable sources.

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
