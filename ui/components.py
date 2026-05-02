"""Trading Desk visual components for the Streamlit app.

Two injection methods are used here, each for a different reason:

* ``inject_theme()`` uses ``st.markdown(..., unsafe_allow_html=True)``
  with a ``<style>`` block. This is the documented Streamlit way to
  inject page-level CSS — the style rules apply to the whole app.
* All other ``render_*`` helpers use ``st.html()``, which renders a
  raw HTML chunk into the page DOM (no markdown processing, no
  escape). They rely on the page-level CSS that ``inject_theme``
  loaded — they don't carry their own ``<style>`` tags.

We don't render the stylesheet via ``st.html`` because that wraps the
payload in a scoped container; ``<style>`` tags inside that wrapper
apply only to siblings within it, not to the surrounding Streamlit
chrome.

Keeping the components in one module means ``ui/app.py`` stays mostly
the same — we just swap in a few calls.

Drop-in usage:

    from ui.components import (
        inject_theme, render_titlebar, render_ticker, render_disclaimer,
        render_idle_hero, render_routing_card, render_metrics_row,
        render_agent_row, render_agent_output, render_statusbar,
    )

The agent persona table mirrors the five-agent system in
``src/inderes_agent/agents/``. Codes match the domain enum names.
"""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

import streamlit as st


# ---------------------------------------------------------------------------
# Persona table — purely cosmetic. Matches src/inderes_agent/agents/* codes.
# ---------------------------------------------------------------------------

PERSONAS: dict[str, dict[str, Any]] = {
    "LEAD": {
        "glyph": "◆", "color": "#FFD24A",
        "role_fi": "Päätoimittaja", "role_en": "Editor-in-chief",
        "desc_fi": "Reitittää kysymyksen, jakaa työn subagenteille ja kirjoittaa lopullisen vastauksen.",
        "desc_en": "Routes the query, dispatches subagents and writes the final synthesis.",
    },
    "QUANT": {
        "glyph": "▲", "color": "#4ADE80",
        "role_fi": "Numerot", "role_en": "Numbers",
        "desc_fi": "Hakee fundamentaalit ja Inderesin estimaatit, ajaa Python-laskut (CAGR, suhdeluvut) sandboxissa.",
        "desc_en": "Pulls fundamentals + Inderes estimates, runs Python math (CAGR, ratios) in a sandbox.",
    },
    "RESEARCH": {
        "glyph": "■", "color": "#60A5FA",
        "role_fi": "Analyytikko", "role_en": "Analyst",
        "desc_fi": "Lukee Inderesin raportit ja transkriptit, etsii laadulliset ajurit ja näkemykset.",
        "desc_en": "Reads Inderes reports + transcripts, surfaces qualitative drivers and theses.",
    },
    "SENTIMENT": {
        "glyph": "●", "color": "#F472B6",
        "role_fi": "Tunnelmat", "role_en": "Vibes",
        "desc_fi": "Kahlaa keskustelupalstan ja sisäpiirikaupat, raportoi yksityissijoittajien tunnelman.",
        "desc_en": "Trawls forum threads + insider trades, reports retail sentiment and signals.",
    },
    "PORTFOLIO": {
        "glyph": "✦", "color": "#A78BFA",
        "role_fi": "Mallisalkku", "role_en": "Model book",
        "desc_fi": "Tarkastaa onko yhtiö Inderesin mallisalkussa ja millä painoilla.",
        "desc_en": "Checks whether the company is in Inderes' model portfolio and at what weight.",
    },
}

# Mock ticker — replace with a real feed if/when available.
TAPE_ITEMS = [
    ("SAMPO", "9.84", "+0.42%", True),
    ("NDA-FI", "11.22", "-0.18%", False),
    ("KCR", "62.10", "+1.20%", True),
    ("NOKIAN-RT", "8.76", "+0.05%", True),
    ("FORTUM", "14.93", "-0.32%", False),
    ("ELISA", "44.20", "+0.10%", True),
    ("NESTE", "12.05", "-2.10%", False),
    ("WRT1V", "20.18", "+0.55%", True),
    ("OUT1V", "5.62", "+0.36%", True),
    ("TYRES", "8.99", "+0.48%", True),
]


# ---------------------------------------------------------------------------
# Theme injection
# ---------------------------------------------------------------------------

def inject_theme() -> None:
    """Inject the Trading Desk CSS once per Streamlit session.

    `st.html()` wraps its payload in a scoped container (`<div data-testid=
    "stHtml">…</div>`) — `<style>` tags inside that wrapper apply only to
    siblings within the wrapper, NOT to the rest of the Streamlit page. So
    rendering a stylesheet via `st.html` produces zero styling on the
    surrounding chrome.

    The documented way to inject page-level CSS in Streamlit is
    `st.markdown(..., unsafe_allow_html=True)` with a `<style>` block —
    those tags ARE preserved and applied to the entire page.

    The font is embedded as a CSS `@import` rather than a separate `<link>`
    tag — `@import` lives inside the `<style>` block and travels with it.
    """
    css_path = Path(__file__).parent / "theme.css"
    if not css_path.exists():
        return
    css = css_path.read_text(encoding="utf-8")
    font_import = (
        "@import url('https://fonts.googleapis.com/css2?"
        "family=JetBrains+Mono:wght@400;500;600;700&display=swap');\n"
        # Streamlit uses Material Symbols Rounded for chevrons + sidebar
        # collapse arrow. We load it explicitly so it's available even if
        # Streamlit's CDN load races with our font-family overrides.
        "@import url('https://fonts.googleapis.com/css2?"
        "family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@"
        "20..48,100..700,0..1,-50..200');\n"
    )
    st.markdown(
        f"<style>{font_import}{css}</style>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Header chrome
# ---------------------------------------------------------------------------

def render_titlebar(lang: str = "fi") -> None:
    online = "ONLINE" if lang == "en" else "VERKOSSA"
    html = (
        '<div class="ia-titlebar">'
        '<span class="ia-brand">INDERES//AGENT</span>'
        '<span class="ia-tag">DESK</span>'
        '<span class="ia-spacer"></span>'
        f'<span class="ia-online">{online}</span>'
        "</div>"
    )
    st.html(html)


def render_ticker() -> None:
    items = []
    for tk, px, chg, up in TAPE_ITEMS * 3:  # repeat for seamless scroll
        cls = "ia-up" if up else "ia-dn"
        arrow = "▲" if up else "▼"
        items.append(
            f'<span class="ia-tape-item">'
            f'<span class="ia-tk">{tk}</span>'
            f'<span class="ia-px">{px}</span>'
            f'<span class="{cls}">{arrow} {chg}</span>'
            f"</span>"
        )
    html = (
        '<div class="ia-tape"><div class="ia-tape-track">'
        + "".join(items)
        + "</div></div>"
    )
    st.html(html)


# ---------------------------------------------------------------------------
# Disclaimer + idle hero
# ---------------------------------------------------------------------------

GITHUB_URL = "https://github.com/5qtb5t9v5k-rgb/inderes-mcp-agent-system"


def render_disclaimer(lang: str = "fi") -> None:
    """Hero panel: short description + agent roster + GitHub CTA.

    Replaces the old big-orange disclaimer. The not-affiliated-with-Inderes
    line lives as fine print at the bottom so the box is more inviting and
    less alarmist.
    """
    if lang == "fi":
        tag = "MULTI-AGENT RESEARCH"
        text = (
            "Viisi agenttia työstää jokaisen kysymyksen yhdessä — yksi reitittäjä ja "
            "neljä erikoistunutta. Kysy Pohjoismaisista osakkeista; saat signaaleja, "
            "et osta- tai myy-suosituksia."
        )
    else:
        tag = "MULTI-AGENT RESEARCH"
        text = (
            "Five agents work each query together — one router and four "
            "specialists. Ask about Nordic equities; get signals, never buy/sell calls."
        )

    agents_html = ""
    for code, p in PERSONAS.items():
        agents_html += (
            f'<span class="ag" style="color:{p["color"]}">'
            f'{p["glyph"]} {code}</span>'
        )

    html = (
        '<div class="ia-hero">'
        f'<div class="ia-hero-tag">{tag}</div>'
        f'<div class="ia-hero-text">{text}</div>'
        f'<div class="ia-hero-agents">{agents_html}</div>'
        '</div>'
    )
    st.html(html)


def render_sidebar_disclaimer(lang: str = "fi") -> None:
    """Red-bordered fine-print disclaimer at the very top of the sidebar."""
    if lang == "fi":
        head = "HUOM"
        body = (
            "Henkilökohtainen tutkimusprojekti, ei Inderes Oyj:n tuottama, "
            "tukema tai hyväksymä. Pinnistää signaaleja Inderesin julkisesta "
            "datasta — ei anna osta- tai myy-suosituksia. Käyttäjä vastaa "
            "omista sijoituspäätöksistään."
        )
    else:
        head = "DISCLAIMER"
        body = (
            "Personal research project, not produced, supported, or endorsed "
            "by Inderes Oyj. Surfaces signals from public Inderes data — does "
            "not issue buy/sell calls. The user is responsible for their own "
            "investment decisions."
        )
    html = (
        f'<div class="ia-side-disclaimer">'
        f'<div class="ia-sd-h">{head}</div>'
        f'<div class="ia-sd-b">{body}</div>'
        '</div>'
    )
    st.html(html)


def render_github_link(lang: str = "fi") -> None:
    """GitHub CTA button — slot anywhere in the sidebar."""
    label = "LISÄTIEDOT GITHUBISSA →" if lang == "fi" else "MORE INFO ON GITHUB →"
    html = (
        f'<a class="ia-side-cta" href="{GITHUB_URL}" target="_blank" rel="noopener">'
        f'{label}</a>'
    )
    st.html(html)


def render_idle_hero(lang: str = "fi") -> None:
    title = "INDERES // AGENT TERMINAL"
    sub = "Kirjoita kysely tai valitse alta" if lang == "fi" else "Type a query or pick one below"
    html = (
        '<div class="ia-idle">'
        '<div class="ia-glyphs">◆ ▲ ■ ● ✦</div>'
        f'<div class="ia-title">{title}</div>'
        f'<div class="ia-sub">{sub}</div>'
        "</div>"
    )
    st.html(html)


# ---------------------------------------------------------------------------
# Routing card
# ---------------------------------------------------------------------------

def render_routing_card(routing: dict, lang: str = "fi") -> None:
    """``routing`` is the parsed contents of ``run_dir/routing.json``."""
    domains = routing.get("domains", [])
    companies = routing.get("companies") or []
    is_cmp = routing.get("is_comparison")
    reason = routing.get("reasoning", "")

    pills = ""
    for d in domains:
        code = d.upper()
        p = PERSONAS.get(code, {"glyph": "•", "color": "#888"})
        pills += (
            f'<span class="ia-pill" '
            f'style="color:{p["color"]};border-color:{p["color"]}">'
            f'{p["glyph"]} {code}</span>'
        )

    co_label = "COMPANY" if lang == "en" else "YHTIÖ"
    dom_label = "DOMAINS" if lang == "en" else "DOMAINIT"
    cmp_label = "COMPARISON" if lang == "en" else "VERTAILU"
    yes = "yes" if lang == "en" else "kyllä"
    no  = "no"  if lang == "en" else "ei"

    html = (
        '<div class="ia-routing">'
        f'<div><div class="ia-rl">{co_label}</div>'
        f'<div class="ia-rv amber">{", ".join(companies) or "—"}</div></div>'
        f'<div><div class="ia-rl">{dom_label}</div>'
        f'<div class="ia-rv">{pills}</div></div>'
        f'<div><div class="ia-rl">{cmp_label}</div>'
        f'<div class="ia-rv">{yes if is_cmp else no}</div></div>'
        f'<div><div class="ia-rl">REASON</div>'
        f'<div class="ia-rv" style="font-size:11px;color:var(--ia-dim)">{_esc(reason)}</div></div>'
        "</div>"
    )
    st.html(html)


# ---------------------------------------------------------------------------
# Metric row — best-effort: pulls metrics from the QUANT subagent's
# JSON if it includes a ``metrics`` block. Otherwise renders nothing.
# ---------------------------------------------------------------------------

def render_metrics_row(run_dir: Path, lang: str = "fi") -> None:
    quant_files = list(run_dir.glob("subagent-*-quant.json"))
    if not quant_files:
        return
    try:
        sa = json.loads(quant_files[0].read_text(encoding="utf-8"))
    except Exception:
        return
    m = sa.get("metrics") or {}
    if not m:
        return  # don't render an empty row

    pe_label  = "P/E"
    tg_label  = "TARGET" if lang == "en" else "TAVOITE"
    rec_label = "REC"    if lang == "en" else "SUOSITUS"
    div_label = "DIV YIELD" if lang == "en" else "OSINKOTUOTTO"

    def _fmt(v):
        if v is None: return "—"
        if isinstance(v, (int, float)):
            s = f"{v:.2f}" if v % 1 else f"{v:.0f}"
            return s.replace(".", ",") if lang == "fi" else s
        return str(v)

    pe = m.get("pe_2025") or m.get("pe")
    pe_e = m.get("pe_2026e")
    tgt = m.get("target")
    rec = m.get("rec") or "—"
    dy  = m.get("div_yield")

    html = (
        '<div class="ia-metrics">'
        f'<div class="ia-metric"><div class="ia-ml">{pe_label} 2025</div>'
        f'<div class="ia-mv green">{_fmt(pe)}</div>'
        f'<div class="ia-ms">2026e {_fmt(pe_e)}</div></div>'
        f'<div class="ia-metric"><div class="ia-ml">{tg_label}</div>'
        f'<div class="ia-mv">€{_fmt(tgt)}</div>'
        f'<div class="ia-ms">Inderes</div></div>'
        f'<div class="ia-metric"><div class="ia-ml">{rec_label}</div>'
        f'<div class="ia-mv blue small">{rec}</div>'
        f'<div class="ia-ms">{rec}</div></div>'
        f'<div class="ia-metric"><div class="ia-ml">{div_label}</div>'
        f'<div class="ia-mv">{_fmt(dy)}%</div>'
        f'<div class="ia-ms">2026e</div></div>'
        "</div>"
    )
    st.html(html)


# ---------------------------------------------------------------------------
# Agent trace rows (one per subagent) — replaces the plain markdown header
# in the existing render_trace_expander.
# ---------------------------------------------------------------------------

def render_agent_row(sa: dict, lang: str = "fi") -> None:
    domain = (sa.get("domain") or "?").upper()
    company = sa.get("company")
    model = sa.get("model_used", "?")
    err = sa.get("error")
    p = PERSONAS.get(domain, {"glyph": "•", "color": "#888", "role_fi": "—", "role_en": "—"})
    role = p["role_en"] if lang == "en" else p["role_fi"]
    status = ("ERROR" if err else ("OK" if lang == "en" else "VALMIS"))
    cls = "err" if err else "done"

    name = domain + (f" / {company}" if company else "")

    html = (
        f'<div class="ia-agent-row {cls}">'
        f'<div class="ia-glyph" style="color:{p["color"]}">{p["glyph"]}</div>'
        f'<div><div class="ia-name" style="color:{p["color"]}">{_esc(name)}</div>'
        f'<div class="ia-role">{_esc(role)}</div></div>'
        f'<div class="ia-model">{_esc(model)}</div>'
        f'<div class="ia-status">{status}</div>'
        f'<div></div>'
        "</div>"
    )
    st.html(html)


def _wrap_python_output(text: str) -> str:
    """Wrap Python sandbox stdout (the line right after a ```python block) as a
    fenced ``output`` block so it renders distinctly from both code and prose.

    Detection: a closing ``⁠```⁠`` followed by one or more blank lines and
    a paragraph whose first line starts with a Python repr marker (``{``, ``[``,
    ``(``, a digit, or a quote). The paragraph runs until the next blank line.
    """
    import re

    pat = re.compile(
        r"(^```\s*$\n)"          # 1: closing fence on its own line
        r"\n+"                   #    one or more blank lines
        r"([\{\[\(\d\'\"][^\n]*" # 2: first repr-like line
        r"(?:\n[^\n]+)*?)"       #    optional further lines (no blanks)
        r"(?=\n\s*\n|\Z)",       #    stop at blank line or end-of-string
        re.MULTILINE,
    )

    def _replace(m: "re.Match[str]") -> str:
        return f"{m.group(1)}\n```output\n{m.group(2)}\n```"

    return pat.sub(_replace, text)


def render_agent_output(text: str | None) -> None:
    """Render the subagent's text in a styled box.

    Each `st.html()` call is its own DOM block, so we can't open a div in
    one call and close it in another. Instead we convert the markdown text
    to HTML via `markdown-it-py` (already an indirect dependency through
    Streamlit's own rich-rendering stack) and wrap the result in our
    `ia-agent-output` div in a single st.html call.

    Before rendering we pre-process the text so a stdout-looking paragraph
    that immediately follows a ```python``` block gets wrapped in its own
    ```output``` fenced block — the CSS then gives it a green left border so
    it's visually distinct from the (blue-bordered) source-code block above.
    """
    if not text:
        text = "_(empty response)_"
    text = _wrap_python_output(text)
    try:
        from markdown_it import MarkdownIt

        # `commonmark` preset doesn't include tables — explicitly enable them
        # so subagent markdown like `| col | col |` renders as <table>, not
        # raw pipe text. Also enable strikethrough since QUANT/LEAD outputs
        # occasionally use it.
        md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
        html_content = md.render(text)
    except Exception:
        # If markdown_it isn't available for some reason, fall back to a
        # `<pre>` block — formatting is lost but content still readable.
        from html import escape as _html_escape

        html_content = f"<pre>{_html_escape(text)}</pre>"
    st.html(f'<div class="ia-agent-output">{html_content}</div>')


def render_full_narrative(run_dir: Path, lang: str = "fi") -> None:
    """Render the full narrative.md (routing + tool-call timeline + subagent
    answers) in a scrollable container at the bottom of the trace.

    Same data as the per-section breakdown above, but in one easy-to-scan
    document — useful for getting a feel for how the agent progressed (which
    tools it called, in what order, how long each took).
    """
    narrative_path = run_dir / "narrative.md"
    if not narrative_path.exists():
        return
    text = narrative_path.read_text(encoding="utf-8")
    title = "TÄYDELLINEN AJOLOKI" if lang == "fi" else "FULL RUN LOG"
    sub = (
        "Reititys, työkalukutsujen aikajana ja subagenttien raakat vastaukset."
        if lang == "fi"
        else "Routing, tool-call timeline, and raw subagent answers."
    )
    try:
        from markdown_it import MarkdownIt

        text = _wrap_python_output(text)
        md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
        html_content = md.render(text)
    except Exception:
        from html import escape as _html_escape

        html_content = f"<pre>{_html_escape(text)}</pre>"
    st.html(
        f'<div class="ia-narrative-h">{title}</div>'
        f'<div class="ia-narrative-sub">{sub}</div>'
        f'<div class="ia-narrative">{html_content}</div>'
    )





# ---------------------------------------------------------------------------
# Statusbar — pinned at the bottom of the main column
# ---------------------------------------------------------------------------

def render_statusbar(meta: dict | None = None, lang: str = "fi") -> None:
    meta = meta or {}
    errors = meta.get("subagent_errors", 0)
    fbacks = meta.get("fallback_events", 0)
    not_advice = (
        "Not investment advice. Surfaces signals, never issues buy/sell calls."
        if lang == "en"
        else "Ei sijoitusneuvonta. Pinnistää signaaleja, ei anna osta/myy-suosituksia."
    )
    err_lbl = "errors" if lang == "en" else "virheet"
    fb_lbl  = "fallbacks" if lang == "en" else "fallbackit"

    html = (
        '<div class="ia-statusbar">'
        '<span class="ia-hot">● mcp.inderes.com</span>'
        '<span>│ gemini-3.1-flash-lite</span>'
        f'<span>│ {err_lbl}: <span class="ia-warn">{errors}</span></span>'
        f'<span>│ {fb_lbl}: {fbacks}</span>'
        '<span class="ia-spacer"></span>'
        f'<span>{not_advice}</span>'
        "</div>"
    )
    st.html(html)


# ---------------------------------------------------------------------------
# Sidebar panels — agent personas + project description
# ---------------------------------------------------------------------------

def render_personas_panel(lang: str = "fi") -> None:
    """Render the 5-agent roster in the sidebar with glyphs, colors, descriptions."""
    title = "AGENTIT" if lang == "fi" else "AGENTS"
    rows = []
    for code, p in PERSONAS.items():
        role = p["role_en"] if lang == "en" else p["role_fi"]
        desc = p["desc_en"] if lang == "en" else p["desc_fi"]
        rows.append(
            f'<div class="ia-persona">'
            f'<div class="ia-pg" style="color:{p["color"]}">{p["glyph"]}</div>'
            f'<div>'
            f'<div class="ia-pn" style="color:{p["color"]}">{code}</div>'
            f'<div class="ia-pr">{_esc(role)}</div>'
            f'<div class="ia-pd">{_esc(desc)}</div>'
            f'</div></div>'
        )
    html = f'<div class="ia-side-h">{title}</div>' + "".join(rows)
    st.html(html)


def render_about_panel(lang: str = "fi") -> None:
    """Render a project description block in the sidebar."""
    title = "PROJEKTI" if lang == "fi" else "PROJECT"
    if lang == "fi":
        intro = (
            "Henkilökohtainen tutkimusprojekti. Päätoimittaja-agentti reitittää "
            "Pohjoismaisia osakkeita koskevan kysymyksen 1–4 erikoistuneelle "
            "subagentille, joista jokainen ottaa yhteyttä Inderes MCP -palveluun "
            "omilla työkaluillaan. Synteesi kootaan aina lopuksi yhdeksi vastaukseksi — "
            "ei koskaan osta- tai myy-suosituksia."
        )
        kv = [
            ("STACK", "MAF + Gemini Flash"),
            ("DATA", "Inderes MCP", "amber"),
            ("OUTPUT", "Signaalit, ei suosituksia"),
            ("STATUS", "● Live", "green"),
        ]
    else:
        intro = (
            "Personal research project. A lead orchestrator routes a Nordic-equity "
            "question to 1–4 specialized subagents, each calling the Inderes MCP "
            "with their own toolset. Outputs are always synthesized into a single "
            "answer — never buy/sell calls."
        )
        kv = [
            ("STACK", "MAF + Gemini Flash"),
            ("DATA", "Inderes MCP", "amber"),
            ("OUTPUT", "Signals, not advice"),
            ("STATUS", "● Live", "green"),
        ]
    rows = ""
    for item in kv:
        k, v = item[0], item[1]
        cls = item[2] if len(item) > 2 else ""
        rows += f'<div class="ia-kv"><span class="k">{k}</span><span class="v {cls}">{_esc(v)}</span></div>'
    html = (
        f'<div class="ia-side-h">{title}</div>'
        f'<div class="ia-about"><p>{intro}</p>{rows}</div>'
    )
    st.html(html)


def _esc(s: Any) -> str:
    """Minimal HTML escape so user-supplied strings can't break our markup."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
