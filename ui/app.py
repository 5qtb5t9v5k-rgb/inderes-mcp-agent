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

# Trading Desk visual layer — pure cosmetics, no agent-pipeline impact.
# `streamlit run ui/app.py` puts `ui/` on sys.path (not the repo root), so we
# import the sibling module by its bare name. Works the same in cloud + local.
from components import (  # noqa: E402
    inject_theme,
    render_titlebar,
    render_ticker,
    render_disclaimer,
    render_routing_card,
    render_metrics_row,
    render_agent_row,
    render_agent_output,
    render_statusbar,
    render_personas_panel,
    render_about_panel,
    render_full_narrative,
    render_sidebar_disclaimer,
    render_github_link,
    CustomStatus,
)


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="inderes-mcp-agent-system",
    page_icon="📊",
    layout="wide",
)

# Trading Desk theme — must run right after set_page_config so the CSS lands
# before any other widget is rendered.
inject_theme()


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
# Title and header — Trading Desk chrome
# ---------------------------------------------------------------------------

_lang = st.session_state.get("ui_lang", "fi")
render_titlebar(_lang)
# render_ticker() — disabled: distracting, replace with a real feed if/when one's available
render_disclaimer(_lang)


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
    """Show routing + per-subagent trace inside a collapsed expander at the
    bottom of the assistant message.

    The whole agent activity log lives behind one outer expander that's closed
    by default — when you open it, you see everything inline (routing card,
    metrics, each agent's full structured output starting with its **Ajatus:**
    line). No nested per-agent expanders inside; the Ajatus row makes it easy
    to scan the agent's reasoning at a glance even with long outputs below.

    The old "Täydellinen ajoloki" full-narrative renderer is removed — it
    rendered the same data twice and was the dominant slowdown as chat
    history grew. narrative.md is still written to disk by the pipeline.
    """
    routing_path = run_dir / "routing.json"
    meta_path = run_dir / "meta.json"
    lang = st.session_state.get("ui_lang", "fi")

    _trace_label = (
        "🔍 Agenttien toimintaloki" if lang == "fi" else "🔍 Agent activity log"
    )
    with st.expander(_trace_label, expanded=False):
        if routing_path.exists():
            r = json.loads(routing_path.read_text(encoding="utf-8"))
            render_routing_card(r, lang)

        # Big metric cards from QUANT's structured output (if present —
        # silently no-ops when the subagent JSON has no `metrics` block).
        render_metrics_row(run_dir, lang)

        if meta_path.exists():
            m = json.loads(meta_path.read_text(encoding="utf-8"))
            cols = st.columns(4)
            cols[0].metric("Duration", f"{m.get('duration_seconds', 0):.1f} s")
            cols[1].metric("Subagents", m.get("subagent_count", 0))
            cols[2].metric("Errors", m.get("subagent_errors", 0))
            cols[3].metric("Fallbacks", m.get("fallback_events", 0))

        for sub_path in sorted(run_dir.glob("subagent-*.json")):
            sa = json.loads(sub_path.read_text(encoding="utf-8"))
            render_agent_row(sa, lang)
            if sa.get("error"):
                st.error(sa["error"])
            else:
                render_agent_output(sa.get("text"))


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    _lang_side = st.session_state.get("ui_lang", "fi")

    # Red disclaimer at the very top — sets expectations before anything else
    render_sidebar_disclaimer(_lang_side)

    # Project description
    render_about_panel(_lang_side)

    # GitHub CTA — link out for more info
    render_github_link(_lang_side)

    # Agent roster
    render_personas_panel(_lang_side)

    # Conversation controls
    _conv_h = "KESKUSTELU" if _lang_side == "fi" else "CONVERSATION"
    _clear_lbl = "🗑️ Tyhjennä keskustelu" if _lang_side == "fi" else "🗑️ Clear chat"
    st.markdown(f'<div class="ia-side-h">{_conv_h}</div>', unsafe_allow_html=True)
    if st.button(_clear_lbl, use_container_width=True):
        st.session_state.state = ConversationState()
        st.session_state.history = []
        st.rerun()

    # Recent runs
    _runs_h = "VIIMEISIMMÄT AJOT" if _lang_side == "fi" else "RECENT RUNS"
    _runs_cap = (
        "Jokainen ajo tallentaa kysymyksen, reitityksen, subagenttien vastaukset "
        "ja aikajanan levylle. Kahdeksan viimeisintä alla."
        if _lang_side == "fi"
        else "Each run saves the query, routing, subagent outputs, and timeline "
        "to disk. The eight most recent are listed below."
    )
    st.markdown(f'<div class="ia-side-h">{_runs_h}</div>', unsafe_allow_html=True)
    st.caption(_runs_cap)
    if RUNS_ROOT.exists():
        recent = sorted(RUNS_ROOT.iterdir(), reverse=True)[:8]
        for d in recent:
            qfile = d / "query.txt"
            label = qfile.read_text(encoding="utf-8").strip() if qfile.exists() else d.name
            label = label[:60] + ("…" if len(label) > 60 else "")
            st.caption(f"`{d.name[:15]}` — {label}")
    else:
        st.caption("Ei vielä ajoja." if _lang_side == "fi" else "No runs yet.")

    # Daily quota progress bar removed from the sidebar — the cap still
    # applies (enforced before each query in `_enforce_daily_cap_or_stop`),
    # we just don't surface the count in the chrome anymore.

    # Logs path note
    runs_dir_display = str(RUNS_ROOT)
    if _lang_side == "fi":
        storage_note = (
            "Lokit ephemeraalisessa container-storagessa — nollautuvat "
            "uudelleenkäynnistyksessä."
            if runs_dir_display.startswith("/tmp")
            else "Lokit tallennetaan pysyvästi."
        )
        st.caption(f"Logit: `{runs_dir_display}`. {storage_note}")
    else:
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
        # Order: LEAD answer first (the synthesis is the headline), then
        # the agent activity log behind a collapsed expander at the
        # bottom for those who want to drill into per-agent thinking.
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
        # CustomStatus replaces st.status — same surface API, but pure
        # HTML/CSS rendering with no Material Symbols icon (which kept
        # racing the icon font load and producing "ieckalmis"-style
        # overlap with the label text).
        status = CustomStatus("Käsittelen kysymystäsi…", expanded=True)
        try:
            answer, run_dir = asyncio.run(
                run_pipeline(prompt, st.session_state.state, status)
            )
            _increment_query_count()
            status.update(label="Valmis", state="complete", expanded=False)
            # Order: LEAD synthesis first (the answer is the headline),
            # then the agent activity log behind a collapsed expander.
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


# ---------------------------------------------------------------------------
# Trading Desk statusbar pinned at the bottom — pulls last run's stats
# ---------------------------------------------------------------------------

_last_meta: dict = {}
if st.session_state.history:
    _last = st.session_state.history[-1]
    if _last.get("run_dir"):
        _meta_path = Path(_last["run_dir"]) / "meta.json"
        if _meta_path.exists():
            try:
                _last_meta = json.loads(_meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
render_statusbar(_last_meta, st.session_state.get("ui_lang", "fi"))
