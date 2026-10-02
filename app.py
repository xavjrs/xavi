"""ISA Terminal - web dashboard (Streamlit).

Run with:  py -3.12 -m streamlit run app.py

Information only: shows your holdings, how they are spread, and news about them.
It does not recommend buying, selling or holding anything.
"""

import html

import altair as alt
import pandas as pd
import streamlit as st

import auth
import isa_core as core
import json

st.set_page_config(page_title="XAVI", page_icon="◼", layout="wide",
                   initial_sidebar_state="expanded")

# Every colour is a (light, dark) pair. The browser picks one with CSS light-dark(), following
# Streamlit's own theme (three-dot menu -> Settings), so a switch updates instantly.
TOKENS = {
    "accent": ("#15803d", "#34d399"), "on_accent": ("#ffffff", "#04150c"),
    "green": ("#16a34a", "#4ade80"), "red": ("#dc2626", "#f87171"),
    "blue": ("#0f766e", "#5eead4"), "grey": ("#5a7767", "#8aa597"),
    "bg": ("#f3faf5", "#07130d"), "panel": ("#ffffff", "#0e2018"),
    "sidebar": ("#e7f4ec", "#0a1a12"), "border": ("#c9e2d3", "#1c3a2b"),
    "text": ("#0f2a1c", "#e6f2ea"), "text_strong": ("#04140b", "#ffffff"),
    "text_soft": ("#2c4a3a", "#c9ddd0"), "hover": ("#dcefe3", "#12281d"),
    "selected": ("#cfe8d9", "#143323"), "track": ("#d3e6da", "#173226"),
    "warn_bg": ("#fff7dd", "#1d1a0c"), "warn_border": ("#ecd98a", "#3d3510"),
    "warn_text": ("#6b5200", "#e5c76b"),
}
CSS_VARS = " ".join(f"--{name}: light-dark({light}, {dark});"
                    for name, (light, dark) in TOKENS.items())
ACCENT, ON_ACCENT, GREEN, RED, BLUE, GREY = (f"var(--{n})" for n in (
    "accent", "on_accent", "green", "red", "blue", "grey"))
BG, PANEL, SIDEBAR, BORDER, TEXT, TEXT_STRONG, TEXT_SOFT = (f"var(--{n})" for n in (
    "bg", "panel", "sidebar", "border", "text", "text_strong", "text_soft"))
HOVER, SELECTED, TRACK, WARN_BG, WARN_BORDER, WARN_TEXT = (f"var(--{n})" for n in (
    "hover", "selected", "track", "warn_bg", "warn_border", "warn_text"))

# Charts are drawn by Vega and need plain hex colours. These mid-tones read well on both the
# light and the dark background (greys are translucent).
CH_ACCENT, CH_GREEN, CH_RED, CH_GREY = "#16a34a", "#22c55e", "#ef4444", "#7d9488"
CH_GRID, CH_RULE, CH_VOLUME = "rgba(128,128,128,0.22)", "rgba(128,128,128,0.6)", \
    "rgba(110,150,130,0.55)"
CH_PALETTE = ["#16a34a", "#0d9488", "#65a30d", "#059669", "#2dd4bf", "#84cc16", "#047857",
              "#4ade80"]

DISCLAIMER = ("Information only - not financial advice. Prices come from Yahoo Finance "
              "(delayed, may contain errors). Nothing here is a recommendation to buy, sell "
              "or hold anything.")

st.markdown(f"""
<style>
.stApp {{ {CSS_VARS}
         font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
         font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased;
         background:{BG}; color:{TEXT}; }}
.block-container {{ padding: 4rem 2.5rem 3rem 2.5rem; max-width: 1440px; }}
header[data-testid="stHeader"] {{ background:transparent; }}

/* brand */
.topbar {{ border-bottom:1px solid {BORDER}; }}
.meta {{ color:{GREY}; font-size:0.82rem; line-height:1.6; }}
.page-title {{ font-size:1.5rem; font-weight:600; letter-spacing:-0.01em; line-height:1.2;
              color:{TEXT_STRONG}; }}

/* metric tiles: all identical in size */
.tile {{ border:1px solid {BORDER}; background:{PANEL}; border-radius:8px; padding:15px 18px;
        height:112px; box-sizing:border-box; overflow:hidden; }}
.tile .label {{ color:{GREY}; font-size:clamp(0.6rem, 0.85vw, 0.7rem); letter-spacing:0.09em;
               font-weight:500; text-transform:uppercase; white-space:nowrap; overflow:hidden;
               text-overflow:ellipsis; }}
.tile .value {{ font-size:clamp(1rem, 1.7vw, 1.5rem); font-weight:600; letter-spacing:-0.01em;
               margin:6px 0 3px 0; white-space:nowrap; color:{TEXT_STRONG}; }}
.tile .sub {{ color:{GREY}; font-size:0.78rem; white-space:nowrap; overflow:hidden;
             text-overflow:ellipsis; }}
.pos {{ color:{GREEN} !important; }} .neg {{ color:{RED} !important; }}

/* section headings + notes */
.section {{ color:{ACCENT}; font-weight:600; letter-spacing:0.1em; margin:36px 0 4px 0;
           text-transform:uppercase; font-size:0.78rem; }}
.note {{ color:{GREY}; font-size:0.8rem; line-height:1.55; margin:2px 0 10px 0; }}
.disclaimer {{ border:1px solid {WARN_BORDER}; background:{WARN_BG}; color:{WARN_TEXT};
              padding:8px 12px; font-size:0.82rem; margin-bottom:12px; border-radius:6px; }}

/* sidebar navigation */
section[data-testid="stSidebar"] {{ background:{SIDEBAR}; border-right:1px solid {BORDER}; }}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{ padding-top:0.6rem; }}
section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap:2px; }}
section[data-testid="stSidebar"] div[role="radiogroup"] label {{ padding:9px 12px; width:100%;
    border-radius:6px; border-left:3px solid transparent; cursor:pointer; }}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div:first-child {{
    display:none; }}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover {{ background:{HOVER}; }}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] {{
    background:{SELECTED}; border-left-color:{ACCENT}; }}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] p {{
    color:{ACCENT}; font-weight:600; }}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] p {{ white-space:pre; }}
.side-label {{ color:{GREY}; font-size:0.66rem; letter-spacing:0.14em; text-transform:uppercase;
              margin:20px 0 6px 2px; }}

/* progress bars, pills, security header */
.hbar {{ margin:12px 0 16px 0; }}
.hbar-top {{ display:flex; justify-content:space-between; font-size:0.82rem; margin-bottom:6px; }}
.hbar-top span:first-child {{ color:{GREY}; }}
.hbar-track {{ position:relative; background:{TRACK}; border-radius:4px; height:8px; }}
.hbar-fill {{ height:8px; border-radius:4px; }}
.pill {{ display:inline-block; background:{PANEL}; border:1px solid {BORDER}; border-radius:6px;
        padding:7px 12px; font-size:0.8rem; margin:4px 8px 4px 0; color:{TEXT_SOFT}; }}
.pill b {{ color:{TEXT_STRONG}; }}
.sec-title {{ font-size:1.5rem; font-weight:600; letter-spacing:-0.01em; color:{TEXT_STRONG}; }}
.sec-title .exch {{ color:{GREY}; font-size:0.85rem; font-weight:400; margin-left:10px; }}
.sec-row {{ display:flex; flex-wrap:wrap; gap:36px; margin:10px 0 4px 0; align-items:flex-end; }}
.sec-row .px {{ font-size:1.7rem; font-weight:600; color:{TEXT_STRONG}; }}
.sec-row .ccy {{ color:{GREY}; font-size:0.75rem; margin:0 8px 0 4px; }}
.sec-row .k {{ color:{GREY}; font-size:0.72rem; margin-top:2px; }}
.sec-row .v {{ font-size:0.95rem; font-weight:500; }}

/* tabs, forms, tables */
button[data-baseweb="tab"] {{ font-weight:600; letter-spacing:0.05em; padding-left:6px;
                              padding-right:6px; margin-right:14px; }}
[data-testid="stForm"] {{ border-radius:8px; padding:18px 20px; border-color:{BORDER}; }}
[data-testid="stExpander"] {{ border-radius:8px; border-color:{BORDER}; }}
[data-testid="stVerticalBlock"] {{ gap:0.9rem; }}
hr {{ margin:0.9rem 0; opacity:0.5; border-color:{BORDER}; }}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def gbp(value, signed=False):
    """£1,234.56 (with + / - when signed)."""
    sign = "-" if value < 0 else ("+" if signed else "")
    return f"{sign}£{abs(value):,.2f}"


def tone(value):
    """CSS class for gains / losses."""
    return "pos" if value > 0 else "neg" if value < 0 else ""


def tile(label, value, sub="", css=""):
    """HTML for one headline tile. Only app-generated numbers go through here."""
    return (f'<div class="tile"><div class="label">{html.escape(label)}</div>'
            f'<div class="value {css}">{value}</div><div class="sub">{sub}</div></div>')


def section(title, note=""):
    """Orange section heading."""
    st.markdown(f'<div class="section">{html.escape(title)}</div>'
                + (f'<div class="note">{html.escape(note)}</div>' if note else ""),
                unsafe_allow_html=True)


def md_escape(text):
    """Escape markdown characters in text from outside sources."""
    for char in "\\`*_{}[]<>()#+!|~":
        text = text.replace(char, "\\" + char)
    return text


def donut(df, label_col, value_col, centre_text, centre_sub):
    """Altair donut chart with a centre label."""
    base = alt.Chart(df).encode(
        theta=alt.Theta(f"{value_col}:Q", stack=True),
        color=alt.Color(f"{label_col}:N", scale=alt.Scale(range=CH_PALETTE),
                        legend=alt.Legend(orient="right", title=None, labelColor=CH_GREY)),
        tooltip=[label_col, alt.Tooltip(f"{value_col}:Q", format=",.1f")])
    arc = base.mark_arc(innerRadius=62, outerRadius=100, stroke="transparent")
    centre = alt.Chart(pd.DataFrame({"t": [centre_text]})).mark_text(
        size=17, color=CH_GREY, dy=-6).encode(text="t:N")
    sub = alt.Chart(pd.DataFrame({"t": [centre_sub]})).mark_text(
        size=11, color=CH_GREY, dy=12).encode(text="t:N")
    return (arc + centre + sub).properties(height=230).configure_view(strokeWidth=0)


def safe_link(title, url):
    """Markdown link for an outside headline (only http/https URLs become links)."""
    if url.startswith(("http://", "https://")):
        return f"[{md_escape(title)}]({url.replace(')', '%29')})"
    return md_escape(title)





def wordmark(height=24):
    """The XAVI logo as an inline vector. It takes the theme's text colour, so it works on both
    the light and the dark background. A has no crossbar, as in the original design."""
    return (f'<svg viewBox="-6 -6 298 82" height="{height}" role="img" aria-label="XAVI" '
            'style="color:var(--text_strong);display:inline-block;vertical-align:middle">'
            '<g fill="none" stroke="currentColor" stroke-width="8" stroke-miterlimit="10">'
            '<path d="M0 0 L66 70 M66 0 L0 70"/><path d="M96 70 L131 0 L166 70"/>'
            '<path d="M184 0 L219 70 L254 0"/><path d="M285 0 V70"/></g></svg>')


def hbar(label, pct, text, colour):
    """Labelled horizontal progress bar (all inputs are numbers or app text)."""
    pct = max(0.0, min(100.0, pct))
    return (f'<div class="hbar"><div class="hbar-top"><span>{html.escape(label)}</span>'
            f'<span>{html.escape(text)}</span></div><div class="hbar-track">'
            f'<div class="hbar-fill" style="width:{pct:.1f}%;background:{colour}"></div></div></div>')


def axis_right(title=None, **kwargs):
    """Right-hand y axis like a trading terminal; fixed width so stacked charts line up."""
    return alt.Axis(orient="right", title=title, labelColor=CH_GREY, gridColor=CH_GRID,
                    domain=False, tickColor=CH_GRID, minExtent=58, **kwargs)


def x_axis(show_labels=True):
    """Shared date axis."""
    return alt.X("Date:T", axis=alt.Axis(title=None, labelColor=CH_GREY, grid=False,
                                         domainColor=CH_GRID, tickColor=CH_GRID,
                                         labels=show_labels))


def timeline_chart(timeline, metric):
    """Returns over time from the dated ledger. metric: 'Value', 'Profit' or 'Return %'."""
    column = {"Value": "value", "Profit": "profit", "Return %": "return_pct"}[metric]
    df = pd.DataFrame({"Date": pd.to_datetime(timeline["dates"]), "Y": timeline[column],
                       "Invested": timeline["invested"], "Value": timeline["value"],
                       "Profit": timeline["profit"], "Return": timeline["return_pct"]})
    fmt = ".1f" if metric == "Return %" else ",.0f"
    y = alt.Y("Y:Q", scale=alt.Scale(zero=metric != "Value"), axis=axis_right(format=fmt))
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["Date"], empty=False)
    area = alt.Chart(df).mark_area(color=CH_ACCENT, opacity=0.08).encode(x="Date:T", y=y)
    line = alt.Chart(df).mark_line(color=CH_ACCENT, strokeWidth=2).encode(x=x_axis(), y=y)
    if metric == "Value":      # money in (net of sales) as a dashed step line
        guide = alt.Chart(df).mark_line(color=CH_GREY, strokeDash=[5, 5], interpolate="step-after"
                                        ).encode(x="Date:T", y=alt.Y("Invested:Q"))
    else:                      # zero line
        guide = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
            color=CH_GREY, strokeDash=[5, 5]).encode(y="y:Q")
    points = alt.Chart(df).mark_point(opacity=0).encode(x="Date:T", y="Y:Q").add_params(nearest)
    rule = alt.Chart(df).mark_rule(color=CH_RULE).encode(
        x="Date:T", tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"),
                             alt.Tooltip("Value:Q", format=",.2f", title="Value £"),
                             alt.Tooltip("Invested:Q", format=",.2f", title="Net invested £"),
                             alt.Tooltip("Profit:Q", format="+,.2f", title="Profit £"),
                             alt.Tooltip("Return:Q", format="+.2f", title="Return %")]
    ).transform_filter(nearest)
    return (area + line + guide + points + rule).properties(height=300).configure_view(
        strokeWidth=0).configure(background="transparent",
                                 autosize=alt.AutoSizeParams(type="fit", contains="padding"))


def security_chart(data):
    """Three panels (price, volume, drawdown) with identical axis widths so they line up."""
    df = pd.DataFrame({"Date": pd.to_datetime(data["dates"]), "Close": data["close"],
                       "Volume": data["volume"], "Drawdown": core.drawdown_series(data["close"])})
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["Date"], empty=False)
    price = alt.Chart(df).mark_line(color=CH_ACCENT, strokeWidth=1.8).encode(
        x=x_axis(False), y=alt.Y("Close:Q", scale=alt.Scale(zero=False), axis=axis_right()))
    points = alt.Chart(df).mark_point(opacity=0).encode(x="Date:T", y="Close:Q").add_params(nearest)
    rule = alt.Chart(df).mark_rule(color=CH_RULE).encode(
        x="Date:T", tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"),
                             alt.Tooltip("Close:Q", format=",.2f"),
                             alt.Tooltip("Drawdown:Q", format=".2f", title="Below peak %")]
    ).transform_filter(nearest)
    volume = alt.Chart(df).mark_bar(color=CH_VOLUME, opacity=0.55).encode(
        x=x_axis(False), y=alt.Y("Volume:Q", axis=axis_right(format="~s")))
    drawdown = alt.Chart(df).mark_area(color=CH_RED, opacity=0.25, line={"color": CH_RED,
                                                                      "strokeWidth": 1.2}).encode(
        x=x_axis(), y=alt.Y("Drawdown:Q", axis=axis_right(format=".0f")))
    def finish(chart, height, title):
        """Fit one panel to the container and give it a small grey title."""
        return chart.properties(height=height, title=alt.TitleParams(
            title, anchor="start", color=CH_GREY, fontSize=11, fontWeight=500)).configure_view(
            strokeWidth=0).configure(background="transparent",
                                     autosize=alt.AutoSizeParams(type="fit", contains="padding"))

    return (finish(price + points + rule, 280, "Price"), finish(volume, 70, "Volume"),
            finish(drawdown, 100, "Drawdown % from peak"))


def performance_chart(rows_):
    """Horizontal bars of profit/loss % per holding (green up, red down)."""
    df = pd.DataFrame([{"Holding": r["ticker"], "P/L %": r["pl_pct"] or 0.0} for r in rows_])
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=3, size=16).encode(
        y=alt.Y("Holding:N", sort="-x", axis=alt.Axis(title=None, labelColor=CH_GREY,
                                                      domain=False, ticks=False,
                                                      labelOverlap=False, labelLimit=90)),
        x=alt.X("P/L %:Q", axis=alt.Axis(title=None, labelColor=CH_GREY, gridColor=CH_GRID,
                                         format=".0f", domain=False)),
        color=alt.condition("datum['P/L %'] >= 0", alt.value(CH_GREEN), alt.value(CH_RED)),
        tooltip=["Holding", alt.Tooltip("P/L %:Q", format="+.2f")])
    return bars.properties(height=max(70, 42 * len(df))).configure_view(
        strokeWidth=0).configure(background="transparent",
                 autosize=alt.AutoSizeParams(type="fit", contains="padding"))


def human(number):
    """12,345,678,900 -> 12.35B."""
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(number) >= limit:
            return f"{number / limit:.2f}{suffix}"
    return f"{number:,.0f}"


CURRENCY_SYMBOL = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "CAD": "C$", "AUD": "A$"}
PAGES = {"Dashboard": "▣   Dashboard", "Analysis": "◧   Analysis", "News": "◈   News",
         "Security": "◎   Security", "Settings": "⚙   Settings"}
PAGE_TITLES = {"Dashboard": "Portfolio overview", "Analysis": "Diversification analysis",
               "News": "News feed", "Security": "Security research", "Settings": "Settings"}


def go_to(page, ticker=None, add_ticker=None):
    """Button callback: jump to a page (optionally pre-filling the add-position ticker)."""
    st.session_state.page = page
    if ticker:
        st.session_state.sec_ticker = ticker
    if add_ticker:
        st.session_state[f"add_ticker_{st.session_state.get('add_nonce', 0)}"] = add_ticker


# ---------------------------------------------------------------------------
# login (local accounts, no recovery)
# ---------------------------------------------------------------------------
def login_screen():
    """Log in / create account page. Stops the script until someone is logged in."""
    st.markdown(f'<div style="text-align:center;margin:56px 0 30px 0">{wordmark(34)}</div>',
                unsafe_allow_html=True)
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        tab_in, tab_new = st.tabs(["Log in", "Create account"])
        with tab_in:
            with st.form("login_form"):
                user = st.text_input("Username")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Log in", width="stretch"):
                    ok, msg = auth.verify_login(user, password)
                    if ok:
                        st.session_state.user = auth.clean_username(user)
                        st.rerun()
                    st.error(msg)
        with tab_new:
            with st.form("signup_form"):
                name = st.text_input("Display name (optional)")
                user = st.text_input("Choose a username",
                                     help="3-20 characters: letters, numbers, . _ -")
                password = st.text_input("Choose a password", type="password",
                                         help=f"At least {auth.MIN_PASSWORD} characters.")
                again = st.text_input("Repeat password", type="password")
                st.warning(auth.NO_RECOVERY)
                agree = st.checkbox("I understand there is no password recovery")
                if st.form_submit_button("Create account", width="stretch"):
                    if password != again:
                        st.error("The two passwords don't match.")
                    elif not agree:
                        st.error("Please tick the box to confirm you understand.")
                    else:
                        ok, msg = auth.create_account(user, password, name)
                        if ok:
                            st.session_state.user = auth.clean_username(user)
                            st.rerun()
                        st.error(msg)
    st.markdown(f'<div class="note" style="text-align:center;margin-top:28px">{DISCLAIMER}</div>',
                unsafe_allow_html=True)
    st.stop()


if st.session_state.get("user") not in auth.load_users():
    st.session_state.pop("user", None)
    login_screen()

# ---------------------------------------------------------------------------
# load this person's data (prices are cached on disk, so this is quick)
# ---------------------------------------------------------------------------
messages = []
core.notify = messages.append
user_folder = auth.user_dir(st.session_state.user)
state = {"portfolio": core.load_portfolio(user_folder), "cache": core.load_cache(),
         "config": core.load_config(user_folder), "dir": user_folder}

if "force_refresh" not in st.session_state:
    st.session_state.force_refresh = False
force = st.session_state.force_refresh
st.session_state.force_refresh = False
with st.spinner("Loading prices..."):
    quotes = core.get_quotes(state, force=force)
metrics = core.compute_metrics(state, quotes)
rows = metrics["rows"]
fx = state["cache"].get("GBP_USD", {}).get("rate")
stamps = [r["timestamp"] for r in rows if r["timestamp"]]
price_time = core.show_time(state, min(stamps)) if stamps else "no data yet"

st.session_state.setdefault("page", "Dashboard")
who = state["config"].get("display_name") or st.session_state.user
with st.sidebar:
    st.markdown(f'<div style="margin:8px 0 22px 2px">{wordmark(22)}</div>',
                unsafe_allow_html=True)
    query = st.text_input("Search", placeholder="Search a ticker  (AAPL, BP.L)",
                          label_visibility="collapsed", key="search_box").strip().upper()
    if query and query != st.session_state.get("last_search"):
        st.session_state.last_search = query
        if core.valid_ticker(query):
            st.session_state.sec_ticker = query
            st.session_state.sec_pick = query
            st.session_state.page = "Security"
        else:
            st.warning("Tickers are 1-10 letters or numbers, e.g. AAPL or BP.L.")
    st.markdown('<div class="side-label">Navigate</div>', unsafe_allow_html=True)
    page = st.radio("Navigate", list(PAGES), format_func=PAGES.get, key="page",
                    label_visibility="collapsed")
    st.markdown('<div class="side-label">Allowance</div>', unsafe_allow_html=True)
    st.markdown(hbar(state["config"]["account_label"], metrics["isa_used_pct"],
                     f"{metrics['isa_used_pct']:.0f}% used", BLUE), unsafe_allow_html=True)
    st.markdown(f'<div class="note">{gbp(metrics["isa_remaining"])} left of '
                f'{gbp(metrics["isa_allowance"])}<br>Tax year ends '
                f'{html.escape(state["config"]["tax_year_end"])}</div>', unsafe_allow_html=True)
    st.markdown('<div class="side-label">Account</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="note">{html.escape(who)}<br>Prices: {price_time}<br>'
                f'Light / dark: ⋮ menu (top right) → Settings</div>', unsafe_allow_html=True)
    if st.button("Refresh prices", width="stretch", key="side_refresh"):
        st.session_state.force_refresh = True
        st.rerun()
    if st.button("Log out", width="stretch", key="side_logout"):
        st.session_state.clear()
        st.rerun()

head_left, head_mid, head_right = st.columns([4, 3, 3], vertical_alignment="center")
with head_left:
    st.markdown(f'<div class="page-title">{PAGE_TITLES[page]}</div>'
                f'<div class="meta">{html.escape(state["config"]["account_label"])} · GBP · '
                f'Prices: {price_time}</div>', unsafe_allow_html=True)
with head_mid:
    st.markdown(f'<div style="text-align:center">{wordmark(24)}</div>', unsafe_allow_html=True)
st.markdown('<div class="topbar" style="padding:0;margin:6px 0 14px 0"></div>',
            unsafe_allow_html=True)
for message in messages:
    st.warning(message)
if any(r["stale"] for r in rows):
    st.warning("Some prices could not be refreshed and may be out of date (marked below).")


# ===========================================================================
# PORT
# ===========================================================================
if page == "Dashboard":
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))
    score, effective = core.diversification_score([r["hold_weight"] for r in rows])
    cols = st.columns(6)
    pl_class = tone(metrics["pl"])
    tiles = [
        ("Total value", gbp(metrics["total"]), "incl. cash", ""),
        ("Invested", gbp(metrics["invested"]), "cost basis", ""),
        ("Unrealised", gbp(metrics["pl"], True),
         f'{metrics["pl_pct"]:+.2f}%' if metrics["pl_pct"] is not None else "—", pl_class),
        ("Cash", gbp(metrics["cash"]), f'{metrics["cash_weight"]:.1f}%', ""),
        ("Spread", f"{score}<span style='font-size:0.9rem;color:{GREY}'>/100</span>",
         "see ANLZ", ""),
        ("GBP/USD", f"{fx:.4f}" if fx else "—", "live rate", ""),
    ]
    for col, (label, value, sub, css) in zip(cols, tiles):
        col.markdown(tile(label, value, sub, css), unsafe_allow_html=True)

    left, right = st.columns([5, 3])
    with left:
        section("Returns over time", "Built from your dated buys and sells. Shares with no buy "
                "date count as held from the start.")
        c_range, c_metric = st.columns([3, 2])
        rng = c_range.segmented_control("Range", list(core.HISTORY_RANGES), default="1Y",
                                        key="bal_range", label_visibility="collapsed") or "1Y"
        metric = c_metric.segmented_control("Show", ["Value", "Profit", "Return %"],
                                            default="Profit", key="bal_metric",
                                            label_visibility="collapsed") or "Profit"
        if not rows and not core.get_transactions(state):
            st.info("Add a position (with its buy date) or a closed trade to see your returns.")
        else:
            with st.spinner("Loading history..."):
                timeline = core.portfolio_timeline(state, rng)
            if timeline:
                st.altair_chart(timeline_chart(timeline, metric), width="stretch")
                series = timeline[{"Value": "value", "Profit": "profit",
                                   "Return %": "return_pct"}[metric]]
                hi, lo = series.index(max(series)), series.index(min(series))
                show = (lambda v: f"{v:+.2f}%") if metric == "Return %" else (lambda v: gbp(v))
                st.markdown(
                    f'<span class="pill">Highest <b>{show(series[hi])}</b> · '
                    f'{core.fmt_date(timeline["dates"][hi])}</span>'
                    f'<span class="pill">Lowest <b>{show(series[lo])}</b> · '
                    f'{core.fmt_date(timeline["dates"][lo])}</span>'
                    f'<span class="pill">Profit now <b>{gbp(timeline["profit"][-1], True)}</b> '
                    f'({timeline["return_pct"][-1]:+.2f}%)</span>'
                    f'<span class="pill">Net invested <b>{gbp(timeline["invested"][-1])}</b></span>',
                    unsafe_allow_html=True)
                st.caption("Return % = total profit (realised + unrealised) / everything bought "
                           "so far. Value view: dashed line = net money in. Starts from your first "
                           "dated purchase.")
                if timeline["skipped"]:
                    st.caption("Not included (no price history in a supported currency): "
                               + ", ".join(timeline["skipped"]))
            else:
                st.info("Price history isn't available right now. Try Refresh.")

        closed = core.closed_positions(state)
        if closed:
            section("Closed positions", "Every sale: when it was opened and closed, and the return")
            st.dataframe(pd.DataFrame([{
                "Ticker": c["ticker"], "Opened": c["opened"] or "—", "Closed": c["closed"],
                "Days": c["days"], "Shares": c["shares"], "Cost £": c["cost"],
                "Proceeds £": c["proceeds"], "Profit £": c["profit"],
                "Return %": c["return_pct"]} for c in closed]), hide_index=True, width="stretch",
                column_config={"Cost £": st.column_config.NumberColumn(format="£%.2f"),
                               "Proceeds £": st.column_config.NumberColumn(format="£%.2f"),
                               "Profit £": st.column_config.NumberColumn(format="£%.2f"),
                               "Return %": st.column_config.NumberColumn(format="%+.2f%%"),
                               "Shares": st.column_config.NumberColumn(format="%g")})
        transactions = core.get_transactions(state)
        if transactions:
            with st.expander(f"Transactions ({len(transactions)}) · fix a date"):
                st.caption("Only the Date column can be changed. Fix a purchase that defaulted to "
                           "today so the returns chart starts when you really bought.")
                ledger = pd.DataFrame([{
                    "ID": t["id"], "Date": pd.to_datetime(t["date"]).date(), "Ticker": t["ticker"],
                    "Type": t["type"], "Shares": t["shares"], "Price £": t["price"],
                    "Fees £": t["fees"], "Total £": t["total"],
                    "Realised £": t.get("realised_pl"), "Notes": t.get("notes", "")}
                    for t in sorted(transactions, key=lambda t: (t["date"], t["id"]),
                                    reverse=True)])
                edited_ledger = st.data_editor(
                    ledger, hide_index=True, width="stretch", key="ledger_editor",
                    disabled=[c for c in ledger.columns if c != "Date"],
                    column_config={"Date": st.column_config.DateColumn(
                        max_value=pd.Timestamp.today().date(), format="YYYY-MM-DD")})
                redated = False
                for old, new in zip(ledger.to_dict("records"), edited_ledger.to_dict("records")):
                    if new["Date"] is not None and new["Date"] != old["Date"]:
                        ok, msg = core.redate_transaction(state, int(old["ID"]),
                                                          pd.Timestamp(new["Date"]).date().isoformat())
                        (st.success if ok else st.error)(msg)
                        redated = redated or ok
                if redated:
                    st.rerun()
    with right:
        section("Progress")
        largest = max((r["hold_weight"] for r in rows), default=0.0)
        st.markdown(
            hbar("Allowance used", metrics["isa_used_pct"], f"{metrics['isa_used_pct']:.1f}%",
                 BLUE) +
            hbar("Cash share of total", metrics["cash_weight"], f"{metrics['cash_weight']:.1f}%",
                 ACCENT) +
            hbar("Largest holding", largest, f"{largest:.1f}% of holdings",
                 RED if largest > 40 else GREEN), unsafe_allow_html=True)
        if rows:
            section("Performance by holding", "Profit / loss % on cost")
            st.altair_chart(performance_chart(rows), width="stretch")

    section("Latest major news", "Last few days, important stories only. See NEWS for all.")
    all_news, _ = core.fetch_news(state)
    major = sorted([i for i in all_news if i["score"] >= 4 and i.get("kind") != "opinion"],
                   key=lambda i: i["published"], reverse=True)[:3]
    for story in major:
        when = core.show_time(state, story["published"])
        st.markdown(f":green[**{md_escape(story['ticker'])}**] &nbsp; :gray[{when} · "
                    f"{md_escape(story['source'])}]  \n{safe_link(story['title'], story['url'])}")
    if not major:
        st.markdown('<div class="note">No major news about your holdings in the last few days.'
                    '</div>', unsafe_allow_html=True)

    section("Holdings", "Edit shares, average cost (£ per share), name, sector or region directly in the table.")
    if not rows:
        st.info("Welcome! Your account starts empty. Add your first position below, set your "
                "cash and yearly allowance on the right, and customise everything in SET.")
    else:
        frame = pd.DataFrame([{
            "Ticker": r["ticker"], "Name": r["name"], "Shares": float(r["shares"]),
            "Avg cost £": float(r["avg_cost"]),
            "Price £": None if r["no_price"] else float(r["price"]),
            "Value £": float(r["value"]), "P/L £": float(r["pl"]),
            "P/L %": r["pl_pct"], "Weight %": float(r["weight"]),
            "Sector": r["sector"], "Region": r["region"],
            "Data": "stale" if r["stale"] else "ok"} for r in rows])
        edited = st.data_editor(
            frame, hide_index=True, width="stretch", key="holdings",
            disabled=["Ticker", "Price £", "Value £", "P/L £", "P/L %", "Weight %", "Data"],
            column_config={
                "Shares": st.column_config.NumberColumn(format="%g", min_value=0.0001,
                                                        max_value=float(core.MAX_SHARES)),
                "Avg cost £": st.column_config.NumberColumn(format="£%.2f", min_value=0.0001,
                                                            max_value=10000.0),
                "Price £": st.column_config.NumberColumn(format="£%.2f"),
                "Value £": st.column_config.NumberColumn(format="£%.2f"),
                "P/L £": st.column_config.NumberColumn(format="£%.2f"),
                "P/L %": st.column_config.NumberColumn(format="%.2f%%"),
                "Weight %": st.column_config.NumberColumn(format="%.1f%%")})
        changed = False
        for old, new in zip(frame.to_dict("records"), edited.to_dict("records")):
            kwargs = {}
            if new["Shares"] is None or new["Avg cost £"] is None:
                st.error(f"{old['Ticker']}: shares and average cost can't be empty.")
                continue
            if abs(new["Shares"] - old["Shares"]) > 1e-9:
                kwargs["shares"] = float(new["Shares"])
            if abs(new["Avg cost £"] - old["Avg cost £"]) > 1e-6:
                kwargs["avg_cost"] = float(new["Avg cost £"])
            if new["Name"] != old["Name"]:
                kwargs["name"] = new["Name"]
            if new["Sector"] != old["Sector"]:
                kwargs["sector"] = new["Sector"]
            if new["Region"] != old["Region"]:
                kwargs["region"] = new["Region"]
            if kwargs:
                ok, msg = core.update_position(state, old["Ticker"], **kwargs)
                (st.success if ok else st.error)(msg)
                changed = changed or ok
        if changed:
            st.rerun()
        st.markdown(
            f'<div class="note">Weights are % of total value including cash. Costs are in £ per '
            f'share. A dollar cost price should be converted to £ first. Source: Yahoo Finance via '
            f'yfinance, cached up to 60 minutes.</div>', unsafe_allow_html=True)

    section("Add position or closed trade", "Enter what you actually paid, and the dates, so cost, P&L and your returns chart are exact.")
    nonce = st.session_state.setdefault("add_nonce", 0)

    def key(name):
        """Widget keys change after each successful add, which clears the form."""
        return f"add_{name}_{nonce}"

    MODES = ["Shares + price per share", "Shares + total paid (£)",
             "Amount invested (£) + price per share"]
    status = st.radio("Position", ["Still holding", "Already sold"], horizontal=True,
                      key=key("status"),
                      help="'Already sold' records a finished trade (buy date and sell date) so its "
                           "result appears in your returns. It doesn't change holdings or cash.")
    sold = status == "Already sold"
    mode = st.radio("What do you know about the purchase?", MODES, horizontal=True,
                    key=key("mode"))
    c1, c2, c3, c4 = st.columns([2, 1.4, 1.6, 1.6])
    ticker = c1.text_input("Ticker", placeholder="e.g. AZN.L, BP.L, AAPL", key=key("ticker"))
    shares = amount = price = total_paid = None
    if mode == MODES[2]:
        amount = c2.number_input("Amount invested £", min_value=0.0, step=10.0, value=0.0,
                                 key=key("amount"), help="Money spent on the shares, before fees.")
    else:
        shares = c2.number_input("Shares", min_value=0.0, max_value=float(core.MAX_SHARES),
                                 value=0.0, step=1.0, key=key("shares"))
    if mode == MODES[1]:
        total_paid = c3.number_input("Total paid £", min_value=0.0, step=10.0, value=0.0,
                                     key=key("total"), help="Everything you paid, including fees.")
    else:
        price = c3.number_input("Price paid per share", min_value=0.0, value=0.0, step=0.01,
                                format="%.4f", key=key("price"),
                                help="The price you bought at, in the currency chosen next.")
    ccy_label = "£ pounds"
    if price is not None:
        ccy_label = c4.selectbox("Price currency", ["£ pounds", "p pence", "$ US dollars"],
                                 key=key("ccy"), help="UK shares are often quoted in pence.")
    ccy = {"£ pounds": "GBP", "p pence": "GBp", "$ US dollars": "USD"}[ccy_label]
    d1, d2, d3, d4 = st.columns([1.4, 1.2, 1.6, 2.6])
    fx_rate = None
    if ccy == "USD":
        fx_rate = d1.number_input("Dollars per £1 when you bought", min_value=0.0, value=0.0,
                                  step=0.01, format="%.4f", key=key("fx"),
                                  help="Leave at 0 to use today's rate.") or None
    fees = d2.number_input("Fees £", min_value=0.0, step=0.5, value=0.0, key=key("fees"),
                           disabled=mode == MODES[1], help="Dealing fees and stamp duty. "
                           "Not needed when you enter the total paid.")
    bought = d3.date_input("Date bought (puts it on the chart)", value=None, max_value=pd.Timestamp.today(),
                           key=key("date"))
    notes = d4.text_input("Notes (optional)", key=key("notes"))
    pay_cash = False if sold else st.checkbox(
        "Pay from my cash balance (reduces cash)", value=True, key=key("paycash"))
    sell_gbp = sell_problem = sell_date = None
    sell_fees = 0.0
    if sold:
        s1, s2, s3, s4 = st.columns([1.4, 1.6, 1.4, 1.2])
        sell_date = s1.date_input("Date sold", value=None, max_value=pd.Timestamp.today(),
                                  key=key("sdate"))
        sell_price = s2.number_input("Sale price per share", min_value=0.0, value=0.0, step=0.01,
                                     format="%.4f", key=key("sprice"))
        sell_label = s3.selectbox("Sale price currency", ["£ pounds", "p pence", "$ US dollars"],
                                  key=key("sccy"))
        sell_fees = s4.number_input("Sale fees £", min_value=0.0, step=0.5, value=0.0,
                                    key=key("sfees"))
        sell_ccy = {"£ pounds": "GBP", "p pence": "GBp", "$ US dollars": "USD"}[sell_label]
        sell_fx = None
        if sell_ccy == "USD":
            sell_fx = st.number_input("Dollars per £1 when you sold", min_value=0.0, value=0.0,
                                      step=0.01, format="%.4f", key=key("sfx"),
                                      help="Leave at 0 to use today's rate.") or None
        if sell_price:
            sell_gbp, sell_problem = core.price_to_gbp(state, sell_price, sell_ccy, sell_fx)

    got_shares, total_cost, problem = core.cost_basis_from_inputs(
        state, shares=shares, price=price, price_ccy=ccy, total_paid=total_paid, amount=amount,
        fees=fees, fx_rate=fx_rate)
    entered = any(v for v in (shares, price, total_paid, amount))
    if problem is None:
        st.markdown(f'<div class="note">This position: {got_shares:,.4g} shares · total cost '
                    f'{gbp(total_cost)} · average {gbp(total_cost / got_shares)} a share.</div>',
                    unsafe_allow_html=True)
    elif entered:
        st.markdown(f'<div class="note">{html.escape(problem)}</div>', unsafe_allow_html=True)
    if sold and problem is None and sell_gbp and bought and sell_date:
        profit = got_shares * sell_gbp - (sell_fees or 0.0) - total_cost
        st.markdown(f'<div class="note">Closed trade: held {(sell_date - bought).days} days · '
                    f'profit {gbp(profit, True)} ({profit / total_cost * 100:+.1f}%).</div>',
                    unsafe_allow_html=True)
    if st.button("Record closed trade" if sold else "Add position", key=key("go")):
        if problem:
            st.error(problem)
        elif sold and (not bought or not sell_date):
            st.error("Enter both the date bought and the date sold.")
        elif sold and (sell_problem or not sell_gbp):
            st.error(sell_problem or "Enter the sale price per share.")
        elif sold:
            ok, msg = core.add_past_trade(
                state, ticker, got_shares, (total_cost - (fees or 0.0)) / got_shares,
                bought.isoformat(), sell_gbp, sell_date.isoformat(), fees or 0.0,
                sell_fees or 0.0, notes)
            if ok:
                st.session_state.add_nonce += 1
                st.session_state.flash = msg
                st.rerun()
            st.error(msg)
        else:
            ok, msg = core.add_position(
                state, ticker, got_shares, total_cost / got_shares, notes, pay_cash,
                bought_on=bought.isoformat() if bought else "", fees=fees or 0.0)
            if ok:
                st.session_state.add_nonce += 1
                st.session_state.flash = msg
                st.rerun()
            st.error(msg)

    if rows:
        with st.expander("Remove a position"):
            target = st.selectbox("Holding", [r["ticker"] for r in rows], key="remove_target")
            confirm = st.checkbox(f"Yes, remove {target} from the tracker (cash is unchanged)")
            if st.button("Remove") and confirm:
                ok, msg = core.remove_position(state, target)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()

    left, middle, right = st.columns([5, 5, 4])
    with left:
        if rows:
            section("Allocation by holding")
            df = pd.DataFrame({"Holding": [r["ticker"] for r in rows],
                               "Weight %": [r["hold_weight"] for r in rows]})
            st.altair_chart(donut(df, "Holding", "Weight %", gbp(metrics["holdings_value"]),
                                  "invested"), width="stretch")
    with middle:
        if rows:
            section("Allocation by sector", "Holding-level labels. Look-through is not included.")
            groups = core.group_by(rows, "sector")
            df = pd.DataFrame({"Sector": list(groups), "Weight %": [g["weight"] for g in
                                                                    groups.values()]})
            st.altair_chart(donut(df, "Sector", "Weight %", str(len(groups)), "sector labels"),
                            width="stretch")
    with right:
        section("Cash & allowance")
        with st.form("cash_form"):
            a, b = st.columns(2)
            new_cash = a.number_input("Cash in account £", min_value=0.0, step=10.0,
                                      value=float(state["config"]["cash"]))
            new_used = b.number_input("Allowance used £", min_value=0.0, step=10.0,
                                      value=float(state["config"]["isa_used"]))
            if st.form_submit_button("Save"):
                state["config"]["cash"], state["config"]["isa_used"] = new_cash, new_used
                core.save_config(state["config"], state["dir"])
                st.rerun()
        st.markdown(
            f'<div class="note">Allowance £{metrics["isa_allowance"]:,.0f} · used '
            f'{gbp(metrics["isa_used"])} ({metrics["isa_used_pct"]:.1f}%) · remaining '
            f'{gbp(metrics["isa_remaining"])}<br>Tax year ends '
            f'{html.escape(state["config"]["tax_year_end"])}. Investing cash already inside the '
            f'ISA doesn\'t use allowance; new deposits do.</div>', unsafe_allow_html=True)

# ===========================================================================
# ANLZ
# ===========================================================================
if page == "Analysis":
    if not rows:
        st.info("Add a holding to see the analysis.")
    else:
        score, effective = core.diversification_score([r["hold_weight"] for r in rows])
        colour = GREEN if score >= 70 else ACCENT if score >= 50 else RED
        label = "Well spread" if score >= 70 else "Moderately spread" if score >= 50 else \
            "Concentrated"
        c1, c2 = st.columns([1, 3])
        c1.markdown(tile("Diversification score",
                         f"<span style='color:{colour};font-size:2.2rem'>{score}</span>"
                         f"<span style='color:{GREY}'>/100</span>", label), unsafe_allow_html=True)
        c2.progress(score / 100)
        c2.markdown(f'<div class="note">Score = (1 - HHI) x 100, where HHI is the sum of squared '
                    f'portfolio weights. Equivalent to {effective:.1f} equal-sized positions '
                    f'(you hold {len(rows)}). Cash is excluded.</div>', unsafe_allow_html=True)

        section("Holding weights", "% of invested holdings")
        for r in sorted(rows, key=lambda x: -x["hold_weight"]):
            a, b = st.columns([1, 6])
            a.markdown(f"**{r['ticker']}** &nbsp; {r['hold_weight']:.1f}%")
            b.progress(min(r["hold_weight"], 100) / 100)

        section("Things worth knowing", "Facts about the spread, not suggestions")
        facts = []
        for r in rows:
            if r["hold_weight"] > 40 and len(rows) > 1:
                facts.append(f"{r['ticker']} makes up {r['hold_weight']:.1f}% of invested "
                             "holdings (a common rule of thumb flags anything over 40%).")
        for key, name in (("sector", "sector"), ("region", "region")):
            for group_name, g in core.group_by(rows, key).items():
                if g["weight"] > 60 and len(rows) > 1:
                    facts.append(f"{g['weight']:.1f}% of invested holdings share the {name} "
                                 f"label '{group_name}'.")
        if metrics["cash_weight"] > 40:
            facts.append(f"Cash is {metrics['cash_weight']:.1f}% of the total portfolio.")
        for fact in facts or ["No single holding, sector or region is above 40-60% of your "
                              "invested holdings."]:
            st.markdown(f"- {fact}")

        left, right = st.columns(2)
        for column, key, title in ((left, "sector", "Sector breakdown"),
                                   (right, "region", "Geographic exposure")):
            with column:
                section(title, "Holding-level labels; funds like SMT.L are not looked through.")
                grouped = core.group_by(rows, key)
                st.dataframe(pd.DataFrame({
                    key.title(): list(grouped),
                    "Value £": [g["value"] for g in grouped.values()],
                    "Weight %": [g["weight"] for g in grouped.values()]}),
                    hide_index=True, width="stretch",
                    column_config={"Value £": st.column_config.NumberColumn(format="£%.2f"),
                                   "Weight %": st.column_config.NumberColumn(format="%.1f%%")})

# ===========================================================================
# NEWS
# ===========================================================================
KIND_LABEL = {"filing": "SEC FILING", "press_release": "PRESS RELEASE",
              "company": "COMPANY SITE", "opinion": "OPINION / TIPS", "news": ""}
SCORE_NAME = {5: "CRITICAL", 4: "MAJOR", 3: "NOTABLE", 2: "MINOR", 1: "BACKGROUND"}
SCORE_COLOUR = {5: "red", 4: "red", 3: "orange", 2: "gray", 1: "gray"}


def render_story(item):
    """One news story: importance, time, ticker, headline link, why flagged, sources."""
    when = core.show_time(state, item["published"]) if item["published"] else "date unknown"
    words = core.matched_keywords(item)
    label = KIND_LABEL.get(item.get("kind", "news"), "")
    also = f" · also: {md_escape(', '.join(item['also'][:4]))}" if item.get("also") else ""
    st.markdown(
        f":{SCORE_COLOUR[item['score']]}[**{item['score']}/5 {SCORE_NAME[item['score']]}**]"
        f" &nbsp; {when} &nbsp; :green[**{md_escape(item['ticker'])}**]"
        + (f" &nbsp; :violet[**{label}**]" if label else "") + "  \n"
        f"**{safe_link(item['title'], item['url'])}**  \n"
        f":green[Flagged for: {md_escape(', '.join(words)) if words else 'no key terms'}]"
        f" &nbsp; :gray[{md_escape(item['source'])}{also}]")


@st.fragment(run_every="15m")
def news_panel():
    """News tab. Re-runs itself every 15 minutes so new stories appear without a refresh."""
    c1, c2, c3, c4 = st.columns([4, 2, 3, 1.3], vertical_alignment="bottom")
    level = c1.radio("Importance", ["All", "Notable+ (3-5)", "Major+ (4-5)", "Critical (5)"],
                     horizontal=True, key="news_level")
    choice = c2.selectbox("Holding", ["All"] + [r["ticker"] for r in rows], key="news_holding")
    order = c3.radio("Sort", ["Most important", "Newest"], horizontal=True, key="news_sort")
    refresh = c4.button("Refresh", width="stretch", key="news_refresh")
    show_opinion = st.checkbox("Include opinion & stock-tip sites (Motley Fool, Zacks, "
                               "Benzinga ...)", key="news_opinion")
    with st.spinner("Loading news..."):
        items, note = core.fetch_news(state, force=refresh)
    if note.strip():
        st.warning(note.strip())
    min_score = {"All": 1, "Notable+ (3-5)": 3, "Major+ (4-5)": 4, "Critical (5)": 5}[level]
    items = [i for i in items if i["score"] >= min_score
             and (choice == "All" or i["ticker"] == choice)
             and (show_opinion or i.get("kind") != "opinion")]
    if order == "Most important":
        items.sort(key=lambda i: (i["score"], i["published"]), reverse=True)
    updated = state["cache"].get(core.news_cache_key(state), {}).get("timestamp")
    sources = "Google News (Reuters, BBC, FT, CNBC and others), Yahoo Finance" + \
        (", SEC filings" if state["config"].get("sec_contact") else "")
    st.markdown(
        f'<div class="note">Updated {core.show_time(state, updated) if updated else "—"} · '
        f'{len(items)} stories · refreshes itself every 15 minutes · Sources: {sources}.<br>'
        'Only stories that name the company are shown; the same story from several outlets is '
        'grouped. Importance (1-5) is a keyword-and-source score, not a view on what a story '
        'means for prices.</div>', unsafe_allow_html=True)
    with st.expander("SEC filings (optional, US companies)"):
        st.markdown("Adds official 8-K / 10-Q / 10-K filings. The SEC requires automated tools "
                    "to identify themselves, so your email is sent to sec.gov in the request "
                    "header and nowhere else.")
        contact = st.text_input("Contact email", value=state["config"].get("sec_contact", ""),
                                key="sec_contact_input")
        if st.button("Save", key="sec_save"):
            state["config"]["sec_contact"] = contact.strip()
            core.save_config(state["config"], state["dir"])
            state["cache"].pop(core.news_cache_key(state), None)
            st.rerun()
    if not items:
        st.info("No stories match this filter.")
    for item in items[:30]:
        render_story(item)
        st.divider()


if page == "News":
    news_panel()

# ===========================================================================
# SECT
# ===========================================================================
if page == "Security":
    options = [r["ticker"] for r in rows]
    searched = st.session_state.get("sec_ticker")
    if searched and searched not in options:
        options = [searched] + options
    if not options:
        st.info("Search for any ticker in the sidebar (for example AAPL or BP.L), or add a "
                "holding on the Dashboard.")
    else:
        pick = st.selectbox("Security", options, key="sec_pick",
                            help="Your holdings, plus anything you searched for.")
        held = next((r for r in rows if r["ticker"] == pick), None)
        holding = next((h for h in state["portfolio"] if h["ticker"] == pick), None)
        rng = st.segmented_control("Range", list(core.HISTORY_RANGES), default="1Y",
                                   key="sec_range", label_visibility="collapsed") or "1Y"
        with st.spinner("Loading company data..."):
            info = core.fetch_company_info(state, pick)
            data = core.fetch_history(state, pick, rng)
            recent = core.fetch_history(state, pick, "1M") or data   # daily, for the header price
            dates = core.fetch_earnings(state, pick)
        if not info and not data:
            st.error(f"No data found for '{pick}'. Check the ticker (UK shares end in .L).")
        else:
            ccy = (recent or data or {}).get("currency") or info.get("currency") or ""
            symbol = CURRENCY_SYMBOL.get(ccy, "")
            suffix = "" if symbol else f" {ccy}"
            last = recent["close"][-1] if recent else None
            prev = recent["close"][-2] if recent and len(recent["close"]) > 1 else None
            change = (last - prev) if last is not None and prev else None
            change_pct = (change / prev * 100) if change is not None and prev else None
            title = html.escape(info.get("longName") or (held or {}).get("name") or pick)
            exchange = html.escape(info.get("fullExchangeName") or "")
            blocks = []
            if last is not None:
                move = ""
                if change is not None:
                    css = "pos" if change >= 0 else "neg"
                    move = f'<span class="{css}">{change:+,.2f} ({change_pct:+.2f}%)</span>'
                blocks.append(f'<div><span class="px">{symbol}{last:,.2f}</span>'
                              f'<span class="ccy">{html.escape(ccy)}</span>{move}'
                              f'<div class="k">Last close · {core.fmt_date(recent["dates"][-1])}</div>'
                              f'</div>')
            blocks.append(f'<div><div class="v">{core.fmt_date(dates[0]) if dates else "—"}</div>'
                          f'<div class="k">Next earnings date</div></div>')
            blocks.append(f'<div><div class="v">{html.escape(info.get("sector") or "—")}</div>'
                          f'<div class="k">Sector</div></div>')
            blocks.append(f'<div><div class="v">{html.escape(info.get("industry") or "—")}</div>'
                          f'<div class="k">Industry</div></div>')
            st.markdown(f'<div class="sec-title">{title}<span class="exch">{exchange} · '
                        f'{html.escape(pick)}</span></div><div class="sec-row">'
                        + "".join(blocks) + "</div>", unsafe_allow_html=True)
            if held is None:
                st.button("Add to my portfolio", key="sec_add", on_click=go_to,
                          args=("Dashboard", None, pick))

            if data:
                for panel in security_chart(data):
                    st.altair_chart(panel, width="stretch")
            else:
                st.info("Price history isn't available for this range right now.")

            scale = 0.01 if (info.get("currency") or "") in ("GBp", "GBX") else 1.0
            low, high = info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh")
            dividend = None
            if info.get("dividendRate") and last:
                dividend = info["dividendRate"] * scale / last * 100
            stats = st.columns(6)
            tiles_ = [
                ("Market cap", human(info["marketCap"] * (scale if scale != 1 else 1))
                 if info.get("marketCap") else "—", html.escape(info.get("currency") or "")),
                ("P/E", f"{info['trailingPE']:.1f}" if info.get("trailingPE") else "—", "trailing"),
                ("Forward P/E", f"{info['forwardPE']:.1f}" if info.get("forwardPE") else "—",
                 "expected"),
                ("Beta", f"{info['beta']:.2f}" if info.get("beta") else "—", "market = 1.00"),
                ("Dividend yield", f"{dividend:.2f}%" if dividend else "—", "annual rate / price"),
                ("Your position", gbp(held["value"]) if held else "—",
                 f"{held['pl_pct']:+.2f}%" if held and held["pl_pct"] is not None
                 else "not held"),
            ]
            for column, (label, value, sub) in zip(stats, tiles_):
                css = ""
                if label == "Your position" and held and held["pl_pct"]:
                    css = tone(held["pl_pct"])
                column.markdown(tile(label, value, sub, css), unsafe_allow_html=True)

            if low and high and last:
                low, high = low * scale, high * scale
                position = (last - low) / (high - low) * 100 if high > low else 50
                st.markdown(
                    f'<div class="hbar"><div class="hbar-top"><span>52-week range</span>'
                    f'<span>{symbol}{low:,.2f} - {symbol}{high:,.2f}{suffix}</span></div>'
                    f'<div class="hbar-track"><div class="hbar-fill" style="width:{max(0, min(100, position)):.1f}%;'
                    f'background:{ACCENT}"></div></div></div>', unsafe_allow_html=True)

            section("Analyst consensus & notes", "Source: Yahoo Finance. Not a recommendation.")
            key_ = info.get("recommendationKey")
            lines = ["**Analyst consensus (Yahoo Finance):** " + (
                key_.replace("_", " ").upper() if key_ and key_ != "none" else "no coverage found")
                + (f" from {info['numberOfAnalystOpinions']} analysts"
                   if info.get("numberOfAnalystOpinions") else "")
                + (f"; mean target {info['targetMeanPrice'] * scale:,.2f} {ccy}"
                   if info.get("targetMeanPrice") else "")]
            if holding:
                lines.append(f"**Your notes:** {md_escape(holding.get('notes') or '—')}")
            for line in lines:
                st.markdown("- " + line)

# ===========================================================================
# SET  (customise + account)
# ===========================================================================
if page == "Settings":
    cfg = state["config"]
    section("Profile & account", "Make it yours. Amounts are shown in GBP.")
    zones = ["Europe/London", "UTC", "Europe/Paris", "America/New_York", "America/Chicago",
             "America/Los_Angeles", "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney"]
    with st.form("profile_form"):
        c1, c2, c3 = st.columns(3)
        display = c1.text_input("Display name", value=cfg["display_name"], max_chars=40)
        label = c2.text_input("Account label", value=cfg["account_label"], max_chars=20,
                              help="Shown in the header, e.g. S&S ISA, SIPP, GIA, Lifetime ISA.")
        zone = c3.selectbox("Timezone", zones,
                            index=zones.index(cfg["timezone"]) if cfg["timezone"] in zones else 0)
        c4, c5 = st.columns(2)
        allowance = c4.number_input("Yearly allowance £", min_value=0.0, step=500.0,
                                    value=float(cfg["isa_allowance"]),
                                    help="UK ISA allowance is £20,000. Change it for other accounts.")
        tax_end = c5.text_input("Tax year ends", value=cfg["tax_year_end"], max_chars=20)
        if st.form_submit_button("Save profile"):
            cfg.update({"display_name": display.strip(), "account_label": label.strip() or "ISA",
                        "timezone": zone, "isa_allowance": allowance or 20000.0,
                        "tax_year_end": tax_end.strip()})
            core.save_config(cfg, state["dir"])
            st.rerun()

    section("News & data keys (optional)", "Stored only in your own account folder.")
    with st.form("keys_form"):
        k1, k2, k3 = st.columns(3)
        news_key = k1.text_input("NewsAPI key", value=cfg["api_keys"].get("newsapi", ""),
                                 type="password")
        finn_key = k2.text_input("Finnhub key", value=cfg["api_keys"].get("finnhub", ""),
                                 type="password")
        sec_mail = k3.text_input("SEC contact email", value=cfg.get("sec_contact", ""),
                                 help="Enables official SEC filings in NEWS (US companies). The "
                                      "SEC requires this to be sent with requests.")
        if st.form_submit_button("Save keys"):
            cfg["api_keys"].update({"newsapi": news_key.strip(), "finnhub": finn_key.strip()})
            cfg["sec_contact"] = sec_mail.strip()
            state["cache"].pop(core.news_cache_key(state), None)
            core.save_config(cfg, state["dir"])
            st.rerun()

    section("Your data")
    export = {"profile": {k: v for k, v in cfg.items() if k != "api_keys"},
              "portfolio": state["portfolio"]}
    d1, d2 = st.columns(2)
    d1.download_button("Download my data (JSON)", json.dumps(export, indent=2),
                       file_name=f"isa-terminal-{st.session_state.user}.json",
                       mime="application/json", width="stretch")
    with d2.expander("Reset everything to zero"):
        st.markdown("Removes all holdings and sets cash and allowance used to £0. Your account "
                    "and settings stay.")
        if st.button("Reset portfolio", key="reset_btn") and st.session_state.get("reset_ok"):
            state["portfolio"] = []
            core.save_portfolio([], state["dir"])
            cfg["cash"], cfg["isa_used"] = 0.0, 0.0
            core.save_config(cfg, state["dir"])
            st.session_state.reset_ok = False
            st.rerun()
        st.checkbox("Yes, reset my portfolio", key="reset_ok")

    section("Password & account", "There is no password recovery.")
    with st.form("password_form"):
        p1, p2, p3 = st.columns(3)
        old_pw = p1.text_input("Current password", type="password")
        new_pw = p2.text_input("New password", type="password")
        new_pw2 = p3.text_input("Repeat new password", type="password")
        if st.form_submit_button("Change password"):
            if new_pw != new_pw2:
                st.error("The new passwords don't match.")
            else:
                ok, msg = auth.change_password(st.session_state.user, old_pw, new_pw)
                (st.success if ok else st.error)(msg)
    with st.expander("Delete my account"):
        st.markdown("Permanently deletes this account and all of its data. This can't be undone "
                    "and can't be recovered.")
        del_pw = st.text_input("Password", type="password", key="del_pw")
        del_word = st.text_input("Type DELETE to confirm", key="del_word")
        if st.button("Delete account permanently"):
            if del_word != "DELETE":
                st.error("Type DELETE (in capitals) to confirm.")
            else:
                ok, msg = auth.delete_account(st.session_state.user, del_pw)
                if ok:
                    st.session_state.clear()
                    st.rerun()
                st.error(msg)

st.markdown(f'<div class="note" style="margin-top:28px">{DISCLAIMER}</div>',
            unsafe_allow_html=True)
