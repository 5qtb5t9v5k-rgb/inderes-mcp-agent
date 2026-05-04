"""Push local ``~/.inderes_agent/tokens.json`` to the configured gist mirror.

Use this after relogging in locally to propagate fresh tokens to the
Streamlit Cloud deployment (which reads the gist on cold start) without
waiting for the next cron tick. Typical workflow after a session expires:

    python -m inderes_agent "test"                    # browser login
    python scripts/sync_local_tokens_to_gist.py       # push to gist
    gh workflow run refresh-inderes-tokens.yml        # verify chain

Why a separate script and not just letting the agent push automatically?
The agent's own gist push requires ``INDERES_TOKENS_GH_TOKEN`` (a GitHub
PAT) in the local ``.env``. This script avoids that by shelling out to
the already-authenticated ``gh`` CLI, so no extra secret material is
needed on disk.

Required:
- ``gh`` CLI installed and authenticated (``gh auth status``).
- ``INDERES_TOKENS_GIST_ID`` env var (or ``.env`` entry) — the hex ID of
  the private gist that holds ``tokens.json``.
- A fresh ``~/.inderes_agent/tokens.json`` from a recent local agent run.

Exit codes:
- 0 on success.
- 1 on missing config / file / push failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Best-effort .env loading so users don't have to remember to export.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

TOKENS_PATH = Path.home() / ".inderes_agent" / "tokens.json"
GIST_FILENAME = "tokens.json"


def main() -> int:
    gist_id = os.environ.get("INDERES_TOKENS_GIST_ID")
    if not gist_id:
        print(
            "ERROR: INDERES_TOKENS_GIST_ID is not set in env or .env.\n"
            "       Find the gist ID at https://gist.github.com (your gists)\n"
            "       or in the repo's GitHub Actions secrets, and add to .env:\n"
            "         INDERES_TOKENS_GIST_ID=<hex-id>",
            file=sys.stderr,
        )
        return 1

    if not shutil.which("gh"):
        print(
            "ERROR: `gh` CLI not installed. Install it:\n"
            "         brew install gh   # macOS\n"
            "       Then authenticate:\n"
            "         gh auth login",
            file=sys.stderr,
        )
        return 1

    if not TOKENS_PATH.exists():
        print(
            f"ERROR: {TOKENS_PATH} not found.\n"
            "       Run the agent locally first to log in:\n"
            '         python -m inderes_agent "test"',
            file=sys.stderr,
        )
        return 1

    # Sanity-check the file before pushing — gist would happily accept any
    # garbage, but failure modes are easier to debug if we catch it here.
    try:
        tokens = json.loads(TOKENS_PATH.read_text())
    except Exception as exc:
        print(f"ERROR: failed to parse {TOKENS_PATH}: {exc}", file=sys.stderr)
        return 1

    if "refresh_token" not in tokens or "access_token" not in tokens:
        print(
            "ERROR: tokens.json is missing required fields "
            "(refresh_token / access_token).",
            file=sys.stderr,
        )
        return 1

    age_min = (time.time() - TOKENS_PATH.stat().st_mtime) / 60
    print(f"local {TOKENS_PATH.name}: age {age_min:.0f} min")
    if age_min > 60:
        print(
            "  ⚠  tokens are >1 h old — if cloud was already broken, "
            "re-login first:"
        )
        print('     python -m inderes_agent "test"')

    rt_prefix = tokens.get("refresh_token", "")[:18]
    print(f"  refresh_token prefix: {rt_prefix}…")

    print(f"pushing to gist {gist_id[:8]}… (file: {GIST_FILENAME})")
    # Pipe the file content via stdin (`-`) instead of passing a path: this
    # avoids `gh` interpreting the path as ambiguous and works regardless
    # of which dir the gist's source file originally came from.
    result = subprocess.run(
        ["gh", "gist", "edit", gist_id, "--filename", GIST_FILENAME, "-"],
        input=TOKENS_PATH.read_text(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "(no stderr)"
        print(f"ERROR: `gh gist edit` failed:\n  {stderr}", file=sys.stderr)
        return 1

    print("✓ pushed local tokens to gist")
    print()
    print("Next: trigger the cron once to verify Keycloak still accepts them:")
    print("  gh workflow run refresh-inderes-tokens.yml")
    print()
    print("Then check the result:")
    print("  gh run list --workflow=refresh-inderes-tokens.yml --limit 1")
    print("  gh run view <run-id> --log | grep -E 'refresh OK|refresh failed'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
