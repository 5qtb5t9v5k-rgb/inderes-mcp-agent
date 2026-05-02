"""Interactive REPL with slash commands and conversation memory."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console

from ..llm.gemini_client import QuotaExhaustedError
from ..observability.narrate import summarize_run, write_narrative
from ..observability.run_log import (
    attach_console_log_handler,
    detach_console_log_handler,
    new_run_dir,
    write_run,
)
from ..orchestration.router import classify_query
from ..orchestration.synthesis import synthesize
from ..orchestration.workflows import WorkflowResult, run_workflow
from . import render

console = Console()

HELP = """\
Slash commands:
  /help     show this help
  /clear    clear conversation history
  /agents   show subagents invoked this session
  /trace    show last query's subagent outputs and models
  /explain  print a human-readable narrative of the last run
  /last     print the directory of the last run's full log
  /runs     list the 10 most recent run directories
  /exit     quit
"""


@dataclass
class ConversationState:
    """Lightweight conversation context — last turn summary for router continuity."""

    last_summary: str = ""
    last_companies: list[str] = field(default_factory=list)
    invoked_agents: set[str] = field(default_factory=set)
    last_workflow: WorkflowResult | None = None
    last_lead_model: str = ""
    last_run_dir: str | None = None


def _build_context(state: ConversationState) -> str:
    if not state.last_companies:
        return ""
    return f"previous turn discussed: {', '.join(state.last_companies)}"


async def handle_query(query: str, state: ConversationState) -> None:
    run_dir = new_run_dir()
    log_handler = attach_console_log_handler(run_dir)
    t0 = time.time()
    try:
        console.print(f"[dim]·[/dim] [cyan]reitittäjä päättää mitä subagentteja käytetään…[/cyan]")
        t = time.time()
        classification = await classify_query(query, conversation_context=_build_context(state))
        console.print(f"[dim]· reititys valmis ({time.time()-t:.1f}s)[/dim]")

        # Continuity: if router didn't pick up a company name but we have context, inherit.
        if not classification.companies and state.last_companies:
            classification.companies = state.last_companies

        render.render_routing(classification)

        console.print(f"[dim]·[/dim] [cyan]subagentit ajetaan, voi kestää 30–90 s…[/cyan]")
        t = time.time()
        workflow_result = await run_workflow(query, classification, run_dir=run_dir)
        for sr in workflow_result.subagent_results:
            tag = "[red]ERROR[/red]" if sr.error else "[green]ok[/green]"
            company = f" — {sr.company}" if sr.company else ""
            console.print(f"[dim]·   {sr.domain.value}{company}: {tag} ({sr.model_used})[/dim]")
        console.print(f"[dim]· subagentit valmiit ({time.time()-t:.1f}s)[/dim]")

        console.print(f"[dim]·[/dim] [cyan]lead syntesoi vastauksen…[/cyan]")
        t = time.time()
        answer, lead_model = await synthesize(query, workflow_result)
        console.print(f"[dim]· synthesis valmis ({time.time()-t:.1f}s, malli={lead_model})[/dim]")
        console.print()

        render.render_answer(answer)

        write_run(
            run_dir=run_dir,
            query=query,
            workflow=workflow_result,
            answer=answer,
            lead_model=lead_model,
            duration_s=time.time() - t0,
        )

        state.last_workflow = workflow_result
        state.last_lead_model = lead_model
        state.last_companies = classification.companies or state.last_companies
        state.last_summary = answer[:400]
        state.last_run_dir = str(run_dir)
        for sr in workflow_result.subagent_results:
            state.invoked_agents.add(f"aino-{sr.domain.value}")

        # Always emit a narrative.md alongside the raw logs so the user can re-read
        # what happened in plain language.
        narrative_path = write_narrative(run_dir)
        render.render_info(f"run log: {run_dir}")
        render.render_info(f"narrative: {narrative_path}")
    finally:
        detach_console_log_handler(log_handler)


async def repl() -> None:
    session: PromptSession = PromptSession(history=InMemoryHistory())
    state = ConversationState()

    render.render_info("inderes-research-agent — type /help for commands, /exit to quit")

    while True:
        try:
            line = await session.prompt_async("> ")
        except (EOFError, KeyboardInterrupt):
            return

        line = line.strip()
        if not line:
            continue

        if line in {"/exit", "/quit"}:
            return
        if line == "/help":
            console.print(HELP)
            continue
        if line == "/clear":
            state = ConversationState()
            render.render_info("conversation cleared.")
            continue
        if line == "/agents":
            agents = sorted(state.invoked_agents) or ["(none yet)"]
            console.print("Invoked this session: " + ", ".join(agents))
            continue
        if line == "/trace":
            if state.last_workflow is None:
                console.print("No query yet.")
            else:
                render.render_trace("(last query)", state.last_workflow, state.last_lead_model)
            continue
        if line == "/last":
            if state.last_run_dir:
                console.print(state.last_run_dir)
            else:
                console.print("No query yet.")
            continue
        if line == "/explain":
            if state.last_run_dir:
                from rich.markdown import Markdown
                from pathlib import Path

                console.print(Markdown(summarize_run(Path(state.last_run_dir))))
            else:
                console.print("No query yet.")
            continue
        if line == "/runs":
            from ..observability.run_log import RUNS_ROOT

            if not RUNS_ROOT.exists():
                console.print("No runs yet.")
            else:
                dirs = sorted(RUNS_ROOT.iterdir(), reverse=True)[:10]
                for d in dirs:
                    q = (d / "query.txt").read_text(encoding="utf-8").strip() if (d / "query.txt").exists() else "(no query.txt)"
                    console.print(f"{d.name}  {q[:80]}")
            continue

        try:
            await handle_query(line, state)
        except QuotaExhaustedError as exc:
            render.render_error(str(exc))
        except Exception as exc:
            render.render_error(f"{type(exc).__name__}: {exc}")
