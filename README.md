# inderes-research-agent

A multi-agent stock-research conversation system for Nordic equities. Built on **Microsoft Agent Framework 1.0+** (Python 3.11+), powered by **Google Gemini** with primary→fallback model selection, querying **Inderes MCP** at `https://mcp.inderes.com`.

```bash
$ python -m inderes_agent "Mitä Sammon nykytilanteesta tulisi ajatella?"
```

A lead orchestrator classifies the question, fans out to 1–4 specialized subagents
(quant, research, sentiment, portfolio) running in parallel, each making targeted
calls into Inderes MCP and returning structured findings. The lead then synthesizes
a single answer in the same language as the question. Every run is persisted to disk
as a forensic record (routing decision, per-subagent outputs, full tool-call timeline).

The system **surfaces signals** — Inderes' own recommendation, target price, insider
activity, analyst notes, forum sentiment — and **never** issues a buy/sell call of
its own. The user makes the decision; the agent shows them the data.

> **Documentation map**
> - [`ARCHITECTURE.md`](ARCHITECTURE.md) — design, components, lifecycle, key decisions
> - [`AGENT_FRAMEWORK.md`](AGENT_FRAMEWORK.md) — Microsoft Agent Framework primer + which features we use
> - [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — every error encountered, with fix
> - [`CHANGELOG.md`](CHANGELOG.md) — version history and design rationale
> - [`CONTRIBUTING.md`](CONTRIBUTING.md) — developer setup, testing, extending
> - [`BUILD_SPEC.md`](BUILD_SPEC.md) — original build specification (historical)

---

## Table of contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Per-run logs](#per-run-logs)
- [Architecture at a glance](#architecture-at-a-glance)
- [Configuration](#configuration)
- [Cost and quotas](#cost-and-quotas)
- [Limitations and non-goals](#limitations-and-non-goals)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Pre-flight checklist](#pre-flight-checklist)

---

## What it does

A natural-language interface to the Inderes Premium dataset. Ask about a Nordic
stock in Finnish or English; get a structured, source-cited answer:

**Example query:**
```
Mikä on Sammon P/E ja Inderesin näkemys?
```

**Example output (abbreviated):**
```
Sammon P/E-luku vuoden 2025 päätteeksi oli 12,53. Inderesin ennusteiden
perusteella arvostuskertoimien odotetaan asettuvan vuosina 2026–2028 noin
14,5–17,9 välille. Yhtiö on hinnoiteltu linjassa pohjoismaisiin verrokkeihin.

Inderesin näkemys: LISÄÄ (INCREASE), tavoitehinta 10,00 €

• Arvostus: tuotto-odotus nojaa tuloskasvuun ja ~4 %:n osinkotuottoon
• Operatiivinen suoritus: vakuutusliiketoiminta vahva
• Ennusteet: oikaistu P/E 17,9 (2026e), 15,8 (2027e), 14,5 (2028e)

Sources: search-companies, get-fundamentals, get-inderes-estimates,
         list-content, get-content
```

The same query also produces a complete trace at `~/.inderes_agent/runs/<ts>/`
including a human-readable `narrative.md` with the routing decision, per-tool-call
timeline, each subagent's full output, and the lead's synthesis.

Typical query latency: 8–25 seconds depending on complexity (single-domain → fast,
multi-domain comparison → slower due to per-company fan-out).

---

## Quick start

### Prerequisites

| Requirement | Details |
|---|---|
| **Python 3.11+** | We use 3.13 in development. ARM-native interpreter required on Apple Silicon (see [TROUBLESHOOTING](TROUBLESHOOTING.md#imports-take-30-60-seconds-on-apple-silicon)). |
| **`uv`** | Fast Python package manager. Install: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Disk space** | At least 20 GB free — APFS becomes unstable above ~90 % capacity. |
| **Inderes Premium** | Required for MCP server access. Subscribe at [inderes.fi/premium](https://www.inderes.fi/premium). |
| **Gemini API key** | Free at [aistudio.google.com](https://aistudio.google.com). Paid tier strongly recommended for sustained use. |

### Install

```bash
git clone <repo-url>
cd inderes-research-agent

uv python install 3.13
uv venv --python-preference only-managed --python 3.13
source .venv/bin/activate
uv pip install --pre -e .
```

The `--pre` flag is mandatory: `agent-framework-gemini` is currently published as
an alpha pre-release on PyPI. The `--python-preference only-managed` flag ensures
`uv` uses its own bundled Python builds, which are always native to your CPU
architecture (avoids the Intel-on-Apple-Silicon performance pitfall).

### Configure

```bash
cp .env.example .env
# edit .env, set GEMINI_API_KEY=AIza...
```

Get the key from [Google AI Studio](https://aistudio.google.com/app/apikey). The
default model configuration (free-tier-realistic Gemini Flash Lite + Flash) works
on the free tier with caveats — see [Cost and quotas](#cost-and-quotas).

### First run

```bash
python -m inderes_agent "What's Konecranes' current P/E?"
```

The first run opens a browser to log in to Inderes (OAuth 2.0 + PKCE against
Keycloak). After login, your access tokens are cached at
`~/.inderes_agent/tokens.json` and refreshed silently on subsequent runs.

Cold start (imports + OAuth + first MCP call) takes about 10–20 s. Once everything
is warm, simple queries return in 8–10 s, multi-domain queries 15–25 s.

---

## Usage

### One-shot

```bash
python -m inderes_agent "Compare Sampo and Nordea on profitability"
```

### Interactive REPL

```bash
python -m inderes_agent
> Anna pikakatsaus Konecranesista.
> Entä insider-aktiivisuus?
> /explain
> /exit
```

The REPL keeps conversation context — follow-up questions like "and the dividend
yield?" inherit the company from the previous turn.

### REPL slash commands

| Command | Action |
|---|---|
| `/help` | List commands |
| `/clear` | Reset conversation history |
| `/agents` | Show subagents invoked this session |
| `/trace` | Show last query's subagent outputs and which Gemini model handled each |
| `/explain` | Print a human-readable narrative of the last run |
| `/last` | Print the directory of the last run's full log |
| `/runs` | List the 10 most recent run directories |
| `/exit` | Quit |

### Programmatic use

```python
import asyncio
from inderes_agent.cli.repl import ConversationState, handle_query

async def main():
    state = ConversationState()
    await handle_query("Mikä on Konecranesin P/E?", state)

asyncio.run(main())
```

See [`examples/`](examples/) for a single-question script and a multi-turn
conversation example.

---

## Per-run logs

Every query writes a complete forensic record to `~/.inderes_agent/runs/<timestamp>/`:

```
20260501-205122-776/
├── query.txt              # the user's question
├── routing.json           # which subagents the router picked, plus reasoning
├── subagent-01-quant.json
├── subagent-02-research.json
├── subagent-03-sentiment.json
├── synthesis.txt          # lead's final synthesized answer
├── meta.json              # duration, fallback events, error counts
├── console.log            # raw HTTP/MCP/fallback log lines with timestamps
└── narrative.md           # human-readable timeline (auto-generated)
```

`narrative.md` is the single best file to inspect afterward. It includes:

1. **Routing decision** with the router's reasoning
2. **Tool-call timeline** with offsets and per-call duration, attributed by agent
3. **Each subagent's full output** (the structured response it returned to the lead)
4. **Lead's synthesis** (what the user saw)
5. **Statistics footer** — agents · tool calls · errors · 503 retries · fallbacks · total duration

You can regenerate the narrative for any past run via:

```bash
python scripts/explain.py                       # latest run
python scripts/explain.py 20260501-205122-776   # specific run
```

In the REPL: `/explain` does the same for the current session's last run.

---

## Architecture at a glance

```
                      User question
                            │
                            ▼
                    ┌─────────────┐
                    │  Router LLM │  Gemini, structured-output JSON
                    └─────┬───────┘
                          │
                ┌─────────┼─────────┬──────────┐
                ▼         ▼         ▼          ▼
           aino-quant  aino-research  aino-sentiment  aino-portfolio
                │         │         │          │
                └─────────┴─────────┴──────────┘
                          │
                          ▼     bounded by MAX_CONCURRENT_AGENTS
                    Inderes MCP (16 tools, partitioned)
                          │
                          ▼
                    ┌─────────────┐
                    │  aino-lead  │  reads subagent outputs, synthesizes
                    └─────┬───────┘
                          ▼
                     Final answer
```

### Subagent → MCP tool mapping

| Agent | Role | MCP tools |
|---|---|---|
| `aino-quant` | Numerical analysis: P/E, ROE, target prices, recommendations | `search-companies`, `get-fundamentals`, `get-inderes-estimates` |
| `aino-research` | Inderes' analyst content, transcripts, filings | `search-companies`, `list-content`, `get-content`, `list-transcripts`, `get-transcript`, `list-company-documents`, `get-document`, `read-document-sections` |
| `aino-sentiment` | Insider trades, forum, calendar | `search-companies`, `list-insider-transactions`, `search-forum-topics`, `get-forum-posts`, `list-calendar-events` |
| `aino-portfolio` | Inderes' own model portfolio | `get-model-portfolio-content`, `get-model-portfolio-price`, `search-companies` |
| `aino-lead` | Synthesizes subagent outputs (no tools) | — |

Each subagent only sees its allowed subset (enforced via `MCPStreamableHTTPTool(allowed_tools=...)`).

For a full architectural walkthrough including the OAuth flow, the schema-sanitization
shim, the Gemini fallback wrapper, and per-tool-call observability, see
[`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Configuration

All configuration lives in `.env`:

```ini
# Required
GEMINI_API_KEY=               # https://aistudio.google.com/app/apikey

# Inderes MCP — change only if Inderes provides a new endpoint
INDERES_MCP_URL=https://mcp.inderes.com
INDERES_MCP_CLIENT_ID=inderes-mcp

# Models (free-tier-safe defaults)
PRIMARY_MODEL=gemini-3.1-flash-lite-preview
FALLBACK_MODEL=gemini-2.5-flash

# Fallback timing (see ARCHITECTURE.md "Gemini client with fallback")
RETRY_DELAY_MS=1000
MAX_RETRIES=1

# Concurrency cap (protects against quota burn)
MAX_CONCURRENT_AGENTS=2

# Logging
LOG_LEVEL=INFO
LOG_JSON=false
```

`.env` is gitignored. `.env.example` contains the same template with no key set.

---

## Cost and quotas

The system is designed to work on Google's free Gemini tier, but free-tier limits are
tight enough that sustained use requires the paid tier.

### Free tier daily limits (per Google AI Studio)

| Model | Requests/day |
|---|---|
| `gemini-3.1-flash-lite-preview` (primary) | 500 |
| `gemini-2.5-flash` (fallback) | 20 |

A single multi-domain query uses 6–15 LLM calls (router + N subagents with their
internal tool-call loops + lead synthesis). Realistic free-tier capacity is roughly
30–50 queries/day before hitting limits.

### Paid tier (recommended)

Enable billing at [Google AI Studio billing](https://aistudio.google.com/app/apikey).
Per-query cost is approximately:

- **Single-domain** (e.g. quant only): ~$0.005
- **Multi-domain** (3 subagents): ~$0.015–0.02
- **Comparison fan-out**: ~$0.03–0.05

A $10 budget covers 500–2000 queries depending on complexity. Paid tier also
substantially reduces 503 capacity errors. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#503-errors-from-gemini)
for details.

### Fallback policy

The system survives transient capacity issues automatically:

1. First attempt → primary model
2. On `503 UNAVAILABLE` → wait `RETRY_DELAY_MS`, retry once on primary
3. On second 503 OR any `429 RESOURCE_EXHAUSTED` → switch to fallback model
4. Fallback gets two attempts with 2 s and 4 s backoff
5. Both exhausted → `QuotaExhaustedError` with a clear message to the user

The model that handled each request is recorded for `/trace` and `narrative.md`.

---

## Limitations and non-goals

This system explicitly does **not**:

- Recommend "buy" or "sell" as its own opinion. It surfaces Inderes' recommendation
  on a separate, clearly attributed line. The user decides.
- Replace investment advice. It is a research surface, not an advisor.
- Predict prices. It quotes Inderes' estimates and analyst views without forecasting.
- Use Pro-tier Gemini models on the free tier (zero quota allocation).
- Hardcode company IDs — every workflow starts with `search-companies(name)` to
  resolve the Inderes-internal ID.

It is also **not** a peer-to-peer agent network. Subagents do not communicate with
each other; the lead reads each one's output as text in its prompt. See
[ARCHITECTURE.md → "What this is and isn't"](ARCHITECTURE.md#what-this-is-and-isnt)
for honest framing.

---

## Testing

```bash
uv pip install -e '.[dev]'
pytest -q
```

Thirteen unit tests cover:

- Router JSON parsing (with code fences, prose leaks, plain JSON)
- `QueryClassification` Pydantic validation
- Fallback client semantics: 503 retry → fallback model, 429 → `QuotaExhaustedError`, success path
- Workflow fan-out: per-company branching for comparisons, no fan-out for single-domain, concurrency cap

End-to-end tests against the real Gemini API and Inderes MCP are not in CI (they
require live credentials and consume quota). Run them manually:

```bash
python -m inderes_agent "What's Konecranes' current P/E?"
```

The diagnostic `python scripts/diag.py` independently probes Gemini and MCP with
per-step timing — useful when something is hanging.

---

## Project layout

```
src/inderes_agent/
├── __main__.py        # entry point; prefetches OAuth token before async work
├── settings.py        # pydantic-settings env loader
├── logging.py         # structlog setup
├── agents/
│   ├── lead.py        # aino-lead orchestrator (no tools)
│   ├── quant.py       # aino-quant
│   ├── research.py    # aino-research
│   ├── sentiment.py   # aino-sentiment
│   ├── portfolio.py   # aino-portfolio
│   └── prompts/       # system prompts for each agent (markdown)
├── llm/
│   └── gemini_client.py    # FallbackGeminiChatClient
├── mcp/
│   ├── inderes_client.py   # MCP tool factory + schema sanitization
│   └── oauth.py            # OAuth Authorization Code + PKCE flow
├── orchestration/
│   ├── router.py      # query classification (structured-output Gemini call)
│   ├── workflows.py   # asyncio.gather + semaphore fan-out
│   └── synthesis.py   # lead synthesis
├── cli/
│   ├── repl.py        # interactive mode + slash commands
│   └── render.py      # rich-formatted output
└── observability/
    ├── tracing.py     # OpenTelemetry tracer
    ├── run_log.py     # per-run directory writer
    └── narrate.py     # narrative.md generator

scripts/
├── diag.py            # standalone Gemini + MCP connectivity diagnostic
└── explain.py         # regenerate narrative.md for any past run

tests/                 # pytest unit tests
examples/              # programmatic-use samples
```

Module-level explanations in [ARCHITECTURE.md](ARCHITECTURE.md). Build rationale
in [`BUILD_SPEC.md`](BUILD_SPEC.md).

---

## Pre-flight checklist

Before your first real run:

- [ ] Apple Silicon? Verify `python -c "import platform; print(platform.machine())"` prints `arm64`. If `x86_64`, recreate venv per [TROUBLESHOOTING](TROUBLESHOOTING.md#imports-take-30-60-seconds-on-apple-silicon).
- [ ] `uv pip install --pre -e .` succeeded without errors.
- [ ] At least 20 GB free disk space (`df -h .`).
- [ ] `GEMINI_API_KEY` set in `.env`.
- [ ] Inderes Premium subscription active.
- [ ] Running interactively (not in headless CI / SSH) — first run opens a browser for OAuth.
- [ ] Tests pass: `pytest -q`.
