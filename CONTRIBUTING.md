# Contributing

Guide for working on `inderes-mcp-agent-system` — local development setup, how to
test, where extension points live, and conventions for changes.

This is a personal-use project, not an open-source library. Conventions below
are for internal consistency rather than for external contributors.

## Table of contents

- [Development environment](#development-environment)
- [Running locally](#running-locally)
- [Testing](#testing)
- [Code style](#code-style)
- [Extension points](#extension-points)
- [Working on the OAuth flow](#working-on-the-oauth-flow)
- [Working on the Gemini fallback](#working-on-the-gemini-fallback)
- [Working on the narrator](#working-on-the-narrator)
- [Common gotchas](#common-gotchas)
- [Commit conventions](#commit-conventions)

---

## Development environment

### Required

- macOS, Linux, or WSL2. Apple Silicon Macs must use ARM-native Python (the
  install commands below ensure this).
- `uv` (the Python package manager). Install:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- ≥ 20 GB free disk space (see [TROUBLESHOOTING](TROUBLESHOOTING.md#disk-full--mmap-errors--bus-errors--short-reads)
  for why this matters).

### Setup

```bash
git clone https://github.com/5qtb5t9v5k-rgb/inderes-mcp-agent-system
cd inderes-mcp-agent-system

uv python install 3.13
uv venv --python-preference only-managed --python 3.13
source .venv/bin/activate
uv pip install --pre -e '.[dev]'

cp .env.example .env
# edit .env: set GEMINI_API_KEY=AIza...
```

The `[dev]` extra adds `pytest`, `pytest-asyncio`, `ruff`, and `mypy`.

### Verify the install

```bash
python -c "import platform; print(platform.machine())"      # arm64 on Apple Silicon
time python -c "from agent_framework_gemini import GeminiChatClient"   # ~1–2 s warm
pytest -q                                                   # 13 passed
```

---

## Running locally

### One-shot

```bash
python -m inderes_agent "What's Konecranes' current P/E?"
```

### REPL

```bash
python -m inderes_agent
```

### Diagnostic (when something is hanging)

```bash
python scripts/diag.py
```

This probes Gemini and Inderes MCP independently with per-step timing — useful
to isolate whether a hang is in the LLM call, the MCP connection, or somewhere else.

### Examining a past run

```bash
python scripts/explain.py                       # latest run
python scripts/explain.py 20260501-205122-776   # specific run
ls -1t ~/.inderes_agent/runs/ | head -5         # 5 most recent
```

---

## Testing

```bash
pytest -q                                       # all unit tests
pytest -v                                       # verbose
pytest tests/test_router.py                     # one file
pytest -k "fallback"                            # by test name pattern
```

Unit tests do not hit the real Gemini API or Inderes MCP. They mock at the
chat-client and workflow seams.

### What's covered

- **Router** (`tests/test_router.py`): JSON extraction with code fences, prose
  leaks, plain JSON; `QueryClassification` Pydantic validation.
- **Fallback client** (`tests/test_fallback.py`): 503 retry, 429 quota
  exhaustion, success-without-fallback paths.
- **Workflows** (`tests/test_workflows.py`): per-company fan-out for
  comparisons, no fan-out for single-domain queries, concurrency cap.

### What's not covered

End-to-end tests against the real Gemini API and Inderes MCP. Run those manually
with credentials when you make material changes:

```bash
python -m inderes_agent "What's Konecranes' current P/E?"
python -m inderes_agent "Compare Konecranes and Cargotec on profitability"
python -m inderes_agent "Mitä Inderes pitää mallisalkussaan?"
```

Each of these exercises a different code path (single-domain, multi-domain
fan-out, portfolio).

### Adding tests

Place new tests in `tests/`. The existing `conftest.py` sets dummy environment
variables so router/workflow tests can construct chat clients and MCP tools
without crashing on missing API keys. If you need a real `GEMINI_API_KEY` for an
integration test, gate it on the env var being set:

```python
@pytest.fixture
def have_real_gemini():
    return bool(os.environ.get("GEMINI_API_KEY")) and os.environ["GEMINI_API_KEY"] != "dummy-test-key"

@pytest.mark.integration
def test_against_real_gemini(have_real_gemini):
    if not have_real_gemini:
        pytest.skip("requires real GEMINI_API_KEY")
    ...
```

---

## Code style

- **Formatting**: `ruff format` (configured in `pyproject.toml`).
- **Linting**: `ruff check`.
- **Type checking**: `mypy` (loosely; not enforced in CI).

```bash
ruff format src tests
ruff check src tests
```

Conventions:

- Async functions where the call hits the network or MAF; sync everywhere else.
- Type hints on public functions and dataclasses; less strict on internals.
- Pydantic models for any structured data crossing a process boundary
  (router output, subagent results, settings).
- `dataclass` for ephemeral internal records.

---

## Extension points

### Adding a new subagent

Concrete steps; see [ARCHITECTURE.md → Extending the system](ARCHITECTURE.md#extending-the-system)
for prose explanation.

1. Create `src/inderes_agent/agents/prompts/<role>.md` describing the agent's
   role, tools, recommended workflow, and required output format. Follow the
   structure of existing prompts (lead, quant, research, sentiment, portfolio).
2. Add the tool subset constant in `src/inderes_agent/mcp/inderes_client.py`:
   ```python
   MY_DOMAIN_TOOLS = ("search-companies", "tool-a", "tool-b")
   ```
3. Create `src/inderes_agent/agents/<role>.py` with a builder function:
   ```python
   def build_<role>_agent() -> Agent:
       return Agent(
           client=build_chat_client(),
           name="aino-<role>",
           instructions=load_prompt("<role>.md"),
           tools=build_mcp_tool(name="inderes-<role>", allowed=MY_DOMAIN_TOOLS),
       )
   ```
4. Export from `src/inderes_agent/agents/__init__.py`.
5. Add the new domain to `Domain` enum in
   `src/inderes_agent/orchestration/router.py`. Update the few-shot examples in
   the router prompt so Gemini learns when to invoke this agent.
6. Register the builder in `_AGENT_BUILDERS` in
   `src/inderes_agent/orchestration/workflows.py`.
7. Update tool-attribution in `src/inderes_agent/observability/narrate.py` so
   the timeline correctly attributes the new tools.
8. Add a test in `tests/test_workflows.py` covering the new domain.

### Pointing at a different MCP server

The system is mostly MCP-agnostic. To repurpose for a different server (Linear,
GitHub, internal company data, etc.):

1. Update `INDERES_MCP_URL` and `INDERES_MCP_CLIENT_ID` in `.env`.
2. Replace `src/inderes_agent/mcp/oauth.py` if the new server uses a different
   auth scheme. For API-key auth, set the header in `_make_header_provider()`
   in `inderes_client.py` and skip OAuth entirely.
3. Replace the tool partitioning constants and prompts to match the new domain.
4. Update or remove `_SanitizingMCPTool` if the new server's schemas don't have
   the `$schema` problem.
5. Update or remove `load_prompts=False` if the new server actually exposes prompts.

The Gemini fallback wrapper, observability layer, and CLI infrastructure are
all unchanged.

### Upgrading the lead model only

Synthesis quality is bounded by the model. To use a smarter model for the lead
only (cheap because the lead does just one call per query):

```python
# src/inderes_agent/agents/lead.py
def build_lead_agent() -> Agent:
    settings = get_settings()
    return Agent(
        client=FallbackGeminiChatClient(
            primary_model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            api_key=settings.require_gemini_key(),
            retry_delay_ms=settings.RETRY_DELAY_MS,
            max_retries=settings.MAX_RETRIES,
        ),
        name="aino-lead",
        instructions=load_prompt("lead.md"),
        tools=None,
    )
```

Requires paid-tier Gemini. Per-query cost increase is small (the lead does one
call per query).

---

## Working on the OAuth flow

OAuth code lives in `src/inderes_agent/mcp/oauth.py` and is triggered eagerly in
`src/inderes_agent/__main__.py` via `prefetch_token()`.

### Forcing a fresh login

```bash
rm ~/.inderes_agent/tokens.json
python -m inderes_agent "..."
```

### Inspecting the cached token

```bash
cat ~/.inderes_agent/tokens.json | python3 -m json.tool
```

The file contains `access_token` (short-lived), `refresh_token` (longer-lived),
`expires_at` (Unix timestamp), `token_endpoint`, and `client_id`.

### Debugging discovery

```bash
curl -s https://mcp.inderes.com/.well-known/oauth-protected-resource | python3 -m json.tool
curl -s https://sso.inderes.fi/auth/realms/Inderes/.well-known/openid-configuration | python3 -m json.tool
```

If either of these fails, OAuth discovery itself is broken — likely a
network/firewall issue.

---

## Working on the Gemini fallback

`FallbackGeminiChatClient` in `src/inderes_agent/llm/gemini_client.py` is the
only place where model selection logic lives. Tests in
`tests/test_fallback.py` cover the retry semantics without hitting the real API.

### Tuning the policy

`.env` controls behavior:
- `RETRY_DELAY_MS`: base delay between primary attempts (default 1000)
- `MAX_RETRIES`: number of primary retries (default 1)
- Fallback delays are hardcoded (2 s and 4 s) in `_fallback_call()`.

### Adding a new exception class to detect

If Gemini introduces a new error code that should trigger fallback, add a
helper alongside `_is_unavailable()` and `_is_quota_exhausted()`:

```python
def _is_my_new_condition(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "<sentinel>" in msg
```

Then update the dispatch in `_awaitable_call()` and `_fallback_call()`.

---

## Working on the narrator

`src/inderes_agent/observability/narrate.py` parses `console.log` plus the JSON
files written by `run_log.write_run()` into a markdown narrative. It runs
automatically after every query and produces `<run_dir>/narrative.md`.

### Regenerating for past runs

```bash
python scripts/explain.py                       # latest
python scripts/explain.py 20260501-205122-776   # specific
```

In the REPL: `/explain` outputs the latest narrative directly.

### Adding new event types

If you add new log lines (e.g. via `logger.info(...)`) that should appear in the
narrative, update `narrate.py`:

1. Add a regex pattern in the `_extract_*()` helpers.
2. Add the event type to the timeline section in `summarize_run()`.

### Tool-call attribution

`narrate.py` attributes tool calls to subagents based on the tool name. Tools
unique to one agent (`get-fundamentals`, `list-content`, etc.) attribute
correctly. Shared tools (`search-companies`) are marked `[shared]`.

To improve attribution, the cleanest approach is to thread an agent identifier
into MAF's logging — but this requires MAF middleware which isn't currently
wired up. Worth doing if attribution becomes important enough.

---

## Common gotchas

### "Module not found" after editing source

You're either:
1. Not in the venv: `source .venv/bin/activate`.
2. The editable install pointer broke: `uv pip install --force-reinstall --pre -e .`.

### Tests pass but real run fails

Likely an issue at a seam tests don't cover — most often:
- OAuth token expired or invalid (check `~/.inderes_agent/tokens.json`).
- Gemini API key wrong or paid tier disabled (check
  https://aistudio.google.com/app/usage).
- Inderes MCP returned new schema fields the sanitizer doesn't strip (check
  `_INCOMPATIBLE_SCHEMA_KEYS` in `inderes_client.py`).

Run the diagnostic to isolate:
```bash
python scripts/diag.py
```

### Disk-full → cryptic errors everywhere

Below ~10 % free disk on macOS, APFS becomes unstable. You'll see `mmap`
failures, `bus error`, truncated reads, and missing files in unrelated tools
(git, Python imports). Always check `df -h .` first when something inexplicable
happens.

### Long-running queries seem stuck

First cold-start imports take 5–10 s. The router LLM call adds 5–15 s. The first
MCP `initialize` adds 1–2 s. Total time to the first visible progress line is
**10–30 s normally**. Wait at least 60 s before suspecting a hang.

---

## Commit conventions

Conventional-commits-ish, but not strict:

```
<type>: <short description>

<optional longer body>

<optional footer with refs>
```

Types loosely:
- `feat:` new functionality
- `fix:` bug fix
- `refactor:` code change without behavior change
- `docs:` documentation only
- `test:` test changes
- `chore:` build/tooling/etc.

Subject line ≤ 72 chars; body wrap at ~72. Use Finnish or English consistently
within a commit (the repo is Finnish-leaning but English commits are also fine).
