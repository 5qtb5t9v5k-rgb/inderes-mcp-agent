"""Streamlit chat UI for inderes-research-agent.

Run locally:
    streamlit run ui/app.py

This is intentionally a thin wrapper over `inderes_agent`'s existing handle_query
flow. The agent does its work the same way as in the CLI; this just renders it
in a browser with chat history, live phase indicators, and an expandable trace.

Hosting note: this MVP is local-only. The Inderes OAuth flow opens the browser
on first run for a localhost callback. Hosting on Streamlit Cloud would require
either pre-baking your token as a secret (single-user) or registering the
deployed URL with Inderes Keycloak (multi-user). See ARCHITECTURE.md for the
auth pipeline.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from inderes_agent.cli.repl import ConversationState
from inderes_agent.llm.gemini_client import QuotaExhaustedError
from inderes_agent.logging import configure_logging
from inderes_agent.mcp.inderes_client import prefetch_token
from inderes_agent.observability.narrate import write_narrative
from inderes_agent.observability.run_log import (
    RUNS_ROOT,
    attach_console_log_handler,
    detach_console_log_handler,
    new_run_dir,
    write_run,
)
from inderes_agent.orchestration.router import classify_query
from inderes_agent.orchestration.synthesis import synthesize
from inderes_agent.orchestration.workflows import run_workflow

# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="inderes-mcp-agent-system",
    page_icon="📊",
    layout="wide",
)

st.title("📊 inderes-mcp-agent-system")
st.caption(
    "Multi-agent research over Inderes MCP. "
    "Surfaces signals — never gives buy/sell calls. "
    "Personal project, not affiliated with Inderes Oyj."
)


# ---------------------------------------------------------------------------
# One-time setup (per Streamlit session)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Authenticating with Inderes…")
def _bootstrap() -> None:
    """Run-once-per-session: load env, configure logging, prefetch OAuth token."""
    load_dotenv()
    configure_logging()
    prefetch_token()


_bootstrap()


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "state" not in st.session_state:
    st.session_state.state = ConversationState()
if "history" not in st.session_state:
    # List of {role: "user"|"assistant", content: str, run_dir: str | None}
    st.session_state.history = []


# ---------------------------------------------------------------------------
# Trace rendering helper
# ---------------------------------------------------------------------------

def _render_subagent_text(run_dir: Path, sa: dict) -> None:
    """Render a subagent's text plus any extracted images.

    The `text` field is markdown that may contain `![chart](images/foo.png)`
    references — Streamlit's `st.markdown` won't resolve relative paths from
    the run dir, so we render the markdown without images and then explicitly
    `st.image()` each saved image.
    """
    import re

    text = sa.get("text") or "_(empty response)_"
    image_paths = sa.get("image_paths") or []

    # Strip image markdown so it doesn't render as broken links.
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)\s*", "", text).strip()
    if cleaned:
        st.markdown(cleaned)

    for rel in image_paths:
        img_path = run_dir / rel
        if img_path.exists():
            st.image(str(img_path), use_container_width=True)


def render_trace_expander(run_dir: Path) -> None:
    """Show routing + per-subagent + tool-call trace inside an expander."""
    routing_path = run_dir / "routing.json"
    meta_path = run_dir / "meta.json"

    with st.expander("🔍 Subagent trace", expanded=False):
        if routing_path.exists():
            r = json.loads(routing_path.read_text(encoding="utf-8"))
            cols = st.columns(3)
            cols[0].metric("Domains", " + ".join(r.get("domains", [])))
            cols[1].metric("Companies", ", ".join(r.get("companies", [])) or "—")
            cols[2].metric("Comparison", "yes" if r.get("is_comparison") else "no")
            if r.get("reasoning"):
                st.caption(f"Routing reasoning: _{r['reasoning']}_")

        if meta_path.exists():
            m = json.loads(meta_path.read_text(encoding="utf-8"))
            cols = st.columns(4)
            cols[0].metric("Duration", f"{m.get('duration_seconds', 0):.1f} s")
            cols[1].metric("Subagents", m.get("subagent_count", 0))
            cols[2].metric("Errors", m.get("subagent_errors", 0))
            cols[3].metric("Fallbacks", m.get("fallback_events", 0))

        for sub_path in sorted(run_dir.glob("subagent-*.json")):
            sa = json.loads(sub_path.read_text(encoding="utf-8"))
            domain = sa.get("domain", "?")
            company = sa.get("company")
            model = sa.get("model_used", "?")
            err = sa.get("error")

            head = f"**{domain}**" + (f" — {company}" if company else "")
            head += f"  · `{model}`"
            head += "  · ❌ ERROR" if err else "  · ✓ ok"
            st.markdown(head)

            if err:
                st.error(err)
            else:
                with st.container(border=True):
                    _render_subagent_text(run_dir, sa)

        narrative_path = run_dir / "narrative.md"
        if narrative_path.exists():
            st.caption(f"Full narrative: `{narrative_path}`")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.subheader("Conversation")
    if st.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.state = ConversationState()
        st.session_state.history = []
        st.rerun()

    st.subheader("Recent runs")
    if RUNS_ROOT.exists():
        recent = sorted(RUNS_ROOT.iterdir(), reverse=True)[:8]
        for d in recent:
            qfile = d / "query.txt"
            label = qfile.read_text(encoding="utf-8").strip() if qfile.exists() else d.name
            label = label[:60] + ("…" if len(label) > 60 else "")
            st.caption(f"`{d.name[:15]}` — {label}")
    else:
        st.caption("No runs yet.")

    st.subheader("About")
    st.caption(
        "5 agents (lead + quant/research/sentiment/portfolio) "
        "running on Microsoft Agent Framework + Gemini Flash. "
        "Per-run logs at `~/.inderes_agent/runs/`."
    )


# ---------------------------------------------------------------------------
# Render past history
# ---------------------------------------------------------------------------

for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("run_dir"):
            render_trace_expander(Path(msg["run_dir"]))


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

async def run_pipeline(query: str, state: ConversationState, status) -> tuple[str, Path]:
    """Execute the full router → workflow → synthesis pipeline with status updates."""
    run_dir = new_run_dir()
    handler = attach_console_log_handler(run_dir)
    t0 = time.time()
    try:
        status.write("⚙️  Reitittäjä päättää mitä subagentteja käytetään…")
        ctx = ", ".join(state.last_companies) if state.last_companies else ""
        ctx_hint = f"previous turn discussed: {ctx}" if ctx else ""
        classification = await classify_query(query, conversation_context=ctx_hint)
        if not classification.companies and state.last_companies:
            classification.companies = state.last_companies
        status.write(
            f"✓ Reititys: **{' + '.join(d.value for d in classification.domains)}**"
            + (f" · {', '.join(classification.companies)}" if classification.companies else "")
        )

        status.write("⚙️  Subagentit ajetaan rinnakkain…")
        workflow_result = await run_workflow(query, classification, run_dir=run_dir)
        for sr in workflow_result.subagent_results:
            mark = "❌ ERROR" if sr.error else "✓"
            company = f" — {sr.company}" if sr.company else ""
            status.write(f"  {mark} {sr.domain.value}{company} ({sr.model_used})")

        status.write("⚙️  Lead syntesoi vastauksen…")
        answer, lead_model = await synthesize(query, workflow_result)

        write_run(
            run_dir=run_dir,
            query=query,
            workflow=workflow_result,
            answer=answer,
            lead_model=lead_model,
            duration_s=time.time() - t0,
        )
        write_narrative(run_dir)

        # Update state for follow-up turns
        state.last_workflow = workflow_result
        state.last_lead_model = lead_model
        state.last_companies = classification.companies or state.last_companies
        state.last_summary = answer[:400]
        state.last_run_dir = str(run_dir)
        for sr in workflow_result.subagent_results:
            state.invoked_agents.add(f"aino-{sr.domain.value}")

        return answer, run_dir
    finally:
        detach_console_log_handler(handler)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

prompt = st.chat_input("Kysy jotain Pohjoismaisista osakkeista…")

if prompt:
    st.session_state.history.append({"role": "user", "content": prompt, "run_dir": None})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("Käsittelen kysymystäsi…", expanded=True)
        try:
            answer, run_dir = asyncio.run(
                run_pipeline(prompt, st.session_state.state, status)
            )
            status.update(label="Valmis", state="complete", expanded=False)
            st.markdown(answer)
            render_trace_expander(run_dir)
            st.session_state.history.append(
                {"role": "assistant", "content": answer, "run_dir": str(run_dir)}
            )
        except QuotaExhaustedError as exc:
            status.update(label="Quota exhausted", state="error", expanded=True)
            st.error(str(exc))
        except Exception as exc:
            status.update(label="Error", state="error", expanded=True)
            st.error(f"{type(exc).__name__}: {exc}")
