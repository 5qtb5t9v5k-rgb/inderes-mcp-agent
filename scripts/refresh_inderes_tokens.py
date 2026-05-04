"""Periodic refresh of Inderes OAuth tokens via GitHub gist mirror.

Designed to run from a GitHub Actions cron schedule (every ~15 min).
Pulls the latest tokens from the configured private gist, calls Inderes'
Keycloak with a refresh_token grant to rotate them, writes the new tokens
back to the same gist. Keeps the SSO session alive so it doesn't hit
idle-timeout, and keeps the gist (which Streamlit Cloud reads on cold
start) always-fresh.

Required env vars (set via GitHub Actions secrets):
- ``INDERES_TOKENS_GIST_ID``  — hex ID of the private gist holding tokens.json
- ``INDERES_TOKENS_GH_TOKEN`` — GitHub PAT with Gists: Read+Write scope

The script is intentionally self-contained — it does NOT depend on the
inderes_agent package, only on httpx + stdlib. This keeps the cron job
fast (no big package install) and lets the cron survive even if the
agent codebase changes shape.

Exit codes:
- 0  = refresh succeeded, OR refresh failed with an "expected" error
       (refresh_token revoked, session terminated, etc.) where there's
       nothing the cron can do — manual relogin required. We don't fail
       the workflow because GitHub would email-spam the maintainer.
- 1  = configuration / connectivity error (missing env, gist 5xx, etc.)
       — fail loud so the maintainer notices.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import httpx

GIST_ID = os.environ.get("INDERES_TOKENS_GIST_ID")
GH_TOKEN = os.environ.get("INDERES_TOKENS_GH_TOKEN")
TOKEN_ENDPOINT = (
    "https://sso.inderes.fi/auth/realms/Inderes/protocol/openid-connect/token"
)
MCP_URL = "https://mcp.inderes.com"
CLIENT_ID = "inderes-mcp"
GIST_FILENAME = "tokens.json"


def _log(msg: str) -> None:
    """Stamped log to stdout — shows up in the Actions run output."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def pull_from_gist() -> dict:
    r = httpx.get(
        f"https://api.github.com/gists/{GIST_ID}",
        headers={
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        timeout=15,
    )
    r.raise_for_status()
    files = r.json().get("files", {})
    content = files.get(GIST_FILENAME, {}).get("content")
    if not content:
        raise RuntimeError(f"Gist has no '{GIST_FILENAME}' file")
    return json.loads(content)


def push_to_gist(tokens: dict) -> None:
    r = httpx.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        json={
            "files": {
                GIST_FILENAME: {"content": json.dumps(tokens, indent=2)}
            }
        },
        headers={
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        timeout=15,
    )
    r.raise_for_status()


def keepalive_mcp_call(access_token: str) -> bool:
    """Make a minimal authenticated MCP request to count as real API activity.

    Hypothesis (BACKLOG: extending cron beyond /token-only): Keycloak's idle
    timer might track real API consumption separately from refresh_token grant
    calls. By hitting the MCP server with a single authenticated request after
    each refresh, we (a) cause Keycloak to introspect the access_token, and
    (b) generate "real" downstream activity so the SSO session sees a use
    that's indistinguishable from a normal user query.

    Sends an MCP `initialize` JSON-RPC request — the lightest valid call. We
    don't follow up with `notifications/initialized`, so the server-side
    session is briefly orphaned, but the auth check has already happened.

    Logs status. Never raises — keepalive is best-effort diagnostic; if it
    fails we still want to push the refreshed tokens to the gist. If after
    a few days of running with this we still see "Session not active" deaths
    overnight, the answer is "no, /token alone isn't worse than MCP calls"
    and we can revert this and look at scheduler reliability (option B in
    the BACKLOG).
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "inderes-tokens-cron-keepalive",
                "version": "0.1",
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    try:
        r = httpx.post(MCP_URL, json=payload, headers=headers, timeout=15)
    except Exception as exc:
        _log(f"  keepalive POST failed: {exc}")
        return False

    # 401/403 = auth not accepted (the whole point we're testing).
    # Anything else means Keycloak introspected the token successfully —
    # which is the activity we want.
    if r.status_code in (401, 403):
        _log(
            f"  keepalive auth rejected status={r.status_code} "
            f"body={r.text[:160]}"
        )
        return False
    _log(f"  keepalive auth accepted status={r.status_code}")
    return True


def refresh_tokens(refresh_token: str) -> dict | None:
    """Call Inderes' Keycloak with refresh_token grant.

    Returns a fresh tokens dict, or None if the refresh failed in a way
    that's not recoverable from a cron (revoked session etc.).
    """
    r = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if r.status_code != 200:
        _log(f"  refresh failed: status={r.status_code} body={r.text[:200]}")
        return None
    data = r.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_at": time.time() + data.get("expires_in", 300),
        "token_endpoint": TOKEN_ENDPOINT,
        "client_id": CLIENT_ID,
    }


def _write_step_summary(text: str) -> None:
    """Append to GITHUB_STEP_SUMMARY (visible at top of the workflow run page).

    Doesn't go to email — just makes the run page glanceable. Most useful
    line is the current health state: "Session healthy" vs "Session dead".
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception as exc:
        _log(f"  step summary write failed: {exc}")


def main() -> int:
    if not (GIST_ID and GH_TOKEN):
        _log("ERROR: missing INDERES_TOKENS_GIST_ID or INDERES_TOKENS_GH_TOKEN")
        return 1

    _log(f"Refreshing Inderes tokens via gist {GIST_ID[:8]}…")

    try:
        current = pull_from_gist()
    except Exception as exc:
        _log(f"  pull from gist failed: {exc}")
        return 1

    # Track session-health state across runs so we can email exactly once
    # per ok→failed transition rather than spamming every 5 min while dead.
    prior_status = current.get("_last_refresh_status", "unknown")
    _log(f"  prior status: {prior_status}")

    rt = current.get("refresh_token")
    if not rt:
        _log("  gist has no refresh_token field; nothing to refresh")
        return 1
    _log(f"  pulled tokens (rt prefix: {rt[:18]}…)")

    new_tokens = refresh_tokens(rt)
    timestamp = datetime.now(timezone.utc).isoformat()

    if new_tokens is None:
        # Refresh failed. Most common cause: SSO session terminated
        # (idle timeout, max-session cap, admin reset, user logged in
        # elsewhere). Cron can't recover — needs browser login locally.
        _log("  refresh did not succeed — manual relogin needed")

        # Persist the failed state in gist so the next run knows whether
        # this is a *new* failure (notify) or ongoing (silent).
        current["_last_refresh_status"] = "failed"
        current["_last_refresh_at"] = timestamp
        try:
            push_to_gist(current)
        except Exception as exc:
            _log(f"  status push to gist failed: {exc}")

        # Make the failure visible in the GitHub Actions UI:
        # ::error:: annotation paints the run red and shows in the UI summary.
        print(
            "::error title=Inderes session dead::"
            "Refresh failed and cron can't recover. "
            "Run locally: python -m inderes_agent 'test' && "
            "python scripts/sync_local_tokens_to_gist.py",
            flush=True,
        )
        _write_step_summary(
            f"## 🔴 Session DEAD\n\n"
            f"Last attempt: `{timestamp}`. Refresh against Keycloak failed.\n\n"
            f"**Recovery:**\n"
            f"```bash\n"
            f"python -m inderes_agent 'test'\n"
            f"python scripts/sync_local_tokens_to_gist.py\n"
            f"```\n"
        )

        # Exit 1 only on transition (ok → failed) so GitHub emails the
        # maintainer once per session-death rather than every 5 min.
        if prior_status == "ok":
            _log("  state transition ok→failed — exiting 1 to trigger email")
            return 1
        _log("  ongoing failure (prior_status was already failed) — exit 0 to avoid spam")
        return 0

    _log(
        f"  refresh OK (new rt prefix: {new_tokens['refresh_token'][:18]}…)"
    )

    # Keepalive: hit MCP with the fresh access_token before we do anything
    # else. If Keycloak's idle timer tracks /token use and MCP API use
    # equivalently, this is redundant; if it tracks them separately, this
    # is what keeps the session alive between real user queries.
    keepalive_mcp_call(new_tokens["access_token"])

    new_tokens["_last_refresh_status"] = "ok"
    new_tokens["_last_refresh_at"] = timestamp
    try:
        push_to_gist(new_tokens)
    except Exception as exc:
        _log(f"  push to gist failed: {exc}")
        return 1

    _log("  pushed fresh tokens to gist")
    _write_step_summary(
        f"## 🟢 Session healthy\n\n"
        f"Last refresh: `{timestamp}`.\n\n"
        f"- Keycloak refresh: ✓\n"
        f"- MCP keepalive: ✓\n"
        f"- Gist push: ✓\n"
    )
    if prior_status == "failed":
        _log("  recovered from previous failure")
        _write_step_summary(
            "\n_Recovered from previous failure — manual relogin must "
            "have happened._\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
