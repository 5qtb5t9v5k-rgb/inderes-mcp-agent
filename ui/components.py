"""Trading Desk visual components for the Streamlit app.

Each helper renders a small HTML string via ``st.html()``. We use
``st.html`` (not ``st.markdown(unsafe_allow_html=True)``) because
recent Streamlit releases strip ``<style>`` and ``<link>`` tags from
markdown even with the unsafe flag. ``st.html`` is purpose-built for
raw HTML/CSS injection and bypasses the sanitizer.

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
    "LEAD":      {"glyph": "◆", "color": "#FFD24A", "role_fi": "Päätoimittaja", "role_en": "Editor-in-chief"},
    "QUANT":     {"glyph": "▲", "color": "#4ADE80", "role_fi": "Numerot",        "role_en": "Numbers"},
    "RESEARCH":  {"glyph": "■", "color": "#60A5FA", "role_fi": "Analyytikko",    "role_en": "Analyst"},
    "SENTIMENT": {"glyph": "●", "color": "#F472B6", "role_fi": "Tunnelmat",      "role_en": "Vibes"},
    "PORTFOLIO": {"glyph": "✦", "color": "#A78BFA", "role_fi": "Mallisalkku",    "role_en": "Model book"},
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

    Streamlit's `st.markdown(..., unsafe_allow_html=True)` strips `<style>`
    and `<link>` tags as a security measure even with the unsafe flag. The
    fix is to use `st.html()`, which is purpose-built for raw HTML/CSS
    injection and does not sanitize style/link tags.

    We also embed the font as a CSS `@import` rather than a separate `<link>`
    tag — `@import` lives inside the `<style>` block and travels with it.
    """
    css_path = Path(__file__).parent / "theme.css"
    if not css_path.exists():
        return
    css = css_path.read_text(encoding="utf-8")
    font_import = (
        "@import url('https://fonts.googleapis.com/css2?"
        "family=JetBrains+Mono:wght@400;500;600;700&display=swap');\n"
    )
    st.html(f"<style>{font_import}{css}</style>")


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

def render_disclaimer(lang: str = "fi") -> None:
    if lang == "fi":
        head = "HUOM"
        body = (
            "Henkilökohtainen tutkimusprojekti, ei Inderes Oyj:n tuottama tai hyväksymä. "
            "Pinnistää signaaleja Inderes-datasta — ei anna osta- tai myy-suosituksia. "
            "Käyttäjä päättää itse."
        )
    else:
        head = "NOTE"
        body = (
            "Personal research project, not affiliated with or endorsed by Inderes Oyj. "
            "Surfaces signals from Inderes data — does not issue buy/sell calls. "
            "The user decides."
        )
    html = f'<div class="ia-disclaimer"><div class="ia-dh">{head}</div>{body}</div>'
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


def render_agent_output(text: str | None) -> None:
    """Render the subagent's text in a styled box.

    Each `st.html()` call is its own DOM block, so we can't open a div in
    one call and close it in another. Instead we convert the markdown text
    to HTML via `markdown-it-py` (already an indirect dependency through
    Streamlit's own rich-rendering stack) and wrap the result in our
    `ia-agent-output` div in a single st.html call.
    """
    if not text:
        text = "_(empty response)_"
    try:
        from markdown_it import MarkdownIt

        html_content = MarkdownIt().render(text)
    except Exception:
        # If markdown_it isn't available for some reason, fall back to a
        # `<pre>` block — formatting is lost but content still readable.
        from html import escape as _html_escape

        html_content = f"<pre>{_html_escape(text)}</pre>"
    st.html(f'<div class="ia-agent-output">{html_content}</div>')


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
