# Architecture

A deep walkthrough of `inderes-research-agent`: what each piece does, why it exists,
and how the pieces fit together. Read this if you intend to extend the system,
debug it, or understand the design rationale beyond what `README.md` covers.

## Table of contents

- [Mental model](#mental-model)
- [Component map](#component-map)
  - [Orchestration](#orchestration)
  - [Agents](#agents)
  - [Inderes MCP integration](#inderes-mcp-integration)
  - [Gemini client with fallback](#gemini-client-with-fallback)
  - [CLI](#cli)
  - [Observability](#observability)
- [Run lifecycle](#run-lifecycle)
- [Key design decisions](#key-design-decisions)
- [What this is and isn't](#what-this-is-and-isnt)
- [Extending the system](#extending-the-system)

---

## Mental model

A user asks a natural-language question about a Nordic stock. A **router** classifies
it. A **fan-out workflow** spawns 1–4 **specialized subagents** in parallel, each
with its own focused subset of Inderes MCP tools. Each subagent runs an LLM
tool-calling loop, gathers data from Inderes, and returns a structured text block.
A **lead** synthesizes those blocks into one final answer. Everything that happened
gets recorded to disk as a forensic record and a human-readable narrative.

```
User question
    │
    ▼
Router (Gemini, structured-output JSON)
    │
    ▼
Workflow (asyncio.gather + semaphore on MAX_CONCURRENT_AGENTS)
    │
    ├──→ aino-quant     ─┐
    ├──→ aino-research  ─┤  each: own chat client, own filtered MCP tool set,
    ├──→ aino-sentiment ─┤        own AFC tool-calling loop, own structured output
    └──→ aino-portfolio ─┘
                         │
                         ▼
              Lead synthesis (Gemini, no tools)
                         │
                         ▼
              Final answer + persisted run directory
```

A typical multi-domain query takes 8–25 seconds end-to-end. Single-domain queries
are faster (~5–10 s); per-company comparisons (multi-domain × multi-company) are
slower because of the concurrency cap.

---

## Component map

### Orchestration

Files: [`src/inderes_agent/orchestration/`](src/inderes_agent/orchestration/)

| File | Role |
|---|---|
| `router.py` | One Gemini call with a structured-output JSON prompt → `QueryClassification(domains, companies, is_comparison, reasoning)`. Few-shot examples in the prompt drive consistency. Includes tolerant JSON extraction (handles code fences, prose leaks). |
| `workflows.py` | Spawns subagents per the classification. `asyncio.Semaphore(MAX_CONCURRENT_AGENTS)` caps parallelism. For comparisons (N>1 companies × multi-domain non-portfolio), fans out per-company. Records `last_used_model` and any errors per subagent in `SubagentResult`. |
| `synthesis.py` | Builds a structured prompt from subagent outputs and feeds it to the lead agent. Returns `(answer_text, lead_model_used)`. |

**Why `asyncio.gather` instead of MAF's `ConcurrentBuilder`?**

Three reasons:

1. Per-company fan-out for comparisons must be decided at runtime.
2. Free-tier quota requires a hard concurrency cap (semaphore).
3. The orchestration logic is short enough that direct asyncio is more readable than wrapping it in MAF's builder API.

We retain MAF's per-agent infrastructure (`Agent` context manager, chat client, MCP
tool, AFC loop), just not its orchestration layer.

### Agents

Files: [`src/inderes_agent/agents/`](src/inderes_agent/agents/) and prompts in
[`agents/prompts/`](src/inderes_agent/agents/prompts/).

Five `agent_framework.Agent` instances built via factory functions. Each gets:

- A **system prompt** (`agents/prompts/<role>.md`) that tells it its role, available
  tools, recommended workflow, and required output format.
- A **chat client** (the `FallbackGeminiChatClient`).
- A **filtered MCP tool** (only the subset of Inderes tools relevant to its specialty).

| Agent | Purpose | MCP tools |
|---|---|---|
| `aino-quant` | Numerical analysis: P/E, ROE, target prices, recommendations | `search-companies`, `get-fundamentals`, `get-inderes-estimates` |
| `aino-research` | Qualitative content: analyst reports, transcripts, filings | `search-companies`, `list-content`, `get-content`, `list-transcripts`, `get-transcript`, `list-company-documents`, `get-document`, `read-document-sections` |
| `aino-sentiment` | Market signals: insider trades, forum, calendar | `search-companies`, `list-insider-transactions`, `search-forum-topics`, `get-forum-posts`, `list-calendar-events` |
| `aino-portfolio` | Inderes' own model portfolio | `get-model-portfolio-content`, `get-model-portfolio-price`, `search-companies` |
| `aino-lead` | Synthesizes subagent outputs (no tools, just an LLM call over a structured prompt) | — |

**Why partition tools per agent?** A monolithic agent with all 16 MCP tools would
have too many options at every reasoning step → degraded tool-call accuracy.
Splitting by responsibility keeps each LLM context tight and each system prompt
focused. This is the highest-leverage decision in the architecture.

**Prompt preamble — current date.** `_common.load_prompt()` prepends a
`# CURRENT DATE` header (ISO + Finnish weekday) to every loaded prompt before
returning it. Without this, Gemini falls back to its training cutoff when
asked "tänään" / "tällä viikolla" — observed answers from "14.5.2025" when
the real date was 2026-05-03. The same date is also prefixed onto every
per-query user prompt in `workflows.py` and `synthesis.py` (system
instructions can lose attention in long contexts; user-prompt prefix is
read first by the model). Router's inline `_ROUTER_INSTRUCTIONS` is
intentionally exempt — routing is a classification task that doesn't need
date context.

**Prompt preamble — thought traces.** Every subagent prompt mandates a
single-line `**Ajatus:** ...` reasoning rubric at the top of its response,
explaining which tools it'll call and why. LEAD mandates a
`**💭 Perustelut:** ...` callout at the top of every synthesis describing
how it combined the subagents' outputs. The Streamlit UI styles these
distinctly (violet for subagents, amber for LEAD), surfacing the
multi-agent reasoning to the user without burying it behind tool-call
logs.

### Inderes MCP integration

Files: [`src/inderes_agent/mcp/`](src/inderes_agent/mcp/)

| File | Role |
|---|---|
| `oauth.py` | OAuth 2.0 Authorization Code + PKCE flow against Inderes' Keycloak SSO. Discovers endpoints from `https://mcp.inderes.com/.well-known/oauth-protected-resource` (RFC 9728), then `https://sso.inderes.fi/auth/realms/Inderes/.well-known/openid-configuration`. Browser opens for first-time login; tokens cached at `~/.inderes_agent/tokens.json` with `0600` permissions. Refresh token used silently on subsequent runs. |
| `inderes_client.py` | Builds `MCPStreamableHTTPTool` per agent with: (a) `allowed_tools` filtering, (b) `httpx.AsyncClient(auth=...)` with `_InderesBearerAuth` for per-request token injection, (c) `_SanitizingMCPTool` subclass that strips `$schema` and other JSON-Schema metadata that Gemini's `FunctionDeclaration` rejects, (d) `load_prompts=False` because Inderes MCP exposes only tools and the default `prompts/list` call would crash with `Method not found`. |

**Why custom OAuth instead of MAF's auth?** MAF's `MCPStreamableHTTPTool` exposes
a `header_provider` callback for tool invocations, but **not** for the
connection-time `initialize` call. The 401 happens during `initialize`, before any
tool call. So we attach auth via `httpx.AsyncClient.auth=` instead — every request
including `initialize` carries the Bearer token.

**Why eagerly trigger OAuth in `__main__.py`?** The 4 subagents are built
concurrently inside `asyncio.gather`. If we let OAuth trigger inside one of them,
the other three might race to start their own OAuth flows (browser opening 4
times). Calling `prefetch_token()` once at startup ensures the browser opens
exactly once and all four agents share the cached token.

**JSON-Schema sanitization.** Inderes' MCP tool schemas include JSON-Schema
metadata (`$schema`, `$id`, `$ref`, `$defs`, `$comment`). Gemini's
`google.genai.types.FunctionDeclaration` Pydantic model has `extra='forbid'` and
rejects these. `_SanitizingMCPTool` overrides `connect()` and recursively strips
those keys from each tool's cached input schema.

### Gemini client with fallback

File: [`src/inderes_agent/llm/gemini_client.py`](src/inderes_agent/llm/gemini_client.py)

`FallbackGeminiChatClient` subclasses `agent_framework_gemini.GeminiChatClient` and
overrides `get_response`. Logic:

1. Try **primary** (`gemini-3.1-flash-lite-preview` by default)
2. On `503 UNAVAILABLE` → wait `RETRY_DELAY_MS`, retry primary once
3. On second 503 OR any `429 RESOURCE_EXHAUSTED` → switch to **fallback** (`gemini-2.5-flash`) for that single request
4. Fallback gets two more attempts with 2 s and 4 s backoff
5. If all attempts exhausted → raise `QuotaExhaustedError`

The model that handled each request is recorded on `self.last_used_model`. The
workflow reads this when building `SubagentResult` so the trace and `narrative.md`
show which model handled each subagent.

Streaming and non-streaming paths are dispatched separately (streaming is wired
but not currently used by the REPL — synthesis prints the full answer at once).

**Why subclass instead of MAF middleware?** MAF middleware operates at the agent
level. We need control at the chat-client level so every LLM call goes through the
fallback wrapper regardless of which agent makes it. Subclassing is the right
seam.

### CLI

Files: [`src/inderes_agent/cli/`](src/inderes_agent/cli/)

| File | Role |
|---|---|
| `repl.py` | Interactive REPL with conversation state, slash commands, inline progress reporting. `handle_query()` is the shared per-query entry point used by both REPL and one-shot mode. |
| `render.py` | rich-formatted output: routing summary, answer (markdown), error panels, compact subagent trace. |

The REPL maintains a `ConversationState` between turns. Two key continuity
behaviors:

1. The router gets a "previous turn discussed: X" hint via `_build_context()`.
2. If the new query lacks an explicit company name but the previous turn had one, the workflow inherits it. So "and the dividend yield?" implicitly means the same company as the previous turn.

### Observability

Files: [`src/inderes_agent/observability/`](src/inderes_agent/observability/)

| File | Role |
|---|---|
| `tracing.py` | OpenTelemetry tracer with `ConsoleSpanExporter`. MAF emits spans natively; this just wires up a tracer provider. |
| `run_log.py` | `attach_console_log_handler()` adds a `FileHandler` to the root logger so HTTP/MCP/fallback logs go to `<run_dir>/console.log`. `write_run()` dumps query, routing, per-subagent, synthesis, meta to JSON files in the run directory. |
| `narrate.py` | Reads a run directory and produces `narrative.md`: routing decision → tool-call timeline → per-subagent outputs → synthesis → stats footer. Tool-call attribution is by tool name (some tools like `search-companies` are shared and marked `[shared]`). |

Per-run output structure at `~/.inderes_agent/runs/<timestamp>/`:

```
20260501-205122-776/
├── query.txt              # plain text, the user's question
├── routing.json           # {domains, companies, is_comparison, reasoning}
├── subagent-NN-<domain>.json   # {index, domain, company, model_used, error, text}
├── synthesis.txt          # plain text, lead's final answer
├── meta.json              # {lead_model, duration_seconds, fallback_events, subagent_count, subagent_errors}
├── console.log            # HTTP/MCP/fallback log lines with ISO timestamps
└── narrative.md           # human-readable timeline (auto-generated post-run)
```

---

## Run lifecycle

A single `python -m inderes_agent "..."` invocation:

```
1. main()
   ├─ load_dotenv()
   ├─ configure_logging()
   ├─ setup_tracing()
   └─ prefetch_token()          ← OAuth flow if no cached token, else silent refresh

2. asyncio.run(_one_shot(question))
   └─ handle_query()
      ├─ new_run_dir()           ← creates ~/.inderes_agent/runs/<timestamp>/
      ├─ attach_console_log_handler()
      │
      ├─ classify_query()        ← Router LLM call → QueryClassification
      │     emit:  "→ domains: [...]  · companies: [...]"
      │
      ├─ run_workflow()
      │   For each (domain, company) pair, in parallel (capped by semaphore):
      │       Build chat client (FallbackGeminiChatClient)
      │       Build MCP tool (_SanitizingMCPTool with BearerAuth)
      │       async with Agent(...) as a:
      │           result = await a.run(prompt)
      │             [AFC loop]: Gemini decides tool → SDK calls MCP → result
      │             fed back → repeats until Gemini returns final text
      │       Capture text, model_used, errors → SubagentResult
      │     emit:  "·   <domain>: ok|ERROR (<model>)"
      │
      ├─ synthesize()            ← Lead LLM call over structured prompt of
      │                             subagent outputs → final answer text
      │
      ├─ render.render_answer()  ← print to user (rich markdown)
      │
      ├─ write_run()             ← persist query.txt, routing.json,
      │                             subagent-NN-*.json, synthesis.txt,
      │                             meta.json
      │
      ├─ write_narrative()       ← parse console.log + JSONs → narrative.md
      │
      └─ render compact trace + paths to logs

3. detach_console_log_handler() (in finally block, always)
```

If anything raises a `QuotaExhaustedError` or other exception, it's caught and
rendered in a red panel, and the per-run directory still gets the partial logs.

---

## Key design decisions

Each was made deliberately and is worth understanding before you change it.

### 1. Per-subagent LLM call with own tool subset

Trades concurrency cost for tool-call accuracy. A single agent with 16 tools makes
more wrong choices at every step than 4 agents with 3–8 tools each. The
multiplicative cost (4× LLM calls) is offset by fewer wasted iterations and
clearer per-agent prompts.

### 2. Lead has no tools

Forces the lead to synthesize from what subagents returned (text in prompt) rather
than re-querying. Keeps the lead fast, prevents duplicating MCP calls, and forces
the team's "knowledge" to flow through one bottleneck — the subagent output text.
This makes debugging much easier (you can read exactly what the lead saw).

### 3. Structured-output router instead of handoff/group-chat

Gemini's structured output is reliable and cheap. The classification is fully
deterministic given the prompt and few-shot examples. No need for a multi-turn
handoff dance. Could be replaced with a smarter classifier if requirements grow,
but for ~7 domain types the current approach works.

### 4. Custom OAuth flow with token cache

MAF doesn't bridge MCP OAuth to chat-client requests. We do the dance ourselves
with PKCE, cache tokens to `~/.inderes_agent/tokens.json`, and inject Bearer
per-request via `httpx.Auth`. The cache file is `0600` (owner-read-write only).

### 5. JSON-Schema sanitization wrapper

Inderes' MCP tool schemas include JSON-Schema metadata that Gemini's
`FunctionDeclaration` validator rejects. We subclass `MCPStreamableHTTPTool`,
intercept `connect()`, and strip those keys recursively. If Gemini relaxes its
validation in a future version, this can be removed.

### 6. `load_prompts=False` on every MCP tool

Inderes MCP doesn't implement `prompts/list`. With the default `load_prompts=True`,
MAF calls `prompts/list` during `initialize` and the server returns "Method not
found", crashing the connection. Disabling prompt loading sidesteps this.

### 7. Per-run directory of files (not a database)

A single SQLite or JSONL log would be more queryable, but per-run directories are
dramatically easier to read by hand, share by zipping, and grep through. Each run
is self-contained. Trade-off: large numbers of runs accumulate inodes.

### 8. `narrative.md` parses from `console.log`

The narrative needs per-tool-call timing, but recording that explicitly would
require MAF middleware on every agent. Parsing the existing `console.log`
(which captures `agent_framework`'s "Function name: X" / "Function X succeeded"
messages with timestamps) is simpler and works.

Limitation: tool-call attribution to a specific subagent is heuristic — a tool
like `search-companies` is allowed by multiple agents and gets marked `[shared]`
in the timeline. For unambiguous tools (`get-fundamentals`, `list-content`, etc.)
attribution is exact.

### 9. ARM-native Python required on Apple Silicon

Intel Python via Rosetta is ~10× slower for cold imports. The README and
TROUBLESHOOTING both call this out; the install instructions use `uv` with
`--python-preference only-managed` to guarantee an ARM-native interpreter.

### 10. Disk space matters

Below ~10 % free disk on macOS, APFS becomes unstable: failed `mmap` calls,
truncated reads, missing files. This manifests as inscrutable git/Python errors.
The README's pre-flight checklist mandates 20 GB free as a guardrail.

---

## What this is and isn't

This is an **orchestrator-worker multi-agent system** in the loose, current-industry
sense — multiple LLM-driven agents with specialized roles and tool subsets,
coordinated by a lead. It is **not**:

- A peer-to-peer agent network with dynamic delegation (the lead reads each
  subagent's output, but they don't talk to each other).
- A self-organizing or self-improving system (no agent rewrites another's
  prompt or workflow).
- A planner-and-replanner like Magentic-One (the workflow is fixed at routing
  time; no re-planning after subagent failures).

If you want a richer pattern (group chat, handoff with tool approvals,
Magentic-One-style dynamic planning), Microsoft Agent Framework provides those
builders out of the box (`agent_framework.orchestrations`). The decision to use a
static fan-out + synthesis pattern here is intentional: predictable, debuggable,
fits free-tier quotas, and is enough for this use case.

---

## Extending the system

### Adding a new subagent

1. Add a system prompt in `src/inderes_agent/agents/prompts/<role>.md` describing
   the agent's role, tools, workflow, and output format.
2. Define the MCP tool subset in `src/inderes_agent/mcp/inderes_client.py`
   (e.g. `MY_DOMAIN_TOOLS = (...)`).
3. Add a factory in `src/inderes_agent/agents/<role>.py`:
   ```python
   def build_my_role_agent() -> Agent:
       return Agent(
           client=build_chat_client(),
           name="aino-my-role",
           instructions=load_prompt("my_role.md"),
           tools=build_mcp_tool(name="inderes-my-role", allowed=MY_DOMAIN_TOOLS),
       )
   ```
4. Export it from `src/inderes_agent/agents/__init__.py`.
5. Add the new domain to the `Domain` enum in
   `src/inderes_agent/orchestration/router.py`, and add a few-shot example to
   the router prompt.
6. Register the builder in `_AGENT_BUILDERS` in
   `src/inderes_agent/orchestration/workflows.py`.
7. Add a tool-attribution mapping in `src/inderes_agent/observability/narrate.py`
   so the timeline correctly attributes the new tools.

### Pointing at a different MCP server

If you want to repurpose the system for a different MCP server (Linear, GitHub,
your company's internal MCP):

1. Update `INDERES_MCP_URL` and `INDERES_MCP_CLIENT_ID` in `.env` (or rename them).
2. Update OAuth discovery in `src/inderes_agent/mcp/oauth.py` if the server uses a
   different auth scheme (e.g. API key via `header_provider` is simpler than PKCE).
3. Replace the tool partitioning constants and prompts to match the new domain.

The Gemini fallback wrapper, observability layer, and CLI infrastructure are
all MCP-agnostic.

### Upgrading the lead model

Synthesis quality is bounded by the model. To use a smarter model for the lead
only (cheap because the lead does just one call per query):

```python
# in src/inderes_agent/agents/lead.py
def build_lead_agent() -> Agent:
    return Agent(
        client=build_chat_client(
            settings_override=Settings(
                PRIMARY_MODEL="gemini-2.5-pro",
                FALLBACK_MODEL="gemini-2.5-flash",
            ),
        ),
        ...
    )
```

(You'll need to add a `settings_override` parameter to `build_chat_client()` —
it's a simple addition.)

This requires paid-tier Gemini, but adds only a few cents per query.
