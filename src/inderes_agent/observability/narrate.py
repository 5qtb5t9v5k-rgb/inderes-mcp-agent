"""Generate a human-readable narrative of a single agent run from its log files.

Combines:
  - query.txt        — what the user asked
  - routing.json     — what the router decided and why
  - subagent-*.json  — what each subagent returned
  - synthesis.txt    — the lead's final answer
  - meta.json        — duration, fallback events
  - console.log      — per-tool-call timing and fallback events

Output is a markdown-friendly timeline. Designed for both terminal viewing (via the
REPL `/explain` command and one-shot post-run print) and saving as `narrative.md`
inside the run directory.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ---- log-line parsing -------------------------------------------------------

_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+"
    r"(?P<level>\w+)\s+(?P<name>\S+)\s+—\s+(?P<msg>.+)$"
)


@dataclass
class LogEvent:
    ts: datetime
    level: str
    logger: str
    msg: str


def _parse_log(path: Path) -> list[LogEvent]:
    events: list[LogEvent] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S,%f")
        events.append(
            LogEvent(
                ts=ts,
                level=m.group("level"),
                logger=m.group("name"),
                msg=m.group("msg"),
            )
        )
    return events


@dataclass
class ToolCall:
    name: str
    started_at: datetime
    ended_at: datetime | None = None
    succeeded: bool | None = None

    @property
    def duration_s(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()


def _extract_tool_calls(events: list[LogEvent]) -> list[ToolCall]:
    """Pair `Function name: X` with the next `Function X succeeded.` for that name."""
    calls: list[ToolCall] = []
    open_calls: dict[str, ToolCall] = {}
    for ev in events:
        if ev.logger != "agent_framework":
            continue
        m = re.match(r"^Function name: (?P<n>\S+)$", ev.msg)
        if m:
            tc = ToolCall(name=m.group("n"), started_at=ev.ts)
            calls.append(tc)
            open_calls[tc.name] = tc
            continue
        m = re.match(r"^Function (?P<n>\S+) (?P<status>succeeded|failed)\.?$", ev.msg)
        if m:
            name = m.group("n")
            tc = open_calls.pop(name, None)
            if tc is not None:
                tc.ended_at = ev.ts
                tc.succeeded = m.group("status") == "succeeded"
    return calls


def _count_fallbacks(events: list[LogEvent]) -> tuple[int, int]:
    """Return (503_retries, fallback_to_secondary_count)."""
    retries = sum(1 for e in events if "primary_model_503_retry" in e.msg)
    fallbacks = sum(1 for e in events if "falling_back_to_secondary" in e.msg)
    return retries, fallbacks


# ---- narrative composition --------------------------------------------------

QUANT_ONLY_TOOLS = {"get-fundamentals", "get-inderes-estimates"}
RESEARCH_ONLY_TOOLS = {
    "list-content",
    "get-content",
    "list-transcripts",
    "get-transcript",
    "list-company-documents",
    "get-document",
    "read-document-sections",
}
SENTIMENT_ONLY_TOOLS = {
    "list-insider-transactions",
    "search-forum-topics",
    "get-forum-posts",
    "list-calendar-events",
}
PORTFOLIO_ONLY_TOOLS = {"get-model-portfolio-content", "get-model-portfolio-price"}


def _attribute_tool(tool_name: str) -> str:
    if tool_name in QUANT_ONLY_TOOLS:
        return "quant"
    if tool_name in RESEARCH_ONLY_TOOLS:
        return "research"
    if tool_name in SENTIMENT_ONLY_TOOLS:
        return "sentiment"
    if tool_name in PORTFOLIO_ONLY_TOOLS:
        return "portfolio"
    return "?"  # search-companies is shared


def _short_summary(text: str, max_chars: int = 400) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + " …"


# Per-subagent text in narrative.md is shown in full (no truncation). The text
# may contain code-execution sandbox transcripts (Python source + printed output
# + the agent's own commentary) which can run to several thousand characters —
# truncating these defeats the forensic purpose of the narrative. The compact
# in-terminal trace (in render.py) keeps a tighter cap because it is a glance,
# not a record.


def summarize_run(run_dir: Path) -> str:
    """Produce a human-readable narrative for one run."""
    query = (run_dir / "query.txt").read_text(encoding="utf-8").strip() if (run_dir / "query.txt").exists() else "(unknown)"
    routing = json.loads((run_dir / "routing.json").read_text(encoding="utf-8")) if (run_dir / "routing.json").exists() else {}
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8")) if (run_dir / "meta.json").exists() else {}
    synthesis = (run_dir / "synthesis.txt").read_text(encoding="utf-8").strip() if (run_dir / "synthesis.txt").exists() else "(no synthesis)"

    events = _parse_log(run_dir / "console.log")
    tool_calls = _extract_tool_calls(events)
    retries, fallbacks = _count_fallbacks(events)

    subagent_files = sorted(run_dir.glob("subagent-*.json"))
    subagents = [json.loads(p.read_text(encoding="utf-8")) for p in subagent_files]

    lines: list[str] = []
    lines.append(f"# Ajo — {run_dir.name}\n")
    lines.append(f"**Kysymys:** {query}\n")

    # Routing
    if routing:
        domains = " + ".join(routing.get("domains", []))
        companies = ", ".join(routing.get("companies", [])) or "—"
        comp = " (vertailu)" if routing.get("is_comparison") else ""
        lines.append(f"## 🧭 Reititys")
        lines.append(f"- domains: **{domains}**{comp}")
        lines.append(f"- companies: {companies}")
        if routing.get("reasoning"):
            lines.append(f"- reasoning: _{routing['reasoning']}_")
        lines.append("")

    # Tool-call timeline (single, ordered, attributed by tool name where possible)
    if tool_calls:
        lines.append("## 🔧 Työkalukutsut (aikajana)")
        t0 = tool_calls[0].started_at
        for tc in tool_calls:
            offset = (tc.started_at - t0).total_seconds()
            dur = f"{tc.duration_s:.1f}s" if tc.duration_s is not None else "—"
            agent = _attribute_tool(tc.name)
            agent_tag = f"[{agent}]" if agent != "?" else "[shared]"
            status = "✓" if tc.succeeded else "✗" if tc.succeeded is False else "?"
            lines.append(f"- `[{offset:6.1f}s]` {agent_tag:11} **{tc.name}** {status} ({dur})")
        lines.append("")

    # Per-subagent
    if subagents:
        lines.append("## 🤖 Subagenttien vastaukset")
        for sa in subagents:
            domain = sa.get("domain", "?")
            company = sa.get("company")
            model = sa.get("model_used", "?")
            err = sa.get("error")
            head = f"### {domain}" + (f" — {company}" if company else "") + f"  _(malli: {model})_"
            lines.append(head)
            if err:
                lines.append(f"❌ **virhe:** {err}")
            else:
                # The text is already structured markdown (text + ```python``` code
                # blocks + ``` output blocks + ![chart](images/...) refs) — render
                # it inline so code is highlighted and images display.
                text = (sa.get("text") or "").strip() or "_(empty response)_"
                lines.append(text)
            lines.append("")

    # Synthesis
    lines.append("## ✍️ Lead-agentin synthesis")
    lines.append(f"_(malli: {meta.get('lead_model', '?')})_\n")
    lines.append(synthesis)
    lines.append("")

    # Footer / stats
    duration = meta.get("duration_seconds")
    n_sub = meta.get("subagent_count", len(subagents))
    n_err = meta.get("subagent_errors", 0)
    n_tools = len(tool_calls)
    lines.append("## 📊 Yhteenveto")
    lines.append(
        f"- {n_sub} subagenttia · {n_tools} työkalukutsua · {n_err} virhettä"
        f" · {retries} 503-retryä · {fallbacks} fallbackia"
    )
    if duration is not None:
        m, s = divmod(int(duration), 60)
        lines.append(f"- kesto: {m}:{s:02d}")
    lines.append("")

    return "\n".join(lines)


def write_narrative(run_dir: Path) -> Path:
    """Render summarize_run() into run_dir/narrative.md and return the path."""
    text = summarize_run(run_dir)
    out = run_dir / "narrative.md"
    out.write_text(text, encoding="utf-8")
    return out
