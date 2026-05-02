"""Shared agent helpers: prompt loading, optional code-execution tool list."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from agent_framework_gemini import GeminiChatClient

PROMPTS_DIR = Path(__file__).parent / "prompts"

_FI_WEEKDAYS = {
    0: "maanantai", 1: "tiistai", 2: "keskiviikko", 3: "torstai",
    4: "perjantai", 5: "lauantai", 6: "sunnuntai",
}


def _today_header() -> str:
    """Date-aware preamble injected at the top of every loaded prompt.

    Without this, when a prompt says ``dateFrom=today-90d`` or the user asks
    "mitä tuloksia tänään julkaistaan", Gemini falls back to its training
    cutoff and silently returns dates from a year or more ago. We inject the
    real current date in both ISO and Finnish-weekday form so the model has
    no excuse to guess.
    """
    today = date.today()
    iso = today.isoformat()
    weekday_fi = _FI_WEEKDAYS[today.weekday()]
    return (
        "# CURRENT DATE\n"
        f"Today is **{iso}** ({weekday_fi}). When the user asks about "
        '"today" / "tänään", "this week" / "tällä viikolla", "next 7 days", '
        "or when a tool parameter says `today`, `today-90d`, `today+7d` etc., "
        "compute it from this date. **Never** use a date older than this — "
        "if your training cutoff is earlier, ignore it; this date is "
        "authoritative.\n\n---\n\n"
    )


def today_prompt_prefix() -> str:
    """Same date preamble, formatted as a user-message prefix.

    System instructions sometimes get less attention than the user prompt
    when the conversation is long. Prefix every per-query prompt with the
    same date info so it's the very first token the model attends to.
    """
    today = date.today()
    iso = today.isoformat()
    weekday_fi = _FI_WEEKDAYS[today.weekday()]
    return (
        f"[CURRENT DATE: {iso} — {weekday_fi}. "
        "Use this for any 'today' / 'tänään' references. "
        "Never substitute a date from your training data.]\n\n"
    )


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return _today_header() + path.read_text(encoding="utf-8")


def with_code_execution(*tools: Any) -> list[Any]:
    """Append Gemini's sandboxed code-execution tool to a list of tools.

    Use this for agents that benefit from real Python computation (pandas/numpy
    in a sandbox) rather than the LLM doing arithmetic in its head. See
    `gemini_with_code_execution.py` in agent_framework_gemini's samples for the
    canonical pattern.
    """
    return [*tools, GeminiChatClient.get_code_interpreter_tool()]
