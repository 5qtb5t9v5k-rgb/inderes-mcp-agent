"""Gemini chat client factory with primary→fallback model selection.

Free-tier Gemini reality (BUILD_SPEC §2.1, §6.8):
  primary  = gemini-3.1-flash-lite           (sometimes 503 UNAVAILABLE)
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


# Error classification — replaces the old loose substring heuristics. The
# previous implementation matched any "429" / "quota" / "resource_exhausted"
# substring anywhere in the exception message, causing two bugs in production:
# (1) per-minute rate limits were misdiagnosed as fatal daily-quota exhaustion
# (the user has to wait until midnight Pacific) when in reality a 60-second
# wait would have succeeded; (2) non-Gemini errors that happened to mention
# "429" or "quota" in their metadata triggered the same false-positive. The
# replacement parses the structured ``APIError`` returned by google-genai and
# reads the ``quotaId`` field that distinguishes daily vs per-minute limits.

_TRANSIENT_HTTP_CODES = frozenset({500, 502, 503, 504})


def _classify_gemini_error(exc: BaseException) -> str:
    """Return one of: ``transient`` / ``rate_limit_minute`` /
    ``rate_limit_day`` / ``other``.

    Only ``rate_limit_day`` should escalate to ``QuotaExhaustedError``.
    ``transient`` and ``rate_limit_minute`` should be retried with backoff.
    ``other`` should propagate immediately — those are bugs, not rate limits.
    """
    try:
        from google.genai.errors import APIError
    except ImportError:
        return "other"

    if not isinstance(exc, APIError):
        return "other"

    code = getattr(exc, "code", None)
    if code in _TRANSIENT_HTTP_CODES:
        return "transient"

    if code != 429:
        return "other"

    # 429 — distinguish per-day from per-minute by reading the quotaId from
    # the response body. Gemini's API returns a structured QuotaFailure with
    # one or more violations; each violation carries a ``quotaId`` that
    # names the specific limit (e.g. ``GenerateRequestsPerMinutePerProject
    # PerModel-FreeTier`` vs ``GenerateRequestsPerDayPerProjectPerModel``).
    details = getattr(exc, "details", None) or {}
    error_block = details.get("error", {}) if isinstance(details, dict) else {}
    detail_list = error_block.get("details", []) if isinstance(error_block, dict) else []
    quota_ids: list[str] = []
    if isinstance(detail_list, list):
        for d in detail_list:
            if not isinstance(d, dict):
                continue
            for v in d.get("violations") or []:
                qid = v.get("quotaId") if isinstance(v, dict) else None
                if qid:
                    quota_ids.append(qid)
    quota_str = " ".join(quota_ids).lower()
    if "perday" in quota_str or "daily" in quota_str:
        return "rate_limit_day"
    # Default for 429 with no quotaId info: treat as per-minute. Safer
    # default than locking the user out for a day — if it actually is daily,
    # the retry will fail again and surface eventually.
    return "rate_limit_minute"


def _log_genai_error(event: str, model: str, exc: BaseException) -> None:
    """Log a Gemini API error with full structural detail.

    Previous code logged only ``falling_back_to_secondary`` without the
    triggering exception, leaving us unable to diagnose what actually
    broke. This helper extracts ``code``, ``status``, ``message``, and
    quota-violation IDs from a Genai ``APIError`` and emits one
    structured warning line. Non-APIError exceptions get a generic
    fallback that still includes type + first 500 chars of ``str(exc)``.
    """
    fields: dict[str, Any] = {
        "event": event,
        "model": model,
        "exc_type": type(exc).__name__,
    }
    try:
        from google.genai.errors import APIError
        if isinstance(exc, APIError):
            fields["code"] = getattr(exc, "code", None)
            fields["status"] = getattr(exc, "status", None)
            fields["message"] = (getattr(exc, "message", None) or "")[:300]
            details = getattr(exc, "details", None) or {}
            error_block = details.get("error", {}) if isinstance(details, dict) else {}
            detail_list = error_block.get("details", []) if isinstance(error_block, dict) else []
            violations: list[dict[str, Any]] = []
            if isinstance(detail_list, list):
                for d in detail_list:
                    if not isinstance(d, dict):
                        continue
                    for v in d.get("violations") or []:
                        if isinstance(v, dict):
                            violations.append({
                                "quotaId": v.get("quotaId"),
                                "quotaMetric": v.get("quotaMetric"),
                            })
            if violations:
                fields["quota_violations"] = violations
    except Exception:  # noqa: BLE001
        # Defensive: never let a logging helper crash the agent
        pass
    fields["raw"] = str(exc)[:500]
    log.warning("%s %s", event, fields)


# ---------------------------------------------------------------------------
# Legacy compatibility shims — keep the old function names working so any
# callers / tests outside this module don't break. New code should use
# ``_classify_gemini_error`` directly.
# ---------------------------------------------------------------------------


def _is_unavailable(exc: BaseException) -> bool:
    """True if the error is a retryable transient (503 etc.).

    Now backed by ``_classify_gemini_error``. Loose substring matching
    retained as a fallback for non-APIError exceptions because callers
    in test code construct plain ``RuntimeError("503 UNAVAILABLE")``.
    """
    if _classify_gemini_error(exc) == "transient":
        return True
    msg = str(exc).lower()
    return "503" in msg or "unavailable" in msg


def _is_quota_exhausted(exc: BaseException) -> bool:
    """True only for genuine per-day quota exhaustion.

    Replaces the old promiscuous substring matcher. Per-minute rate
    limits, concurrent-request limits, and other 429 sub-variants no
    longer trigger this — they go through the backoff retry path
    instead.
    """
    return _classify_gemini_error(exc) == "rate_limit_day"


def _is_rate_limited_transient(exc: BaseException) -> bool:
    """True for per-minute rate limits or transient 5xx — retry with backoff."""
    kind = _classify_gemini_error(exc)
    return kind in ("rate_limit_minute", "transient")


class QuotaExhaustedError(RuntimeError):
    """Raised when both primary and fallback exhaust their per-DAY quota.

    Per-minute rate limits and transient 5xx errors are handled by the
    backoff-retry loop in ``_fallback_call`` and do NOT raise this
    error. If you see this, the day-quota is genuinely exhausted.
    """


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

        # DEBUG: print state so we can see what's actually in config when
        # LEAD (Pro) gets rejected by the API. Tag the agent name so we know
        # which call is being prepared.
        import os
        if os.environ.get("INDERES_DEBUG_GEMINI_CONFIG"):
            print(
                f"[gemini-config] model={getattr(self, 'primary_model', '?')} "
                f"tools_count={len(getattr(config, 'tools', None) or [])} "
                f"tool_config={config.tool_config!r}",
                flush=True,
            )

        # Gemini Pro rejects requests that carry a ``function_calling_config``
        # without any ``function_declarations`` in ``tools``. Flash silently
        # accepts the same request. Inspect the resolved Tool list and clear
        # ``function_calling_config`` whenever no declarations exist — this
        # covers LEAD (tools=None) AND any tool list that contains only
        # server-side tools (code_execution etc., which don't carry
        # function_declarations).
        has_function_declarations = any(
            getattr(t, "function_declarations", None)
            for t in (getattr(config, "tools", None) or [])
        )
        if not has_function_declarations:
            # Both are required: clear tool_config entirely AND clear tools.
            # If config.tools is even an empty list, Pro's strict validator
            # may still complain about the dangling tool_config setup.
            if config.tool_config is not None:
                config.tool_config = None
            # Also reset config.tools to None — empty list and None are
            # treated differently by the API surface.
            try:
                config.tools = None
            except Exception:
                pass

        if self._has_server_side_tool(options):
            tool_config = config.tool_config or genai_types.ToolConfig()
            tool_config.include_server_side_tool_invocations = True
            config.tool_config = tool_config

        if os.environ.get("INDERES_DEBUG_GEMINI_CONFIG"):
            print(
                f"[gemini-config-after] model={getattr(self, 'primary_model', '?')} "
                f"tools={getattr(config, 'tools', None)!r} "
                f"tool_config={config.tool_config!r}",
                flush=True,
            )
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
        """Non-streaming path with three layers of resilience:

        1. **In-place retry with backoff** for per-minute rate limits and
           transient 5xx on the *primary* model. Most of the failures
           tonight (2026-05-11 21:04–21:19) were sub-400ms immediate
           rejects — almost certainly per-minute caps or concurrent-
           request limits that resolve in 30–90 s. Used to give up
           immediately by falling back, which then ALSO rate-limited,
           and the user saw a misleading "Daily quota exhausted" error.
        2. **Fallback to secondary model** only after primary's local
           retries are exhausted, or for non-rate-limit failures (e.g.
           transient 5xx that retries can't resolve).
        3. **QuotaExhaustedError** only when classification confirms a
           per-DAY quota with quotaId containing "perday"/"daily".
        """
        async def _send() -> Any:
            self._set_model(self.primary_model)
            return await super(FallbackGeminiChatClient, self).get_response(messages, *args, **kwargs)

        # Local retry on the primary first — handles the most common
        # case (transient burst hitting per-minute cap during fan-out).
        primary_exc: BaseException | None = None
        for attempt in range(3):
            try:
                return await _send()
            except Exception as exc:
                primary_exc = exc
                kind = _classify_gemini_error(exc)
                if kind == "rate_limit_day":
                    # Genuine daily exhaustion — go straight to fallback,
                    # don't waste attempts on a model that's locked out.
                    _log_genai_error("primary_day_quota_exhausted", self.primary_model, exc)
                    break
                if kind in ("rate_limit_minute", "transient"):
                    if attempt == 2:
                        _log_genai_error(
                            "primary_retries_exhausted", self.primary_model, exc,
                        )
                        break
                    wait = (attempt + 1) * 30  # 30s, 60s
                    log.warning(
                        "primary_retry attempt=%d wait=%ds kind=%s exc_type=%s",
                        attempt + 1, wait, kind, type(exc).__name__,
                    )
                    await asyncio.sleep(wait)
                    continue
                # `other` — propagate immediately, this is a bug not a rate limit
                _log_genai_error("primary_non_retryable_error", self.primary_model, exc)
                raise

        # If we reach here, primary failed despite retries. Try the fallback.
        if primary_exc is not None:
            return await self._fallback_call(messages, primary_exc, *args, **kwargs)
        # Defensive: should be unreachable
        raise RuntimeError("primary exhausted without recording exception")

    async def _fallback_call(
        self,
        messages,
        primary_exc: BaseException,
        *args: Any,
        **kwargs: Any,
    ):
        """Switch to fallback model. Same retry-with-backoff discipline as primary."""
        _log_genai_error(
            "falling_back_to_secondary",
            f"{self.primary_model}→{self.fallback_model}",
            primary_exc,
        )
        self.fallback_event_count += 1
        self._set_model(self.fallback_model)

        fallback_exc: BaseException | None = None
        for attempt in range(3):
            try:
                return await super().get_response(messages, *args, **kwargs)
            except Exception as exc:
                fallback_exc = exc
                kind = _classify_gemini_error(exc)
                if kind == "rate_limit_day":
                    _log_genai_error("fallback_day_quota_exhausted", self.fallback_model, exc)
                    raise QuotaExhaustedError(
                        "Genuine daily quota exhausted on BOTH "
                        f"{self.primary_model} AND {self.fallback_model}. "
                        "Check https://aistudio.google.com/app/usage for confirmation. "
                        "Resets at 00:00 Pacific (10:00 Helsinki)."
                    ) from exc
                if kind in ("rate_limit_minute", "transient"):
                    if attempt == 2:
                        _log_genai_error(
                            "fallback_retries_exhausted", self.fallback_model, exc,
                        )
                        # Re-raise the LAST seen exception with full context,
                        # rather than the misleading "daily quota" message
                        raise RuntimeError(
                            f"Rate-limited on both primary ({type(primary_exc).__name__}) "
                            f"and fallback after retries. Latest fallback error: {exc!s}"
                        ) from exc
                    wait = (attempt + 1) * 30
                    log.warning(
                        "fallback_retry attempt=%d wait=%ds kind=%s",
                        attempt + 1, wait, kind,
                    )
                    await asyncio.sleep(wait)
                    continue
                # `other` — non-retryable, surface as-is
                _log_genai_error("fallback_non_retryable_error", self.fallback_model, exc)
                raise

        if fallback_exc is not None:
            raise fallback_exc
        raise RuntimeError("fallback exhausted without recording exception")

    async def _streaming_stream(self, messages, *args: Any, **kwargs: Any):
        """Streaming path: re-stream on fallback. Yields chunks.

        Streaming retries are trickier than non-streaming — we can't sleep
        between chunks. Strategy: if the initial stream-open fails with a
        retryable error, we fall back to the secondary model immediately
        (no in-place retry). The non-streaming path absorbs most rate-limit
        bursts before agents ever reach streaming output.
        """
        try:
            self._set_model(self.primary_model)
            stream = super().get_response(messages, *args, **kwargs)
            stream = await stream if inspect.isawaitable(stream) else stream
            async for chunk in stream:
                yield chunk
            return
        except Exception as exc:
            kind = _classify_gemini_error(exc)
            if kind == "other":
                raise
            _log_genai_error("streaming_falling_back", self.primary_model, exc)
            self.fallback_event_count += 1
            self._set_model(self.fallback_model)
            try:
                stream = super().get_response(messages, *args, **kwargs)
                stream = await stream if inspect.isawaitable(stream) else stream
                async for chunk in stream:
                    yield chunk
            except Exception as exc2:
                kind2 = _classify_gemini_error(exc2)
                if kind2 == "rate_limit_day":
                    _log_genai_error("streaming_fallback_day_quota", self.fallback_model, exc2)
                    raise QuotaExhaustedError(
                        "Genuine daily quota exhausted on BOTH "
                        f"{self.primary_model} AND {self.fallback_model}."
                    ) from exc2
                _log_genai_error("streaming_fallback_failed", self.fallback_model, exc2)
                raise


def build_chat_client(
    settings: Settings | None = None,
    primary_model: str | None = None,
) -> FallbackGeminiChatClient:
    """Single factory; all agents must use this rather than constructing
    GeminiChatClient directly.

    ``primary_model`` overrides ``settings.PRIMARY_MODEL`` for callers that
    want a stronger model (e.g. LEAD's deep-mode synthesis using Pro).
    The fallback always stays on the configured FALLBACK_MODEL — so even
    if the override (e.g. Pro) hits a 503 / quota cap, the agent still
    answers via Flash rather than failing the query.
    """
    s = settings or get_settings()
    primary = primary_model or s.PRIMARY_MODEL
    return FallbackGeminiChatClient(
        primary_model=primary,
        fallback_model=s.FALLBACK_MODEL,
        api_key=s.require_gemini_key(),
        retry_delay_ms=s.RETRY_DELAY_MS,
        max_retries=s.MAX_RETRIES,
    )
