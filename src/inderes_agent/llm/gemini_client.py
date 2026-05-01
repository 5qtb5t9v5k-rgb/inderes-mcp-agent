"""Gemini chat client factory with primary→fallback model selection.

Free-tier Gemini reality (BUILD_SPEC §2.1, §6.8):
  primary  = gemini-3.1-flash-lite-preview  (sometimes 503 UNAVAILABLE)
  fallback = gemini-2.5-flash               (more reliable)
  Pro-tier models are quota-zero on free tier — do not select them.

We subclass `GeminiChatClient` (verified API: single `get_response(messages, *, stream=False, ...)`)
and intercept it. On 503 from primary we retry once after RETRY_DELAY_MS, and on a second 503 — or
any 429 — we switch to FALLBACK_MODEL for that single call. The model that handled the request is
recorded on `last_used_model` for /trace.

Streaming and non-streaming paths share the same wrapper: `get_response` returns either an awaitable
(stream=False) or a `ResponseStream` (stream=True). We handle both shapes generically.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from agent_framework_gemini import GeminiChatClient
from google.genai import types as genai_types

from ..settings import Settings, get_settings

log = logging.getLogger(__name__)


def _is_unavailable(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "503" in msg or "unavailable" in msg


def _is_quota_exhausted(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "resource_exhausted" in msg or "quota" in msg


class QuotaExhaustedError(RuntimeError):
    """Raised when both primary and fallback exhaust quota (BUILD_SPEC §6.8)."""


class FallbackGeminiChatClient(GeminiChatClient):
    """Drop-in `GeminiChatClient` that retries primary then falls back to a secondary model.

    All chat requests route through `get_response`, which we override. The base class
    machinery (tool calling, structured output) is untouched.
    """

    def __init__(
        self,
        *,
        primary_model: str,
        fallback_model: str,
        api_key: str,
        retry_delay_ms: int = 1000,
        max_retries: int = 1,
        **kwargs: Any,
    ) -> None:
        super().__init__(api_key=api_key, model=primary_model, **kwargs)
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.retry_delay = retry_delay_ms / 1000.0
        self.max_retries = max_retries
        self.last_used_model: str = primary_model
        self.fallback_event_count: int = 0

    def _set_model(self, model: str) -> None:
        # Verified: GeminiChatClient stores model on self.model.
        self.model = model
        self.last_used_model = model

    # ------------------------------------------------------------------
    # Hybrid tool-config fix: when an agent's tool list mixes server-side
    # built-ins (e.g. code_execution) with client-side function-calling tools
    # (e.g. MCP), the Gemini API rejects the request unless we set
    # `tool_config.include_server_side_tool_invocations=True`. The base
    # GeminiChatClient builds tool_config only from the user's `tool_choice`
    # and never adds this flag. We override `_prepare_config` to inject it
    # whenever the resolved tool list contains a server-side tool.
    # ------------------------------------------------------------------

    def _has_server_side_tool(self, options) -> bool:
        for t in options.get("tools") or []:
            if isinstance(t, genai_types.Tool):
                if t.code_execution is not None:
                    return True
                if getattr(t, "google_search", None) is not None:
                    return True
                if getattr(t, "google_maps", None) is not None:
                    return True
                if getattr(t, "url_context", None) is not None:
                    return True
        return False

    def _prepare_config(self, options, system_instruction):  # type: ignore[override]
        config = super()._prepare_config(options, system_instruction)
        if self._has_server_side_tool(options):
            tool_config = config.tool_config or genai_types.ToolConfig()
            tool_config.include_server_side_tool_invocations = True
            config.tool_config = tool_config
        return config

    def get_response(self, messages, *args: Any, **kwargs: Any):  # type: ignore[override]
        """Override sync entry point. Routes through the fallback handler."""
        return self._dispatch(messages, *args, **kwargs)

    def _dispatch(self, messages, *args: Any, **kwargs: Any):
        """Decide once whether we're in streaming mode and dispatch accordingly.

        Streaming returns a sync `ResponseStream` (an async iterable); non-streaming
        returns an awaitable. We must NOT `await` a stream, so branch up front.
        """
        is_stream = kwargs.get("stream", False)
        if is_stream:
            return self._streaming_stream(messages, *args, **kwargs)
        return self._awaitable_call(messages, *args, **kwargs)

    async def _awaitable_call(self, messages, *args: Any, **kwargs: Any):
        """Non-streaming: try primary, retry on 503, fall back on persistent failure."""
        async def _send() -> Any:
            self._set_model(self.primary_model)
            return await super(FallbackGeminiChatClient, self).get_response(messages, *args, **kwargs)

        try:
            return await _send()
        except Exception as exc:
            if _is_unavailable(exc) and self.max_retries > 0:
                log.warning("primary_model_503_retry model=%s", self.primary_model)
                await asyncio.sleep(self.retry_delay)
                try:
                    return await _send()
                except Exception as exc2:
                    if _is_unavailable(exc2) or _is_quota_exhausted(exc2):
                        return await self._fallback_call(messages, *args, **kwargs)
                    raise
            if _is_unavailable(exc) or _is_quota_exhausted(exc):
                return await self._fallback_call(messages, *args, **kwargs)
            raise

    async def _fallback_call(self, messages, *args: Any, **kwargs: Any):
        log.warning(
            "falling_back_to_secondary primary=%s fallback=%s",
            self.primary_model,
            self.fallback_model,
        )
        self.fallback_event_count += 1
        self._set_model(self.fallback_model)
        # Give the fallback model 2 attempts with backoff — when Gemini's load is high,
        # both flash-lite-preview and 2.5-flash can both 503 simultaneously, but a few
        # seconds later the fallback typically recovers.
        last_exc: BaseException | None = None
        for attempt in range(2):
            try:
                return await super().get_response(messages, *args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if _is_quota_exhausted(exc):
                    raise QuotaExhaustedError(
                        "Daily Gemini quota exhausted on both primary and fallback models. "
                        "Try again tomorrow or upgrade to paid tier."
                    ) from exc
                if not _is_unavailable(exc) or attempt == 1:
                    raise
                log.warning("fallback_503_retry attempt=%d", attempt + 1)
                await asyncio.sleep(self.retry_delay * (attempt + 1) * 2)  # 2s, 4s
        # Should not reach here
        if last_exc:
            raise last_exc
        raise RuntimeError("fallback exhausted without explicit error")

    async def _streaming_stream(self, messages, *args: Any, **kwargs: Any):
        """Streaming path: re-stream on fallback. Yields chunks."""
        try:
            self._set_model(self.primary_model)
            stream = super().get_response(messages, *args, **kwargs)
            stream = await stream if inspect.isawaitable(stream) else stream
            async for chunk in stream:
                yield chunk
            return
        except Exception as exc:
            if not (_is_unavailable(exc) or _is_quota_exhausted(exc)):
                raise
            log.warning(
                "streaming_falling_back primary=%s fallback=%s",
                self.primary_model,
                self.fallback_model,
            )
            self.fallback_event_count += 1
            self._set_model(self.fallback_model)
            try:
                stream = super().get_response(messages, *args, **kwargs)
                stream = await stream if inspect.isawaitable(stream) else stream
                async for chunk in stream:
                    yield chunk
            except Exception as exc2:
                if _is_quota_exhausted(exc2):
                    raise QuotaExhaustedError(
                        "Daily Gemini quota exhausted on both primary and fallback models."
                    ) from exc2
                raise


def build_chat_client(settings: Settings | None = None) -> FallbackGeminiChatClient:
    """Single factory; all agents must use this rather than constructing GeminiChatClient directly."""
    s = settings or get_settings()
    return FallbackGeminiChatClient(
        primary_model=s.PRIMARY_MODEL,
        fallback_model=s.FALLBACK_MODEL,
        api_key=s.require_gemini_key(),
        retry_delay_ms=s.RETRY_DELAY_MS,
        max_retries=s.MAX_RETRIES,
    )
