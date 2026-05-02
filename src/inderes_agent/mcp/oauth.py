"""OAuth Authorization Code + PKCE flow for Inderes MCP.

Why this exists: Microsoft Agent Framework's `MCPStreamableHTTPTool` does NOT forward
auth providers to the underlying streamable HTTP client — it only exposes a
`header_provider` callback. So we run the OAuth dance ourselves, cache the token
to disk, and inject it as `Authorization: Bearer <token>` via header_provider.

Discovery: GET https://mcp.inderes.com/.well-known/oauth-protected-resource
  → authorization_servers = ["https://sso.inderes.fi/auth/realms/Inderes"]
  → GET <auth_server>/.well-known/openid-configuration for endpoints

Flow:
  1. Generate code_verifier + code_challenge (S256)
  2. Open browser to /protocol/openid-connect/auth?response_type=code&...
  3. Listen on http://localhost:<port>/callback for the redirect
  4. POST code + verifier to /protocol/openid-connect/token
  5. Save access_token + refresh_token to ~/.inderes_agent/tokens.json
  6. Refresh on expiry via refresh_token grant

The token cache is per-user, file-perm 0600.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

DEFAULT_RESOURCE_URL = "https://mcp.inderes.com"
DEFAULT_CLIENT_ID = "inderes-mcp"
TOKEN_REFRESH_BUFFER_S = 60  # refresh if expiring within 60 seconds

# Override the cache directory via env var. Useful in cloud environments where
# `~` doesn't exist or isn't writable (e.g. Streamlit Cloud → /tmp/inderes_agent).
CACHE_DIR_ENV = "INDERES_AGENT_CACHE"

# Bootstrap the token cache from this env var on first use, if no cache file
# exists yet. Used in cloud deployments where the operator pastes a JSON blob
# of an already-completed OAuth handshake into the deployment's secrets.
CLOUD_TOKEN_ENV = "INDERES_OAUTH_TOKENS_JSON"


def _cache_dir() -> Path:
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".inderes_agent"


def _token_cache_path() -> Path:
    return _cache_dir() / "tokens.json"


# Backwards-compat alias used by tests and the `/runs` UI helper.
TOKEN_CACHE = _token_cache_path()


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: float  # epoch seconds
    token_endpoint: str
    client_id: str

    @property
    def is_fresh(self) -> bool:
        return time.time() < (self.expires_at - TOKEN_REFRESH_BUFFER_S)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_endpoint": self.token_endpoint,
            "client_id": self.client_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TokenSet:
        return cls(**d)


def _save_tokens(tokens: TokenSet) -> None:
    cache = _token_cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(tokens.to_dict(), indent=2))
    os.chmod(cache, 0o600)


def _load_tokens() -> TokenSet | None:
    cache = _token_cache_path()
    if not cache.exists():
        _bootstrap_from_env()
        if not cache.exists():
            return None
    try:
        return TokenSet.from_dict(json.loads(cache.read_text()))
    except Exception:
        return None


def _bootstrap_from_env() -> bool:
    """If `INDERES_OAUTH_TOKENS_JSON` is set, write its contents to the token cache.

    Used in cloud deployments (Streamlit Cloud etc.) where the operator pastes
    an already-completed OAuth token JSON into deployment secrets. Without this,
    the agent would try to open a browser at startup and fail in a headless
    environment.

    Returns True if a fresh cache file was written, False otherwise (env var
    missing, cache already exists, or parse error).
    """
    raw = os.environ.get(CLOUD_TOKEN_ENV)
    if not raw:
        return False
    cache = _token_cache_path()
    if cache.exists():
        # Don't overwrite an existing cache — it may be more recent than the env
        # var (refresh has happened during this container's lifetime).
        return False
    try:
        data = json.loads(raw)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, indent=2))
        os.chmod(cache, 0o600)
        log.info("oauth_token_bootstrapped_from_env path=%s", cache)
        return True
    except Exception as exc:
        log.warning("oauth_env_bootstrap_failed err=%s", exc)
        return False


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class _DiscoveryResult:
    authorization_endpoint: str
    token_endpoint: str
    scopes: list[str]


def _discover(resource_url: str) -> _DiscoveryResult:
    pr = httpx.get(
        f"{resource_url.rstrip('/')}/.well-known/oauth-protected-resource",
        timeout=10.0,
    )
    pr.raise_for_status()
    pr_data = pr.json()
    auth_servers = pr_data.get("authorization_servers") or []
    if not auth_servers:
        raise RuntimeError("OAuth protected-resource metadata has no authorization_servers")
    scopes = pr_data.get("scopes_supported") or ["openid", "profile", "email"]

    auth_server = auth_servers[0].rstrip("/")
    cfg = httpx.get(f"{auth_server}/.well-known/openid-configuration", timeout=10.0)
    cfg.raise_for_status()
    cfg_data = cfg.json()

    return _DiscoveryResult(
        authorization_endpoint=cfg_data["authorization_endpoint"],
        token_endpoint=cfg_data["token_endpoint"],
        scopes=scopes,
    )


def _run_callback_server(port: int, expected_state: str) -> tuple[str, str]:
    """Run a single-shot HTTP server on localhost. Returns (code, state)."""
    received: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            qs = urllib.parse.urlparse(self.path).query
            params = dict(urllib.parse.parse_qsl(qs))
            received.update(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if "code" in params and params.get("state") == expected_state:
                self.wfile.write(
                    b"<html><body style='font-family:sans-serif;padding:40px'>"
                    b"<h2>inderes-research-agent</h2>"
                    b"<p>Authentication complete. You can close this tab.</p>"
                    b"</body></html>"
                )
            else:
                self.wfile.write(
                    b"<html><body><h2>Auth failed</h2><pre>"
                    + json.dumps(params, indent=2).encode()
                    + b"</pre></body></html>"
                )
            done.set()

        def log_message(self, *args, **kwargs):  # silence
            return

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    if not done.wait(timeout=300):
        server.server_close()
        raise TimeoutError("Auth callback did not arrive within 5 minutes")
    server.server_close()
    thread.join(timeout=2)

    if "code" not in received:
        raise RuntimeError(f"Auth callback missing code: {received}")
    if received.get("state") != expected_state:
        raise RuntimeError("OAuth state mismatch — possible CSRF")
    return received["code"], received["state"]


def _do_authorization_code_flow(
    discovery: _DiscoveryResult,
    client_id: str,
) -> TokenSet:
    port = _free_port()
    redirect_uri = f"http://localhost:{port}/callback"

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)

    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(discovery.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{discovery.authorization_endpoint}?{urllib.parse.urlencode(auth_params)}"

    print("\nOpening browser for Inderes login…")
    print(f"If it does not open, visit:\n  {auth_url}\n")
    try:
        webbrowser.open(auth_url, new=1, autoraise=True)
    except Exception:
        pass

    code, _ = _run_callback_server(port, state)

    resp = httpx.post(
        discovery.token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: {resp.status_code} {resp.text}")
    data = resp.json()

    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + int(data.get("expires_in", 300)),
        token_endpoint=discovery.token_endpoint,
        client_id=client_id,
    )


def _refresh_tokens(tokens: TokenSet) -> TokenSet | None:
    if not tokens.refresh_token:
        return None
    try:
        resp = httpx.post(
            tokens.token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": tokens.client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15.0,
        )
        if resp.status_code != 200:
            log.info("refresh_failed status=%s body=%s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", tokens.refresh_token),
            expires_at=time.time() + int(data.get("expires_in", 300)),
            token_endpoint=tokens.token_endpoint,
            client_id=tokens.client_id,
        )
    except Exception as exc:
        log.info("refresh_exception %s", exc)
        return None


class HeadlessAuthError(RuntimeError):
    """Raised when an interactive OAuth flow is needed but the environment can't open a browser.

    Shipped with a clear remediation message — typically the operator needs to
    run `python -m inderes_agent ...` once locally to populate
    `~/.inderes_agent/tokens.json`, then paste that file's contents into the
    deployment's `INDERES_OAUTH_TOKENS_JSON` secret.
    """


def _is_headless() -> bool:
    """Best-effort detection of an environment that can't host an interactive browser.

    Streamlit Cloud, Docker without DISPLAY, CI runners, etc. We use this to
    fail fast with a clear message instead of hanging on a blocked browser
    callback.
    """
    if os.environ.get("INDERES_AGENT_FORCE_INTERACTIVE"):
        return False
    # Standard headless markers
    if os.environ.get("STREAMLIT_RUNTIME_ENV", "").lower() in {"cloud", "production"}:
        return True
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    if os.environ.get("CI"):
        return True
    # macOS / Linux desktop usually has DISPLAY (Linux) or just works (macOS).
    # If DISPLAY is missing on Linux we treat as headless.
    import sys
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return True
    return False


def get_inderes_access_token(
    *,
    resource_url: str = DEFAULT_RESOURCE_URL,
    client_id: str = DEFAULT_CLIENT_ID,
    force_login: bool = False,
) -> str:
    """Return a valid access token, performing OAuth or refresh as needed.

    First call (no cache) opens a browser. Subsequent calls reuse the cached token,
    refreshing transparently when it's near expiry.

    In headless environments (Streamlit Cloud etc.), only refresh works — if a
    fresh login would be needed, raises `HeadlessAuthError` with instructions
    for the operator instead of hanging on a blocked browser callback.
    """
    # Eagerly bootstrap from env var on every call so cloud deployments don't
    # have to ensure prefetch_token() is invoked before any other code path.
    _bootstrap_from_env()

    cached = None if force_login else _load_tokens()
    if cached and cached.is_fresh:
        return cached.access_token

    if cached and cached.refresh_token:
        refreshed = _refresh_tokens(cached)
        if refreshed:
            _save_tokens(refreshed)
            return refreshed.access_token

    if _is_headless():
        raise HeadlessAuthError(
            "OAuth flow requires opening a browser, but this environment is "
            "headless (Streamlit Cloud / CI / no display).\n\n"
            "Remediation: run the agent once locally to complete OAuth, then "
            "paste ~/.inderes_agent/tokens.json into the deployment's "
            "INDERES_OAUTH_TOKENS_JSON secret. See ui/DEPLOY.md."
        )

    discovery = _discover(resource_url)
    tokens = _do_authorization_code_flow(discovery, client_id)
    _save_tokens(tokens)
    return tokens.access_token
