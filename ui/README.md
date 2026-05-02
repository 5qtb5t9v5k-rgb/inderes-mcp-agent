# Streamlit UI

A thin browser-based wrapper around the agent. Same code as the CLI underneath
(`inderes_agent.cli.repl.handle_query` flow), just rendered as a chat with live
phase indicators and an expandable subagent trace.

## Run

```bash
# from repo root, with venv active
uv pip install --pre -e '.[ui]'
streamlit run ui/app.py
```

Streamlit opens the app in your browser at `http://localhost:8501`. First load
takes ~10 s for imports. The OAuth token from a previous CLI run (cached at
`~/.inderes_agent/tokens.json`) is reused; first time on a fresh machine, the
Inderes login window pops up.

## What the UI does

- **Chat interface** with persistent conversation history per session
- **Live progress** during query execution: routing → subagents → synthesis
- **Subagent trace** shown in an expander under each answer, including:
  - Routing decision and reasoning
  - Per-subagent model used and full output text
  - Duration, error count, fallback events
- **Sidebar** with a clear-chat button and a list of recent runs

## What it doesn't do (yet)

- Hosting (local-only — the OAuth callback expects localhost)
- Streaming answers (synthesis prints all at once)
- Model picker (uses the same `PRIMARY_MODEL` / `FALLBACK_MODEL` from `.env`)
- Concurrency tuning UI (use `MAX_CONCURRENT_AGENTS` env var)

## Hosting (Streamlit Cloud)

Single-user, public, password-gated deployment is supported. See
[`DEPLOY.md`](DEPLOY.md) for step-by-step instructions covering:

- Capturing your OAuth tokens locally and pasting them as a Streamlit secret
- Setting an app password and a daily query cap
- Configuring a Gemini budget cap so a leaked password can't run away with cost
- Day-to-day operational tasks (token rotation, password rotation)

Multi-user OAuth (each user logs in with their own Inderes credentials) would
require coordination with Inderes to register the deployed URL as a Keycloak
redirect URI — out of scope for this project.

## Stopping the server

Ctrl-C in the terminal where you ran `streamlit run`. The browser tab can stay
open; reconnecting later requires restarting the server.
