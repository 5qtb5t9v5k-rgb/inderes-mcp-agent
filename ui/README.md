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

The Streamlit app wears a "Trading Desk" theme: dark Bloomberg-style chrome,
JetBrains Mono throughout, color-coded agent personas with glyphs (◆ LEAD,
▲ QUANT, ■ RESEARCH, ● SENTIMENT, ✦ PORTFOLIO).

### Top of page (hero)

- Brand line: `INDERES//AGENT  DESK  ● VERKOSSA`
- Brand equation: `INDERES + MCP + AGENTIT = INSIGHTS`
- One-line tagline + agent roster in their persona colors

### Chat (main column)

- **Live progress** during query execution via a custom status box (no
  Material Symbols icon — pulsing CSS dot indicator). Phases:
  Reitittäjä → Subagentit → LEAD → Valmis.
- **LEAD synthesis** answer with a `**💭 Perustelut:**` amber-callout at
  the top — meta-level reasoning for how the subagents' outputs were
  combined. Then the answer body.
- **🔍 Agenttien toimintaloki** expander at the bottom (collapsed by
  default) showing routing decision, per-subagent rows (with `**Ajatus:**`
  thought-trace styled in violet italic), and full output for each agent
  including code blocks + sandbox stdout (green-bordered) + tables.

### Sidebar (left)

- Red-bordered legal disclaimer at the top
- `ARKKITEHTUURI` panel: short description of LEAD → subagents → MCP flow
  plus a STACK/DATA/LOG/STATUS key-value strip
- `LISÄTIEDOT GITHUBISSA →` button linking to the repo
- `AGENTIT` panel: each persona with glyph, role, and short FI description
- `KESKUSTELU` clear-chat button
- `VIIMEISIMMÄT AJOT` list of the 8 most recent runs on disk

### Bottom

- Status bar with MCP host, model, error/fallback counters
- Chat input "Kysy jotain Pohjoismaisista osakkeista…"

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
