"""Tests for cloud-deployment OAuth bootstrap behavior."""

from __future__ import annotations

import json
import os

import pytest

from inderes_agent.mcp import oauth


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Point the token cache at a temp dir for the duration of the test."""
    monkeypatch.setenv(oauth.CACHE_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(oauth.CLOUD_TOKEN_ENV, raising=False)
    yield tmp_path


def _fake_tokens_dict(access: str = "fake-access") -> dict:
    return {
        "access_token": access,
        "refresh_token": "fake-refresh",
        "expires_at": 9999999999.0,
        "token_endpoint": "https://example.com/token",
        "client_id": "inderes-mcp",
    }


def test_bootstrap_writes_cache_from_env(isolated_cache, monkeypatch):
    """Setting INDERES_OAUTH_TOKENS_JSON populates the cache file."""
    tokens = _fake_tokens_dict()
    monkeypatch.setenv(oauth.CLOUD_TOKEN_ENV, json.dumps(tokens))

    cache_path = oauth._token_cache_path()
    assert not cache_path.exists()

    assert oauth._bootstrap_from_env() is True
    assert cache_path.exists()

    loaded = oauth._load_tokens()
    assert loaded is not None
    assert loaded.access_token == "fake-access"
    assert loaded.is_fresh


def test_bootstrap_skipped_when_cache_exists(isolated_cache, monkeypatch):
    """Existing cache (e.g. after refresh) is not clobbered by env var."""
    cache_path = oauth._token_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(_fake_tokens_dict("from-disk")))

    monkeypatch.setenv(oauth.CLOUD_TOKEN_ENV, json.dumps(_fake_tokens_dict("from-env")))

    assert oauth._bootstrap_from_env() is False  # didn't overwrite
    loaded = oauth._load_tokens()
    assert loaded is not None
    assert loaded.access_token == "from-disk"


def test_bootstrap_no_env_var(isolated_cache):
    """No env var → no bootstrap, no cache file."""
    assert oauth._bootstrap_from_env() is False
    assert not oauth._token_cache_path().exists()


def test_bootstrap_invalid_json(isolated_cache, monkeypatch):
    """Malformed env var doesn't crash, just returns False."""
    monkeypatch.setenv(oauth.CLOUD_TOKEN_ENV, "{not valid json")
    assert oauth._bootstrap_from_env() is False
    assert not oauth._token_cache_path().exists()


def test_cache_dir_override(tmp_path, monkeypatch):
    """INDERES_AGENT_CACHE points _token_cache_path to a custom location."""
    monkeypatch.setenv(oauth.CACHE_DIR_ENV, str(tmp_path / "custom"))
    assert oauth._cache_dir() == tmp_path / "custom"
    assert oauth._token_cache_path() == tmp_path / "custom" / "tokens.json"


def test_cache_dir_default(monkeypatch):
    """Without override, falls back to ~/.inderes_agent."""
    monkeypatch.delenv(oauth.CACHE_DIR_ENV, raising=False)
    assert oauth._cache_dir().name == ".inderes_agent"


def test_headless_detection_explicit(monkeypatch):
    """STREAMLIT_RUNTIME_ENV=cloud forces headless mode."""
    monkeypatch.setenv("STREAMLIT_RUNTIME_ENV", "cloud")
    monkeypatch.delenv("INDERES_AGENT_FORCE_INTERACTIVE", raising=False)
    assert oauth._is_headless()


def test_headless_detection_force_interactive_overrides(monkeypatch):
    """INDERES_AGENT_FORCE_INTERACTIVE wins over auto-detection."""
    monkeypatch.setenv("STREAMLIT_RUNTIME_ENV", "cloud")
    monkeypatch.setenv("INDERES_AGENT_FORCE_INTERACTIVE", "1")
    assert not oauth._is_headless()


def test_load_tokens_triggers_env_bootstrap(isolated_cache, monkeypatch):
    """_load_tokens calls _bootstrap_from_env when cache doesn't exist."""
    monkeypatch.setenv(oauth.CLOUD_TOKEN_ENV, json.dumps(_fake_tokens_dict()))
    assert not oauth._token_cache_path().exists()

    loaded = oauth._load_tokens()
    assert loaded is not None
    assert loaded.access_token == "fake-access"
    assert oauth._token_cache_path().exists()


def test_refresh_failure_recovers_via_gist_when_rotation_race(
    isolated_cache, monkeypatch
):
    """Cron rotates tokens between cloud's pulls → cloud's refresh fails →
    cloud should fall back to a fresh gist pull and use those tokens.

    This is the exact failure mode observed in production: cron runs every
    5 min, cloud's in-memory refresh_token gets invalidated by cron's
    rotation, next refresh attempt sees "Token is not active". Without
    this recovery, cloud raises HeadlessAuthError and dies.
    """
    # Stale cache: this is what cloud has in memory after its first pull.
    stale = _fake_tokens_dict("stale-access")
    stale["refresh_token"] = "stale-rt"
    stale["expires_at"] = 1.0  # already expired → triggers refresh
    cache_path = oauth._token_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(stale))

    # Gist mode configured so the recovery path can engage.
    monkeypatch.setenv("INDERES_TOKENS_GIST_ID", "fake-gist-id")
    monkeypatch.setenv("INDERES_TOKENS_GH_TOKEN", "fake-gh-token")
    # Suppress the *real* INDERES_OAUTH_TOKENS_JSON env var leaking from
    # CI / shell — the cache_path we just wrote must be the input.
    monkeypatch.delenv(oauth.CLOUD_TOKEN_ENV, raising=False)

    # Set up: gist has *fresh* tokens (cron pushed them after rotating).
    fresh = _fake_tokens_dict("fresh-access")
    fresh["refresh_token"] = "fresh-rt"
    fresh["expires_at"] = 9999999999.0  # still fresh

    pull_calls: list[None] = []

    def fake_pull():
        pull_calls.append(None)
        return oauth.TokenSet.from_dict(fresh)

    # First refresh attempt fails (Keycloak says stale-rt is not active),
    # representing the rotation race.
    refresh_calls: list[oauth.TokenSet] = []

    def fake_refresh(tokens):
        refresh_calls.append(tokens)
        return None  # always fails — cron already rotated everything

    monkeypatch.setattr(oauth, "_pull_tokens_from_gist", fake_pull)
    monkeypatch.setattr(oauth, "_refresh_tokens", fake_refresh)
    # Don't trigger headless error path even if env detection says cloud.
    monkeypatch.setenv("INDERES_AGENT_FORCE_INTERACTIVE", "1")

    token = oauth.get_inderes_access_token()
    assert token == "fresh-access"
    # We tried the stale token first.
    assert len(refresh_calls) == 1
    assert refresh_calls[0].refresh_token == "stale-rt"
    # We pulled the gist as recovery.
    assert len(pull_calls) >= 1


def test_refresh_failure_no_gist_no_recovery(isolated_cache, monkeypatch):
    """Without gist configured, refresh failure still raises HeadlessAuthError.

    The recovery path should only engage when gist mode is configured —
    we don't want to silently swallow refresh failures in non-cloud setups.
    """
    stale = _fake_tokens_dict("stale-access")
    stale["expires_at"] = 1.0
    cache_path = oauth._token_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(stale))

    monkeypatch.delenv("INDERES_TOKENS_GIST_ID", raising=False)
    monkeypatch.delenv("INDERES_TOKENS_GH_TOKEN", raising=False)
    monkeypatch.delenv(oauth.CLOUD_TOKEN_ENV, raising=False)
    monkeypatch.setenv("STREAMLIT_RUNTIME_ENV", "cloud")  # force headless
    monkeypatch.delenv("INDERES_AGENT_FORCE_INTERACTIVE", raising=False)
    monkeypatch.setattr(oauth, "_refresh_tokens", lambda _: None)

    with pytest.raises(oauth.HeadlessAuthError):
        oauth.get_inderes_access_token()
