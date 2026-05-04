#!/usr/bin/env bash
# One-shot recovery for "Inderes session dead" on the cloud deployment.
#
# Run this when:
# - Cloud Streamlit shows "Authenticating with Inderes…" then HeadlessAuthError
# - GitHub Actions emailed you "Inderes session dead"
# - You just got back from a holiday and want a fresh start
#
# What it does:
# 1. cd to the repo root and activate the venv
# 2. Stash the existing local tokens.json (so login is forced fresh)
# 3. Run the agent with a tiny test query — opens a browser to Inderes login
# 4. Push the fresh tokens to the gist (so cloud + cron see them)
# 5. Trigger the cron workflow once to verify the chain end-to-end
# 6. Tail the latest cron log to show the result
#
# Usage:
#   bash scripts/relogin.sh            # from anywhere
#   ./scripts/relogin.sh               # from repo root (after chmod +x)
#
# Prerequisites:
# - .env contains INDERES_TOKENS_GIST_ID
# - `gh` CLI authenticated (`gh auth status` should show logged in)
# - venv exists at .venv/

set -e  # exit on first error
set -u  # error on undefined variables

# Resolve repo root from this script's location, regardless of cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "▶ repo: $REPO_ROOT"

# --- 1. Sanity checks --------------------------------------------------------

if [[ ! -d .venv ]]; then
  echo "ERROR: .venv/ not found at $REPO_ROOT/.venv"
  echo "       Create it: uv venv --python 3.13 && source .venv/bin/activate && uv pip install --pre -e ."
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh CLI not installed. brew install gh && gh auth login"
  exit 1
fi

# Activate venv (sets PATH so `python` resolves to .venv's interpreter).
# shellcheck disable=SC1091
source .venv/bin/activate
echo "▶ python: $(which python)"

# --- 2. Stash old tokens so login is fresh ----------------------------------

TOKENS_PATH="$HOME/.inderes_agent/tokens.json"
if [[ -f "$TOKENS_PATH" ]]; then
  BACKUP="$TOKENS_PATH.bak.$(date +%s)"
  mv "$TOKENS_PATH" "$BACKUP"
  echo "▶ stashed old tokens → $BACKUP"
else
  echo "▶ no existing tokens to stash"
fi

# --- 3. Run agent → opens browser → fresh login -----------------------------

echo
echo "▶ launching agent (selain aukeaa, kirjaudu Inderesiin)…"
echo
python -m inderes_agent "test" || {
  echo
  echo "ERROR: agent run failed. Check the output above. tokens.json may not be fresh."
  exit 1
}

# --- 4. Sync fresh tokens to gist -------------------------------------------

echo
echo "▶ syncing to gist…"
python scripts/sync_local_tokens_to_gist.py

# --- 5. Trigger cron + watch result -----------------------------------------

echo
echo "▶ triggering cron to verify end-to-end…"
gh workflow run refresh-inderes-tokens.yml

# Give GitHub a moment to spin up the runner before we ask for the result.
sleep 25

RUN_ID=$(gh run list \
  --workflow=refresh-inderes-tokens.yml \
  --limit 1 \
  --json databaseId \
  --jq '.[0].databaseId')

echo "▶ cron run id: $RUN_ID"

# Filter for the lines that actually matter — full log is huge.
RESULT=$(gh run view "$RUN_ID" --log 2>&1 \
  | grep -E "refresh OK|refresh failed|keepalive|Session not active" \
  | head -5 || true)

echo "$RESULT"

# --- 6. Final verdict --------------------------------------------------------

echo
if echo "$RESULT" | grep -q "refresh OK"; then
  echo "✅ Pelaa. Cloud Streamlit pitäisi toimia ~1 min sisällä."
  exit 0
else
  echo "⚠  Cron ei sanonut 'refresh OK'. Katso koko logi:"
  echo "    gh run view $RUN_ID --log"
  exit 1
fi
