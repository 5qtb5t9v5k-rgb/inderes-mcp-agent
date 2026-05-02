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
