# Changelog

All notable changes to this project. Format roughly follows
[Keep a Changelog](https://keepachangelog.com); the project does not yet follow
[SemVer](https://semver.org) strictly.

## [unreleased] — 2026-05-02 / 2026-05-03

A heavy iteration on the Streamlit UI plus operational improvements to the
agent layer. No breaking changes.

### Added — Streamlit UI ("Trading Desk" visual layer)

- Bloomberg-style dark theme: JetBrains Mono throughout, amber accents, agent
  glyphs (◆ LEAD, ▲ QUANT, ■ RESEARCH, ● SENTIMENT, ✦ PORTFOLIO).
- Hero panel with brand equation `INDERES + MCP + AGENTIT = INSIGHTS` + agent
  roster.
- Sidebar: red disclaimer at top (single source of truth for legal notice),
  architecture summary, GitHub CTA, agent personas with descriptions,
  recent runs list.
- Routing card with colored domain pills + free-form `PERUSTELU` (pink/violet
  accent so prose reasoning is distinct from categorical fields).
- Per-agent rows in the activity log expander: glyph + role + model + status
  badge; full structured output renders inline below each row.
- `CustomStatus` widget replaces `st.status` — pulsing CSS-only dot
  indicator for state, no Material Symbols icon-font race conditions.
- Markdown table + strikethrough rendering enabled for subagent output.
- Python sandbox stdout (e.g. `print(df)` results) auto-detected and wrapped
  in a green-bordered ```output``` block, distinct from the blue-bordered
  source-code blocks.
- `.streamlit/config.toml` sets primary color to amber so Streamlit's own
  chrome (chat input focus ring, progress bars) is on-brand.

### Added — agent reasoning visibility (#3 from BACKLOG.md)

- Mandatory `**Ajatus:**` thought-trace line at the top of every subagent
  response — surfaces tool-selection reasoning before the structured answer.
  Violet-bordered italic styling in `.ia-agent-output`.
- Mandatory `**💭 Perustelut:**` reasoning callout at the top of every LEAD
  synthesis — meta-level commentary on how the subagents' outputs were
  combined. Amber-bordered styling in `.ia-lead-answer`, distinct from the
  subagents' violet so visual hierarchy is preserved.

### Added — date awareness in prompts

- `load_prompt()` now prepends a `# CURRENT DATE` header (ISO + Finnish
  weekday) to every loaded subagent prompt. Without this, Gemini was
  answering "tänään 14.5.2025" when the system date was 2026-05-03.
- `today_prompt_prefix()` also prefixes every per-query user prompt with
  the same date stamp (belt-and-suspenders against system-instruction
  attention loss in long contexts).

### Added — durable OAuth token persistence

- New optional GitHub Gist mirror for `tokens.json`. Configure
  `INDERES_TOKENS_GIST_ID` and `INDERES_TOKENS_GH_TOKEN` and the agent
  pushes refreshed tokens to a private gist on every refresh, pulls from
  the gist on cold start. Solves the Streamlit Cloud problem where
  refresh-token rotation eats itself across container restarts.
- See `ui/DEPLOY.md §6.5` for setup.

### Fixed

- Material Symbols icon font no longer overridden by the global mono font
  rule (fixed `keyboard_double_arrow_right` ligature text in sidebar
  collapse arrow + `check_circle` overlap on status widget).
- Markdown tables in subagent output now render correctly (was missing
  `table` extension on `MarkdownIt("commonmark")`).
- Raw Python source from QUANT (no fences) auto-wraps in ```python``` so
  it renders as code instead of as headers/paragraphs.
- Trace-expander toggling no longer auto-scrolls Chrome to the bottom of
  the page (`overflow-anchor: none` + JS scrollY restore on summary
  click).

### Removed

- "📜 Täydellinen ajoloki" expander — was rendering the full narrative.md
  inline for every old assistant message on every Streamlit rerun, the
  dominant slowdown as chat history grew. The narrative.md file is still
  written to disk by the pipeline.
- Daily-quota progress bar from the sidebar — cap mechanism still works
  server-side; just don't surface the count.

---

## [0.1.0] — 2026-05-01

Initial release. The system is functionally complete: it ingests natural-language
stock-research questions, routes them across four specialized subagents, and
synthesizes a final answer with sources. Tested end-to-end against real Inderes
MCP and paid Gemini.

### Added — core architecture

- Five-agent system on Microsoft Agent Framework 1.0+: `aino-lead` orchestrator
  plus four specialized subagents (`aino-quant`, `aino-research`,
  `aino-sentiment`, `aino-portfolio`).
- Each subagent gets a focused subset of Inderes MCP tools (3–8 tools each, not
  all 16) — improves tool-call accuracy materially over a monolithic agent.
- Lead has no tools; synthesizes subagent outputs from a structured prompt.
- Router uses structured-output Gemini call (JSON) with few-shot examples.
- Workflow uses `asyncio.gather` + `Semaphore(MAX_CONCURRENT_AGENTS)` instead of
  MAF's `ConcurrentBuilder` — runtime-decided fan-out for comparison queries
  and a hard quota cap.

### Added — Gemini fallback

- `FallbackGeminiChatClient` subclass with primary→retry→fallback chain.
- Default models: primary `gemini-3.1-flash-lite-preview`, fallback
  `gemini-2.5-flash`. Both are free-tier-eligible.
- Fallback policy: 1 retry on primary with `RETRY_DELAY_MS`; switch to fallback
  on persistent 503 or any 429; fallback gets 2 attempts with 2 s/4 s backoff.
- `last_used_model` recorded per chat client so the trace and `narrative.md`
  show which model handled each request.
- `QuotaExhaustedError` raised when both primary and fallback exhaust quota.
- No reference to `gemini-2.5-pro` anywhere — quota is zero on free tier.

### Added — Inderes MCP integration

- OAuth 2.0 Authorization Code + PKCE flow against Inderes' Keycloak SSO
  (`src/inderes_agent/mcp/oauth.py`):
  - Discovery from `/.well-known/oauth-protected-resource` (RFC 9728), then
    `/.well-known/openid-configuration`.
  - Localhost callback server handles the redirect.
  - Tokens cached at `~/.inderes_agent/tokens.json` with `0600` permissions.
  - Refresh-token reuse on subsequent runs.
- `_InderesBearerAuth(httpx.Auth)` injects the latest cached token per request,
  handling refresh-on-expiry transparently.
- `prefetch_token()` called eagerly in `__main__.py` so concurrent agent builds
  share the cached token instead of racing four OAuth flows.
- `_SanitizingMCPTool` subclass strips `$schema`, `$id`, `$ref`, `$defs`,
  `$comment` from tool input schemas after `connect()`. Inderes' MCP schemas
  include these JSON-Schema metadata fields; Gemini's `FunctionDeclaration`
  Pydantic validator rejects them.
- `load_prompts=False` on every MCP tool: Inderes MCP doesn't implement
  `prompts/list`. Default `load_prompts=True` would crash with `Method not found`.

### Added — observability

- Per-run directory at `~/.inderes_agent/runs/<timestamp>/` containing:
  `query.txt`, `routing.json`, `subagent-NN-<domain>.json`, `synthesis.txt`,
  `meta.json`, `console.log`, `narrative.md`.
- `narrate.py` parses `console.log` (extracts function-call timings) and JSON
  files into a human-readable markdown narrative with routing decision,
  per-tool-call timeline, per-subagent outputs, synthesis, and statistics.
- REPL slash commands: `/explain`, `/trace`, `/last`, `/runs`, `/agents`,
  `/clear`, `/help`, `/exit`.
- One-shot mode auto-prints a compact subagent trace plus paths to the run log
  and `narrative.md`.
- OpenTelemetry tracer with `ConsoleSpanExporter` configured (MAF emits spans
  natively).

### Added — CLI

- `rich` for terminal output: markdown answers, tables, error panels.
- `prompt_toolkit` for REPL input with history.
- Inline progress lines per phase (router → subagents → synthesis) so users
  don't think the system is hung during multi-second waits.
- Standalone `scripts/diag.py` that probes Gemini and MCP connectivity
  independently with per-step timing.
- `scripts/explain.py` regenerates `narrative.md` for any past run.

### Added — tests

- 13 unit tests covering:
  - Router JSON parsing (with code fences, prose leaks, plain JSON)
  - `QueryClassification` Pydantic validation
  - `FallbackGeminiChatClient`: 503 retry, 429 quota exhaustion,
    success-without-fallback paths
  - Workflow: per-company fan-out for comparisons, no fan-out for
    single-domain queries, concurrency cap enforcement
- End-to-end tests against real services not in CI (require credentials and
  consume Gemini quota).

### Build environment

- Python 3.11+ required; 3.13 in development via `uv`-managed CPython.
- `uv` recommended over plain pip for install speed and ARM-native Python
  management.
- `agent-framework-gemini` requires `--pre` flag (currently alpha-versioned on
  PyPI as `1.0.0aN`).
- ARM-native Python required on Apple Silicon: Intel Python via Rosetta 2 is
  ~10× slower for cold imports.
- 20+ GB free disk space recommended: APFS becomes unstable above ~90 % capacity
  (manifests as `mmap` failures, `bus error`, truncated reads — affecting both
  the application and git).

### Known limitations

- Synthesis quality is bounded by the lite-tier model on free Gemini. Paid tier
  enables upgrading the lead to `gemini-2.5-pro` (~few €/month additional)
  without changing the rest of the stack.
- During Gemini-side capacity spikes, both primary and fallback can return 503
  simultaneously. The system gracefully degrades (lead synthesizes from
  whichever subagents succeeded) but the failed subagents return empty data
  that the lead has to acknowledge.
- Tool-call attribution in `narrative.md` is heuristic. Tools unique to one
  agent (e.g. `get-fundamentals`) attribute correctly; tools shared across
  agents (e.g. `search-companies`) are marked `[shared]`.
- The system doesn't implement Magentic-One-style dynamic re-planning. If a
  subagent fails, the lead does its best with the remaining outputs but can't
  replan the workflow to compensate.

---

## Roadmap (not yet implemented)

Ideas worth exploring; none are committed.

- Upgrade only the lead to a stronger model (e.g. `gemini-2.5-pro`) for better
  synthesis quality without 4× cost on subagents.
- ~~Web UI via Streamlit hosted on Streamlit Community Cloud with a single
  pre-cached OAuth token (personal-use deployment).~~ Implemented in
  [unreleased]; tokens persist via optional GitHub Gist mirror. See
  `BACKLOG.md` for further agentic-improvement ideas.
- Streaming synthesis output in the REPL (the streaming path in
  `FallbackGeminiChatClient` is wired but not used by the current REPL flow).
- Automatic retry-the-whole-query if too many subagents fail simultaneously
  (rather than letting the lead degrade).
- Per-day quota usage display in `/trace` (currently you have to consult the
  Google AI Studio dashboard).
- Time-window filtering for `aino-research` to avoid pulling stale reports
  when the user explicitly wants only recent material.
