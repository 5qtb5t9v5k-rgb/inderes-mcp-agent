"""Streamlit chat UI for inderes-research-agent.

Run locally:
    streamlit run ui/app.py

Cloud deployment (single-user, public app, password-gated):
    See ui/DEPLOY.md.

The agent code is identical to the CLI — this module just adds:
- a chat surface,
- a password gate (Streamlit secrets) to limit drive-by access,
- a daily query cap so a leaked password can't burn unlimited Gemini quota,
- bridging from st.secrets to os.environ so oauth.py can pick up
  INDERES_OAUTH_TOKENS_JSON in cloud deployments.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import date
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# IMPORTANT: bridge Streamlit secrets to os.environ BEFORE importing any
# inderes_agent module — oauth.py reads INDERES_OAUTH_TOKENS_JSON at module
# load time and again on every token request, but the env var has to be set
# before the very first call.
def _bridge_secrets_to_env() -> None:
    """Copy each Streamlit secret into os.environ so non-Streamlit code can read it.

    Streamlit secrets aren't auto-exposed as env vars. We do it here so
    `inderes_agent.mcp.oauth` (which reads `os.environ["INDERES_OAUTH_TOKENS_JSON"]`)
    works without depending on Streamlit.

    Locally there's typically no secrets.toml — accessing `st.secrets.items()`
    raises `StreamlitSecretNotFoundError` in that case. We treat that as "no
    secrets to bridge" and continue silently.
    """
    try:
        items = list(st.secrets.items())
    except Exception:
        return  # no secrets.toml configured — fine in local dev
    for key, value in items:
        # Nested TOML tables (like [INDERES_OAUTH_TOKENS]) get serialised to JSON.
        if isinstance(value, dict):
            os.environ.setdefault(f"{key}_JSON", json.dumps(value))
        else:
            os.environ.setdefault(key, str(value))


_bridge_secrets_to_env()

from inderes_agent.cli.repl import ConversationState  # noqa: E402
from inderes_agent.llm.gemini_client import QuotaExhaustedError  # noqa: E402
from inderes_agent.logging import configure_logging  # noqa: E402
from inderes_agent.mcp.inderes_client import prefetch_token  # noqa: E402
from inderes_agent.mcp.oauth import HeadlessAuthError  # noqa: E402
from inderes_agent.observability.narrate import write_narrative  # noqa: E402
from inderes_agent.observability.run_log import (  # noqa: E402
    RUNS_ROOT,
    attach_console_log_handler,
    detach_console_log_handler,
    new_run_dir,
    write_run,
)
from inderes_agent.orchestration.router import classify_query  # noqa: E402
from inderes_agent.orchestration.synthesis import synthesize  # noqa: E402
from inderes_agent.orchestration.workflows import run_workflow  # noqa: E402


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="inderes-mcp-agent-system",
    page_icon="📊",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Password gate (Path C: public deployment, simple shared password)
# ---------------------------------------------------------------------------

def _check_password() -> bool:
    """If APP_PASSWORD secret/env is set, require it before rendering the app.

    Returns True iff the user is authenticated (or no password is configured).
    Calls st.stop() in the unauthenticated path, so the rest of the page never
    renders.
    """
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        return True  # no gate configured — open mode, useful in local dev

    if st.session_state.get("authenticated"):
        return True

    st.title("🔒 inderes-mcp-agent-system")
    st.caption("This deployment is password-protected.")
    pw = st.text_input("Password", type="password", key="_pw_input")
    if not pw:
        st.stop()
    if pw == expected:
        st.session_state.authenticated = True
        st.rerun()
    else:
        st.error("Incorrect password.")
        st.stop()
    return False


_check_password()


# ---------------------------------------------------------------------------
# Daily query cap (limits damage if password leaks)
# ---------------------------------------------------------------------------

_QUERY_COUNTER_PATH = Path(os.environ.get("INDERES_AGENT_CACHE", "/tmp/inderes_agent")) / "query_count.json"


def _query_count_today() -> int:
    today = date.today().isoformat()
    if _QUERY_COUNTER_PATH.exists():
        try:
            data = json.loads(_QUERY_COUNTER_PATH.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return int(data.get("count", 0))
        except Exception:
            pass
    return 0


def _increment_query_count() -> int:
    today = date.today().isoformat()
    count = _query_count_today() + 1
    _QUERY_COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _QUERY_COUNTER_PATH.write_text(
        json.dumps({"date": today, "count": count}), encoding="utf-8"
    )
    return count


def _daily_cap() -> int:
    raw = os.environ.get("DAILY_QUERY_CAP", "")
    try:
        return int(raw) if raw else 0  # 0 = no cap
    except ValueError:
        return 0


def _enforce_daily_cap_or_stop() -> None:
    cap = _daily_cap()
    if cap <= 0:
        return
    used = _query_count_today()
    if used >= cap:
        st.error(
            f"Päivittäinen kyselyraja täynnä ({used}/{cap}). "
            "Kokeile huomenna uudelleen."
        )
        st.stop()


# ---------------------------------------------------------------------------
# Title and header
# ---------------------------------------------------------------------------

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
    """Run-once-per-session: load env, configure logging, prefetch OAuth token.

    On Streamlit Cloud `prefetch_token()` should succeed silently if
    INDERES_OAUTH_TOKENS_JSON is in secrets — oauth.py bootstraps the cache
    from the env var. If a fresh login is needed, raises HeadlessAuthError
    which we surface as a clear ops message.
    """
    load_dotenv()
    configure_logging()
    try:
        prefetch_token()
    except HeadlessAuthError as exc:
        st.error(str(exc))
        st.stop()


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
    """Render a subagent's text. Image extraction is not supported in this
    build (`agent_framework_gemini` doesn't surface inline_data parts), so
    we just render the markdown directly. `extract_parts` has already
    stripped any dangling `![alt](...)` references that would render as
    broken icons.
    """
    text = sa.get("text") or "_(empty response)_"
    st.markdown(text)


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
    st.caption(
        "Each query saves a forensic record (query, routing, per-subagent "
        "outputs, synthesis, full timeline) to disk. The 8 most recent are "
        "listed below."
    )
    if RUNS_ROOT.exists():
        recent = sorted(RUNS_ROOT.iterdir(), reverse=True)[:8]
        for d in recent:
            qfile = d / "query.txt"
            label = qfile.read_text(encoding="utf-8").strip() if qfile.exists() else d.name
            label = label[:60] + ("…" if len(label) > 60 else "")
            st.caption(f"`{d.name[:15]}` — {label}")
    else:
        st.caption("No runs yet.")

    cap = _daily_cap()
    if cap > 0:
        used = _query_count_today()
        st.subheader("Daily quota")
        st.progress(min(used / cap, 1.0), text=f"{used} / {cap} queries today")

    st.subheader("About")
    st.caption(
        "5 agents — a lead orchestrator plus four specialized subagents "
        "(quant, research, sentiment, portfolio). Built on Microsoft Agent "
        "Framework + Google Gemini Flash, querying Inderes MCP."
    )
    runs_dir_display = str(RUNS_ROOT)
    storage_note = (
        "Per-run logs saved to ephemeral container storage — they reset on "
        "every container restart."
        if runs_dir_display.startswith("/tmp")
        else "Per-run logs saved persistently."
    )
    st.caption(f"Logs at `{runs_dir_display}`. {storage_note}")


# ---------------------------------------------------------------------------
# Render past history
# ---------------------------------------------------------------------------

for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("run_dir"):
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
    # Enforce the daily cap BEFORE we charge a query against the cap. The
    # increment happens after a successful run.
    _enforce_daily_cap_or_stop()

    st.session_state.history.append({"role": "user", "content": prompt, "run_dir": None})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("Käsittelen kysymystäsi…", expanded=True)
        try:
            answer, run_dir = asyncio.run(
                run_pipeline(prompt, st.session_state.state, status)
            )
            _increment_query_count()
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
