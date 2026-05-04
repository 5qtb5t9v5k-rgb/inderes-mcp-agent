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
import logging
import os
import time
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

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

    Uses direct assignment (not setdefault) so that if the env var was
    pre-seeded with an empty/stale value somewhere upstream, the secret
    still wins. Also prints a one-liner at startup listing which secret
    keys got bridged — useful when debugging "why isn't my new secret
    being read" issues in the cloud logs.
    """
    try:
        items = list(st.secrets.items())
    except Exception as exc:
        print(f"[secrets] bridge skipped: {exc}", flush=True)
        return  # no secrets.toml configured — fine in local dev
    bridged: list[str] = []
    for key, value in items:
        # Nested TOML tables (like [INDERES_OAUTH_TOKENS]) get serialised to JSON.
        if isinstance(value, dict):
            os.environ[f"{key}_JSON"] = json.dumps(value)
            bridged.append(f"{key}_JSON")
        else:
            os.environ[key] = str(value)
            bridged.append(key)
    # Mask values that look like secrets in the print, but show keys so we
    # can verify the right ones got bridged.
    print(f"[secrets] bridged {len(bridged)} keys: {', '.join(sorted(bridged))}", flush=True)


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
    render_lead_answer,
    render_followup_chips,
    render_recommendation_badge,
    CustomStatus,
    PERSONAS,
    DOMAIN_VERBS_FI,
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
# Trading Desk chat avatars
# ---------------------------------------------------------------------------
# Streamlit's chat_message(avatar=...) only accepts URLs, image paths, or
# *real* emoji codepoints — Unicode dingbats like ❯ (U+276F) or ◆ (U+25C6)
# are NOT detected as emoji and Streamlit treats them as file paths,
# crashing with FileNotFoundError. So we use proper emoji that match the
# Trading Desk theme as closely as the emoji vocabulary allows:
#   💼 — briefcase, the "research desk" side
#   🔶 — orange diamond, matches LEAD's amber ◆ glyph

USER_AVATAR = "👤"
ASSISTANT_AVATAR = "🔶"


# ---------------------------------------------------------------------------
# Public-safe error rendering
# ---------------------------------------------------------------------------

DEMO_VIDEO_URL = "https://www.youtube.com/watch?v=2lw6lC2ho_c"

# Help-request counter is persisted as a separate file in the same gist
# that already mirrors tokens.json, so we don't need a second secret.
HELP_REQUESTS_GIST_FILE = "help_requests.json"


def _read_help_request_state() -> dict:
    """Read the recovery-requests counter from the gist mirror.

    Best-effort: if the gist isn't configured, the file doesn't exist,
    or the GitHub API hiccups, returns zeros so the UI still renders.
    """
    gist_id = os.environ.get("INDERES_TOKENS_GIST_ID", "").strip()
    gh_token = os.environ.get("INDERES_TOKENS_GH_TOKEN", "").strip()
    if not (gist_id and gh_token):
        return {"count": 0, "last_at": None}

    try:
        import httpx
        r = httpx.get(
            f"https://api.github.com/gists/{gist_id}",
            headers={
                "Authorization": f"token {gh_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10.0,
        )
        if r.status_code != 200:
            return {"count": 0, "last_at": None}
        files = r.json().get("files", {}) or {}
        content = (files.get(HELP_REQUESTS_GIST_FILE) or {}).get("content")
        if not content:
            return {"count": 0, "last_at": None}
        data = json.loads(content)
        return {
            "count": int(data.get("count", 0)),
            "last_at": data.get("last_request_at"),
        }
    except Exception as exc:
        log.warning("help_request_state_read_failed %s", exc)
        return {"count": 0, "last_at": None}


def _record_help_request() -> tuple[bool, dict]:
    """Increment the counter and push back to the gist. Returns (ok, state).

    On any failure returns (False, current_state_or_zeros) so the caller
    can show a soft "yritä uudelleen" hint without losing the prior
    count from the UI.
    """
    gist_id = os.environ.get("INDERES_TOKENS_GIST_ID", "").strip()
    gh_token = os.environ.get("INDERES_TOKENS_GH_TOKEN", "").strip()
    if not (gist_id and gh_token):
        return False, {"count": 0, "last_at": None}

    current = _read_help_request_state()
    from datetime import datetime, timezone
    new_payload = {
        "count": current["count"] + 1,
        "last_request_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        import httpx
        r = httpx.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers={
                "Authorization": f"token {gh_token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
            json={
                "files": {
                    HELP_REQUESTS_GIST_FILE: {
                        "content": json.dumps(new_payload, indent=2),
                    },
                },
            },
            timeout=10.0,
        )
        if r.status_code in (200, 201):
            return True, {
                "count": new_payload["count"],
                "last_at": new_payload["last_request_at"],
            }
        log.warning(
            "help_request_state_write_failed status=%d body=%s",
            r.status_code,
            r.text[:200],
        )
    except Exception as exc:
        log.warning("help_request_state_write_exception %s", exc)
    return False, current


def _format_relative_fi(iso_ts: str | None) -> str:
    """Format an ISO timestamp as a Finnish-language relative-time string."""
    if not iso_ts:
        return "ei vielä yhtään"
    try:
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        secs = (datetime.now(timezone.utc) - ts).total_seconds()
        if secs < 60:
            return "juuri äsken"
        if secs < 3600:
            return f"{int(secs // 60)} min sitten"
        if secs < 86400:
            return f"{int(secs // 3600)} h sitten"
        return f"{int(secs // 86400)} pv sitten"
    except Exception:
        return ""


def _get_cached_help_state(max_age_s: int = 30) -> dict:
    """Get the help-request state, caching for max_age_s seconds in
    session_state. Avoids hammering the gist API on every Streamlit
    rerun (the page rerenders every time the user interacts with
    anything, including hovering certain components).
    """
    cache = st.session_state.get("_help_state_cache")
    cache_ts = st.session_state.get("_help_state_cache_ts", 0.0)
    if cache is not None and (time.time() - cache_ts) < max_age_s:
        return cache
    fresh = _read_help_request_state()
    st.session_state["_help_state_cache"] = fresh
    st.session_state["_help_state_cache_ts"] = time.time()
    return fresh


def _render_auth_expired() -> None:
    """Generic message for expired Inderes auth — safe to show to anyone.

    The Streamlit app sits behind a password gate, but anything we render
    is potentially visible to whoever knows the password (or anyone who
    eventually gets past it). So this message doesn't expose internal
    paths, scripts, repo names, or implementation details.

    Adds three affordances on top of the bare error:
    - An embedded demo video so a first-time visitor can still see what
      the tool does even when it can't run live.
    - A "Pyydä apua" button that increments a counter persisted in the
      gist mirror — operator sees the counter rise and knows people are
      waiting.
    - The counter itself rendered as a metric so visitors get social
      proof ("you're not the first") and the operator gets a glanceable
      activity gauge.

    Anti-spam: per-session_state flag prevents re-clicking within the
    same browser session. Cross-session abuse is mitigated by the
    password gate; this is a hobby-project tradeoff.
    """
    st.error(
        "🔴  **Inderes-yhteys vanhentunut.**\n\n"
        "Ole hyvä ja autentikoi yhteys uudelleen, ja yritä sitten "
        "kysyä uudestaan."
    )

    st.markdown("**Ensimmäistä kertaa täällä?** Katso lyhyt demo:")
    try:
        st.video(DEMO_VIDEO_URL)
    except Exception as exc:
        log.warning("demo_video_embed_failed %s", exc)
        st.markdown(f"[Katso demo YouTubessa]({DEMO_VIDEO_URL})")

    st.markdown("---")

    state = _get_cached_help_state()

    left, right = st.columns([3, 2], vertical_alignment="center")

    with left:
        if st.session_state.get("_help_request_sent"):
            st.success(
                "✓ Kiitos pyynnöstä — laitetaan agentti taas pystyyn "
                "mahdollisimman pian.\n\n"
                f"Olit pyyntö **#{state['count']}** agentin elinaikana."
            )
        else:
            st.markdown(
                "**Haluatko että agentti laitetaan takaisin pystyyn?**\n\n"
                "Klikkaa nappia — pyyntösi näkyy laskurissa, ja korjaan "
                "tilanteen kun huomaan."
            )
            if st.button(
                "📧 Pyydä apua",
                type="primary",
                use_container_width=True,
            ):
                ok, new_state = _record_help_request()
                if ok:
                    st.session_state["_help_request_sent"] = True
                    st.session_state["_help_state_cache"] = new_state
                    st.session_state["_help_state_cache_ts"] = time.time()
                    st.rerun()
                else:
                    st.warning(
                        "Pyynnön tallennus ei mennyt läpi. "
                        "Yritä hetken päästä uudelleen."
                    )

    with right:
        st.metric(
            label="Apupyyntöjä yhteensä",
            value=str(state["count"]),
            help=(
                "Kuinka monta kertaa joku on klikannut 'Pyydä apua' "
                "-nappia agentin elinaikana."
            ),
        )
        if state["last_at"]:
            st.caption(f"viimeisin: {_format_relative_fi(state['last_at'])}")
        else:
            st.caption("ei vielä pyyntöjä")


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
    except HeadlessAuthError:
        # The full exception goes to server logs; the UI gets a generic
        # public-safe message that doesn't expose paths/commands/scripts.
        log.exception("oauth_headless_at_bootstrap")
        _render_auth_expired()
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
    avatar = ASSISTANT_AVATAR if msg["role"] == "assistant" else USER_AVATAR
    with st.chat_message(msg["role"], avatar=avatar):
        # LEAD answers go through render_lead_answer so the
        # **💭 Perustelut:** callout gets its amber styling. User messages
        # are plain markdown.
        if msg["role"] == "assistant":
            run_dir = Path(msg["run_dir"]) if msg.get("run_dir") else None
            # Inderes recommendation badge — only renders if QUANT surfaced
            # a recommendation in this run; silently no-ops otherwise.
            if run_dir is not None:
                render_recommendation_badge(run_dir)
            render_lead_answer(msg["content"])
            # Followup question chips — extracted from the LEAD synthesis
            # itself; clicking one writes to st.session_state.pending_query
            # and triggers a rerun that submits it as a new query.
            render_followup_chips(
                msg["content"],
                run_dir_name=(run_dir.name if run_dir else f"hist{id(msg)}"),
            )
            if run_dir is not None:
                render_trace_expander(run_dir)
        else:
            st.markdown(msg["content"])


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

async def run_pipeline(query: str, state: ConversationState, status) -> tuple[str, Path]:
    """Execute the full router → workflow → synthesis pipeline with status updates."""
    run_dir = new_run_dir()
    handler = attach_console_log_handler(run_dir)
    t0 = time.time()

    def _persona_chip(domain_value: str) -> str:
        """Render `▲ QUANT` (or whichever) with its persona color."""
        code = domain_value.upper()
        p = PERSONAS.get(code, {"glyph": "•", "color": "#888"})
        return (
            f'<span style="color:{p["color"]}; font-weight:600">'
            f'{p["glyph"]} {code}</span>'
        )

    try:
        # Phase 1: routing
        status.write("🧭 Reititän kysymyksen oikeille agenteille…")
        ctx = ", ".join(state.last_companies) if state.last_companies else ""
        ctx_hint = f"previous turn discussed: {ctx}" if ctx else ""
        classification = await classify_query(query, conversation_context=ctx_hint)
        if not classification.companies and state.last_companies:
            classification.companies = state.last_companies

        domains_html = " + ".join(_persona_chip(d.value) for d in classification.domains)
        companies_html = (
            f'<span style="color:var(--ia-amber)">{", ".join(classification.companies)}</span>'
            if classification.companies else "—"
        )
        status.write(
            f"✓ Päädyin: {domains_html} — kohde: {companies_html}",
            html=True,
        )

        # Phase 2: dispatch — write a "käynnistyy → verb" line per agent BEFORE
        # awaiting so the user sees who's working during the (often 10–30s)
        # parallel run.
        status.write("⚙️  Subagentit käynnistyvät rinnakkain…")
        for d in classification.domains:
            verb = DOMAIN_VERBS_FI.get(d.value, "tutkii")
            status.write(
                f"  {_persona_chip(d.value)} käynnistyy → {verb}",
                html=True,
            )

        # Inject conversation context into the SUBAGENT prompt as well, not
        # just the router. Without this, a followup like "Onko jompikumpi
        # näistä yhtiöistä mallisalkussa?" lands on the subagent without
        # any indication of what "näistä" refers to, and the subagent has
        # to ask for clarification. The router already knows the previous
        # turn's companies, but it routes — it doesn't propagate context to
        # subagent prompts. So we do that here.
        augmented_query = query
        if state.last_companies and not classification.is_comparison:
            ctx_line = (
                f"[CONTEXT: edellinen kysely käsitteli yhtiöitä: "
                f"{', '.join(state.last_companies)}. "
                f"Jos tämä kysymys viittaa 'näihin yhtiöihin', 'jompaan kumpaan' "
                f"tai pronominein ilman selvää viittausta, tulkitse niiden "
                f"viittaavan yllä mainittuihin yhtiöihin.]\n\n"
            )
            augmented_query = ctx_line + query

        workflow_result = await run_workflow(
            augmented_query, classification, run_dir=run_dir,
        )

        # Phase 3: per-agent results
        for sr in workflow_result.subagent_results:
            chip = _persona_chip(sr.domain.value)
            company = f" · {sr.company}" if sr.company else ""
            if sr.error:
                status.write(
                    f"  {chip}{company} ❌ virhe: {sr.error[:60]}",
                    html=True,
                )
            else:
                status.write(
                    f"  {chip}{company} ✓ valmis "
                    f'<span style="color:var(--ia-faint); font-size:10px">'
                    f'({sr.model_used})</span>',
                    html=True,
                )

        # Phase 4: synthesis
        status.write(
            f"{_persona_chip('lead')} yhdistää tulokset synteesiksi…",
            html=True,
        )
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

# A followup-chip click sets st.session_state.pending_query and reruns. If
# the user didn't type anything THIS rerun, pick up the queued query and
# treat it as if they typed it.
if not prompt and st.session_state.get("pending_query"):
    prompt = st.session_state.pop("pending_query")

if prompt:
    # Enforce the daily cap BEFORE we charge a query against the cap. The
    # increment happens after a successful run.
    _enforce_daily_cap_or_stop()

    st.session_state.history.append({"role": "user", "content": prompt, "run_dir": None})
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
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
            # Order: Inderes recommendation badge (if QUANT surfaced one),
            # then LEAD synthesis, then followup-chip buttons, then the
            # agent activity log behind a collapsed expander.
            render_recommendation_badge(run_dir)
            render_lead_answer(answer)
            render_followup_chips(answer, run_dir_name=run_dir.name)
            render_trace_expander(run_dir)
            st.session_state.history.append(
                {"role": "assistant", "content": answer, "run_dir": str(run_dir)}
            )
        except QuotaExhaustedError as exc:
            status.update(label="Quota exhausted", state="error", expanded=True)
            st.error(str(exc))
        except HeadlessAuthError:
            # Auth expired mid-query (e.g. tokens died between cold start
            # and now). Don't leak the raw error which references local
            # paths and scripts — show the same generic card as cold-start.
            status.update(label="Inderes-yhteys vanhentunut", state="error", expanded=True)
            log.exception("oauth_headless_during_query")
            _render_auth_expired()
        except Exception as exc:
            # If anything looks like an auth/refresh problem, mask it the
            # same way. Otherwise show the (already-non-sensitive)
            # exception class — type names are safe.
            msg = str(exc).lower()
            if any(t in msg for t in ("session not active", "token is not active",
                                       "invalid_grant", "headless")):
                status.update(label="Inderes-yhteys vanhentunut", state="error", expanded=True)
                log.exception("oauth_likely_failure_in_query")
                _render_auth_expired()
            else:
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
