"""XAVI web app (Flask, desktop layout).

A thin web layer over the same tested code the terminal and Streamlit apps use:
    isa_core.py      prices (Yahoo Finance), news, history, FIFO sells, dated returns, CSV, backup
    auth.py          accounts: hashed passwords (scrypt), lockout after 5 wrong guesses
    xavi_charts.py   server-drawn SVG charts that follow light / dark mode

Run:  py -3.13 app_simple.py     then open http://127.0.0.1:5000
Information only - not financial advice.
"""

import io
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))          # isa_core.py and auth.py live in the repo root
DATA_DIR = os.environ.get("XAVI_DATA_DIR") or os.path.join(BASE_DIR, "portfolio_data")
os.makedirs(DATA_DIR, exist_ok=True)
os.environ.setdefault("ISA_USERS_DIR", os.path.join(DATA_DIR, "accounts"))   # before importing auth

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from markupsafe import Markup

import auth
import isa_core as core
import xavi_charts as charts

# ---------------------------------------------------------------------------------------------
# app setup
# ---------------------------------------------------------------------------------------------
def load_secret_key():
    """Session signing key: from XAVI_SECRET_KEY, else a random key kept in portfolio_data/."""
    env = os.environ.get("XAVI_SECRET_KEY")
    if env:
        return env
    path = os.path.join(DATA_DIR, "secret.key")
    if os.path.exists(path):
        return open(path, encoding="utf-8").read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(key)
    return key


DEMO = os.environ.get("XAVI_DEMO") == "1"          # public sandbox mode: no accounts, nothing kept
SANDBOX_DIR = os.path.join(DATA_DIR, "sandboxes")
SANDBOX_MINUTES = int(os.environ.get("XAVI_SANDBOX_MINUTES", "30"))
MAX_SANDBOXES = int(os.environ.get("XAVI_MAX_SANDBOXES", "200"))
MAX_HOLDINGS = 25                                   # per sandbox
SAMPLE_HOLDINGS = (("AAPL", 10, 150.0), ("MSFT", 5, 320.0), ("BP.L", 50, 4.50), ("HSBA.L", 100, 7.00))
_sandbox_lock = threading.Lock()

app = Flask(__name__)
app.secret_key = load_secret_key()
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SECURE=os.environ.get("XAVI_HTTPS") == "1",
                  TEMPLATES_AUTO_RELOAD=not DEMO, MAX_CONTENT_LENGTH=2 * 1024 * 1024)
if os.environ.get("XAVI_BEHIND_PROXY") == "1":      # hosted behind one reverse proxy (Render etc.)
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# The shared price cache is one JSON file; serialise writes, and keep Yahoo retries short so a
# page never hangs for long when Yahoo is slow.
_cache_lock = threading.RLock()
_original_save_cache = core.save_cache


def _locked_save_cache(cache):
    with _cache_lock:
        return _original_save_cache(cache)


core.save_cache = _locked_save_cache
core.FETCH_RETRIES = 2
core.RETRY_BACKOFF_SEC = 1
core.notify = lambda message: app.logger.warning("core: %s", message)

PUBLIC_ENDPOINTS = {"login", "signup", "static"}
RANGES = list(core.HISTORY_RANGES)
CURRENCY_SYMBOL = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "CAD": "C$", "AUD": "A$"}


# ---------------------------------------------------------------------------------------------
# template helpers
# ---------------------------------------------------------------------------------------------
def csrf_token():
    """Per-session token that every POST must carry (stops other sites posting for you)."""
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def csrf_input():
    """Hidden form field with the CSRF token."""
    return Markup(f'<input type="hidden" name="_csrf" value="{csrf_token()}">')


def human(number):
    """12,345,678,900 -> 12.35B."""
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(number) >= limit:
            return f"{number / limit:.2f}{suffix}"
    return f"{number:,.0f}"


app.jinja_env.globals.update(csrf_input=csrf_input, csrf_token=csrf_token, fmt_date=core.fmt_date,
                             human=human)


# ---- demo sandboxes: one private temporary folder per visitor, seeded with a sample portfolio ----
def _sandbox_path(sandbox_id):
    return os.path.join(SANDBOX_DIR, sandbox_id)


def seed_sample(folder):
    """Write the sample portfolio into `folder` (used for new sandboxes and 'Reset sample')."""
    os.makedirs(folder, exist_ok=True)
    state = {"portfolio": [], "cache": core.load_cache(), "config": core.load_config(folder), "dir": folder}
    state["config"].update(cash=5000.0, isa_used=15000.0, display_name="Demo visitor")
    core.save_config(state["config"], folder)
    core.save_portfolio([], folder)
    core.save_transactions([], folder)
    for ticker, shares, price in SAMPLE_HOLDINGS:
        core.add_position(state, ticker, shares, price, "sample holding", False, "2025-10-01",
                          skip_price_check=True)


def _expire_sandboxes():
    """Delete sandboxes nobody has touched for SANDBOX_MINUTES. Returns how many remain."""
    os.makedirs(SANDBOX_DIR, exist_ok=True)
    cutoff, kept = time.time() - SANDBOX_MINUTES * 60, 0
    for name in os.listdir(SANDBOX_DIR):
        path = _sandbox_path(name)
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
            else:
                kept += 1
        except OSError:
            pass
    return kept


def ensure_sandbox(fresh=False):
    """The visitor's sandbox id: reuse their live one, or make a new sample copy. None if we're full."""
    with _sandbox_lock:
        sid = session.get("sandbox", "")
        path = _sandbox_path(sid) if re.fullmatch(r"[0-9a-f]{16}", sid) else None
        if path and os.path.isdir(path) and not fresh:
            os.utime(path, None)                         # activity keeps it alive
            return sid
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        if _expire_sandboxes() >= MAX_SANDBOXES:
            return None
        sid = secrets.token_hex(8)
        seed_sample(_sandbox_path(sid))
        session["sandbox"], session["username"] = sid, "demo"
        return sid


# ---- simple per-visitor rate limit (in memory; fine for one small server) ----
_hits = defaultdict(deque)
_hits_lock = threading.Lock()
RATE_RULES = {"security": (20, 60), "news": (20, 60), "holdings_add": (30, 60)}   # name: (calls, seconds)
RATE_ALL = (240, 60)


def rate_limited(key, calls, seconds):
    now = time.time()
    with _hits_lock:
        queue = _hits[key]
        while queue and queue[0] < now - seconds:
            queue.popleft()
        if len(queue) >= calls:
            return True
        queue.append(now)
        if len(_hits) > 5000:                            # drop idle visitors
            for stale in [k for k, q in _hits.items() if not q or q[-1] < now - 120][:2000]:
                _hits.pop(stale, None)
    return False


@app.before_request
def gate():
    """Rate limit and CSRF check, then make sure there is a signed-in user (or demo sandbox)."""
    if request.endpoint != "static":
        who = request.remote_addr or "?"
        rule = RATE_RULES.get(request.endpoint)
        if (DEMO and rate_limited(("all", who), *RATE_ALL)) or \
                (DEMO and rule and rate_limited((request.endpoint, who), *rule)):
            return render_template("error.html", title="Slow down a little",
                                   message="Too many requests from your connection. Wait a minute and try again."), 429
    if request.method == "POST":
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Your session expired or the form was out of date. Go back, reload the "
                       "page and try again.")
    if request.endpoint == "static":
        return None
    if DEMO:
        if request.endpoint in ("login", "signup"):
            return redirect(url_for("dashboard"))
        had = session.get("sandbox")
        if ensure_sandbox() is None:
            return render_template("error.html", title="The demo is full",
                                   message="Lots of people are trying XAVI right now. Please try again in a few minutes."), 503
        if had and not os.path.isdir(_sandbox_path(had)):   # (only reached if it expired under us)
            flash("Your sandbox timed out, so here is a fresh sample portfolio.", "success")
        return None
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    user = session.get("username")
    if not user or user not in auth.load_users():
        session.clear()
        return redirect(url_for("login"))
    return None


def current_folder():
    """Folder holding this visitor's files: their sandbox (demo) or their account folder."""
    if DEMO:
        return _sandbox_path(session["sandbox"])
    return auth.user_dir(session["username"])


@app.context_processor
def inject_common():
    """Sidebar values, current time and theme-independent helpers for every page."""
    out = {"now": datetime.now(), "side_allowance_pct": 0.0, "side_allowance_left": 0.0,
           "side_allowance_total": 20000.0, "side_label": "S&S ISA", "side_name": "", "demo": DEMO,
           "sandbox_minutes": SANDBOX_MINUTES}
    user = session.get("username")
    if user:
        try:
            cfg = core.load_config(current_folder())
            total = float(cfg.get("isa_allowance") or 20000.0)
            used = float(cfg.get("isa_used") or 0.0)
            out.update(side_allowance_total=total, side_allowance_left=total - used,
                       side_allowance_pct=used / total * 100 if total else 0.0,
                       side_label=cfg.get("account_label", "S&S ISA"),
                       side_name=cfg.get("display_name") or user, username=user)
        except Exception:      # a broken config must not take the whole page down
            pass
    return out


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", title="That didn't work", message=error.description), 400


@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", title="Page not found",
                           message="That page doesn't exist."), 404


@app.errorhandler(500)
def server_error(error):
    return render_template("error.html", title="Something went wrong",
                           message="The error has been logged. Try again in a moment."), 500


# ---------------------------------------------------------------------------------------------
# data helpers
# ---------------------------------------------------------------------------------------------
def user_state():
    """This person's data in the shape isa_core expects (loaded fresh on every request)."""
    name = session["username"]
    folder = current_folder()
    return {"portfolio": core.load_portfolio(folder), "cache": core.load_cache(),
            "config": core.load_config(folder), "dir": folder, "user": name,
            "transactions": core.load_transactions(folder)}


def number(form, key, default=None):
    """A float from a form field (or `default` if blank / not a number)."""
    raw = (form.get(key) or "").replace(",", "").replace("£", "").replace("$", "").strip()
    try:
        return float(raw)
    except ValueError:
        return default


def result(ok, message, target="dashboard", **values):
    """Flash a result and go back to a page."""
    flash(message, "success" if ok else "error")
    return redirect(url_for(target, **values))


def price_time_text(state, metrics):
    """'4 Oct 2026, 18:04 BST (12 min ago)' for the oldest price on the page, plus a stale flag."""
    stamps = [r["timestamp"] for r in metrics["rows"] if r["timestamp"]]
    if not stamps:
        return "no price data yet", True
    oldest = min(stamps)
    minutes = int(core.age_seconds(oldest) // 60)
    ago = f"{minutes} min ago" if minutes < 90 else f"{minutes // 60}h ago"
    return f"{core.show_time(state, oldest)} ({ago})", any(r["stale"] for r in metrics["rows"])


# ---------------------------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("dashboard") if session.get("username") else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        ok, message = auth.verify_login(username, request.form.get("password", ""))
        if ok:
            session.clear()                           # fresh session (and fresh CSRF token)
            session["username"] = auth.clean_username(username)
            return redirect(url_for("dashboard"))
        flash(message, "error")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        form = request.form
        if form.get("password") != form.get("password_confirm"):
            flash("The two passwords don't match.", "error")
        elif not form.get("understood"):
            flash("Please tick the box to confirm you understand there is no password recovery.", "error")
        else:
            ok, message = auth.create_account(form.get("username", ""), form.get("password", ""),
                                              form.get("display_name", ""))
            if ok:
                flash("Account created. Please log in.", "success")
                return redirect(url_for("login"))
            flash(message, "error")
        return redirect(url_for("signup"))
    return render_template("signup.html", no_recovery=auth.NO_RECOVERY)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/demo/reset", methods=["POST"])
def demo_reset():
    """Demo mode: throw away this visitor's changes and start again from the sample portfolio."""
    if not DEMO:
        abort(404)
    if ensure_sandbox(fresh=True) is None:
        abort(400, "The demo is full right now. Try again in a few minutes.")
    return result(True, "Sample portfolio restored.")


# ---------------------------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------------------------
def timeline_chart(timeline, metric):
    """SVG for the returns chart plus the figures for the pills."""
    dates, value, invested = timeline["dates"], timeline["value"], timeline["invested"]
    profit, ret = timeline["profit"], timeline["return_pct"]
    tips = [f"{core.fmt_date(d)} · value £{v:,.2f} · net invested £{i:,.2f} · profit "
            f"{p:+,.2f} ({r:+.2f}%)" for d, v, i, p, r in zip(dates, value, invested, profit, ret)]
    if metric == "value":
        series, svg = value, charts.line_chart(dates, value, tips, guide=invested, kind="money",
                                               label="Value of holdings over time")
    elif metric == "return":
        series, svg = ret, charts.line_chart(dates, ret, tips, kind="pct", include_zero=True,
                                             label="Total return over time")
    else:
        series, svg = profit, charts.line_chart(dates, profit, tips, kind="money", include_zero=True,
                                                label="Total profit over time")
    hi, lo = series.index(max(series)), series.index(min(series))
    return svg, {"hi": series[hi], "hi_date": dates[hi], "lo": series[lo], "lo_date": dates[lo]}


@app.route("/dashboard")
def dashboard():
    state = user_state()
    if request.args.get("refresh"):
        core.get_quotes(state, force=True)
        return redirect(url_for("dashboard", range=request.args.get("range", "1Y"),
                                metric=request.args.get("metric", "profit")))
    quotes = core.get_quotes(state)
    metrics = core.compute_metrics(state, quotes)
    score, effective = core.diversification_score([r["hold_weight"] for r in metrics["rows"]])
    rng = request.args.get("range", "1Y")
    rng = rng if rng in RANGES else "1Y"
    metric = request.args.get("metric", "profit")
    metric = metric if metric in ("value", "profit", "return") else "profit"
    chart_svg, pills, timeline = None, None, None
    if state["portfolio"] or state["transactions"]:
        try:
            timeline = core.portfolio_timeline(state, rng)
        except Exception as err:
            app.logger.exception("timeline failed: %s", err)
        if timeline:
            chart_svg, pills = timeline_chart(timeline, metric)
    price_time, stale = price_time_text(state, metrics)
    return render_template(
        "dashboard.html", has_ledger=bool(state["transactions"]), m=metrics, rows=metrics["rows"],
        score=score,
        effective=effective, rng=rng, ranges=RANGES, metric=metric, chart_svg=chart_svg,
        pills=pills, timeline=timeline, closed=core.closed_positions(state),
        price_time=price_time, stale=stale, largest=max([r["hold_weight"] for r in metrics["rows"]] or [0]),
        groups=core.group_by(metrics["rows"], "sector"))


# ---------------------------------------------------------------------------------------------
# holdings and trades (all go through isa_core so the maths is the tested maths)
# ---------------------------------------------------------------------------------------------
@app.route("/holdings/add", methods=["POST"])
def holdings_add():
    """Add shares to a holding (JSON in, JSON out)."""
    data = request.get_json(silent=True) or {}
    state = user_state()
    if DEMO and len(state["portfolio"]) >= MAX_HOLDINGS:
        return jsonify(success=False, error=f"The demo is limited to {MAX_HOLDINGS} holdings."), 400
    ccy = data.get("currency", "GBP")
    if ccy not in ("GBP", "GBp", "USD"):
        return jsonify(success=False, error="Currency must be pounds, pence or dollars."), 400
    try:
        shares, price = float(data.get("shares") or 0), float(data.get("price") or 0)
        fees = float(data.get("fees") or 0)
        fx = float(data["fx"]) if data.get("fx") else None
    except (TypeError, ValueError):
        return jsonify(success=False, error="Shares, price and fees must be numbers."), 400
    bought = str(data.get("bought") or "").strip()
    if bought:
        when = core.parse_date(bought)
        if when is None or when > date.today():
            return jsonify(success=False, error="Date bought must be a past date like 2026-03-14."), 400
    got_shares, total, problem = core.cost_basis_from_inputs(
        state, shares=shares, price=price, price_ccy=ccy, fees=fees, fx_rate=fx)
    if problem:
        return jsonify(success=False, error=problem), 400
    ok, message = core.add_position(
        state, data.get("ticker", ""), got_shares, total / got_shares, str(data.get("notes", ""))[:200],
        bool(data.get("pay_cash")), bought_on=bought, fees=fees,
        skip_price_check=bool(data.get("allow_unlisted")))
    if not ok:
        return jsonify(success=False, error=message, can_force="not found on Yahoo" in message), 400
    ticker = data["ticker"].strip().upper()
    if data.get("sector") or data.get("region"):
        core.update_position(state, ticker, sector=str(data.get("sector", "")),
                             region=str(data.get("region", "")))
    flash(message, "success")
    return jsonify(success=True, message=message)


@app.route("/holdings/<ticker>/edit", methods=["POST"])
def holdings_edit(ticker):
    f = request.form
    ok, message = core.update_position(
        user_state(), ticker, shares=number(f, "shares"), avg_cost=number(f, "avg_cost"),
        name=f.get("name"), notes=f.get("notes"), sector=f.get("sector"), region=f.get("region"))
    return result(ok, message)


@app.route("/holdings/<ticker>/remove", methods=["POST"])
def holdings_remove(ticker):
    return result(*core.remove_position(user_state(), ticker))


@app.route("/holdings/<ticker>/sell", methods=["POST"])
def holdings_sell(ticker):
    f = request.form
    state = user_state()
    sold_on = (f.get("date") or "").strip()
    if sold_on and (core.parse_date(sold_on) is None or core.parse_date(sold_on) > date.today()):
        return result(False, "Date sold must be a past date like 2026-03-14.")
    ccy = f.get("currency", "GBP")
    price = number(f, "price")
    gbp = None
    if price:
        gbp, problem = core.price_to_gbp(state, price, ccy if ccy in ("GBP", "GBp", "USD") else "GBP",
                                         number(f, "fx"))
        if problem:
            return result(False, problem)
    return result(*core.sell_position(state, ticker, number(f, "shares", 0), gbp,
                                      number(f, "fees", 0.0), f.get("cash") != "manual",
                                      sold_on, f.get("notes", "")[:200]),
                  target="dashboard")


@app.route("/trades/closed", methods=["POST"])
def trades_closed():
    """Record a trade that is already finished (buy date and sell date in the past)."""
    f = request.form
    state = user_state()
    ccy = f.get("currency", "GBP")
    if ccy not in ("GBP", "GBp", "USD"):
        return result(False, "Currency must be pounds, pence or dollars.", target="history")
    buy, problem = core.price_to_gbp(state, number(f, "buy_price", 0), ccy, number(f, "buy_fx"))
    sell, problem2 = core.price_to_gbp(state, number(f, "sell_price", 0), ccy, number(f, "sell_fx"))
    if problem or problem2:
        return result(False, problem or problem2, target="history")
    return result(*core.add_past_trade(
        state, f.get("ticker", ""), number(f, "shares", 0), buy, f.get("buy_date", ""), sell,
        f.get("sell_date", ""), number(f, "buy_fees", 0.0), number(f, "sell_fees", 0.0),
        f.get("notes", "")[:200]), target="history")


@app.route("/transactions/<int:tx_id>/date", methods=["POST"])
def transactions_date(tx_id):
    return result(*core.redate_transaction(user_state(), tx_id, request.form.get("date", "")),
                  target="history")


# ---------------------------------------------------------------------------------------------
# analysis, news, security, history
# ---------------------------------------------------------------------------------------------
@app.route("/analysis")
def analysis():
    state = user_state()
    quotes = core.get_quotes(state)
    metrics = core.compute_metrics(state, quotes)
    rows = metrics["rows"]
    score, effective = core.diversification_score([r["hold_weight"] for r in rows])
    price_time, stale = price_time_text(state, metrics)
    label = "Well spread" if score >= 70 else "Moderately spread" if score >= 50 else "Concentrated"
    return render_template(
        "analysis.html", m=metrics, rows=sorted(rows, key=lambda r: -r["hold_weight"]), score=score,
        effective=effective, label=label, facts=core.concentration_facts(metrics) if rows else [],
        sectors=core.group_by(rows, "sector"), regions=core.group_by(rows, "region"),
        currencies=core.group_by(rows, "currency"), price_time=price_time, stale=stale)


NEWS_LEVELS = {"1": "All", "3": "Notable+", "4": "Major+", "5": "Critical"}
SCORE_NAME = {5: "CRITICAL", 4: "MAJOR", 3: "NOTABLE", 2: "MINOR", 1: "BACKGROUND"}
KIND_LABEL = {"filing": "SEC FILING", "press_release": "PRESS RELEASE", "company": "COMPANY SITE",
              "opinion": "OPINION / TIPS", "news": ""}


@app.route("/news")
def news():
    state = user_state()
    if request.args.get("refresh"):
        core.fetch_news(state, force=True)
        return redirect(url_for("news", **{k: v for k, v in request.args.items() if k != "refresh"}))
    items, note = core.fetch_news(state)
    level = request.args.get("min", "1")
    level = level if level in NEWS_LEVELS else "1"
    ticker = request.args.get("ticker", "")
    show_opinion = request.args.get("opinion") == "1"
    sort = request.args.get("sort", "important")
    shown = [i for i in items if i["score"] >= int(level) and (not ticker or i["ticker"] == ticker)
             and (show_opinion or i.get("kind") != "opinion")]
    shown.sort(key=(lambda i: (i["score"], i["published"])) if sort == "important"
               else (lambda i: i["published"]), reverse=True)
    updated = state["cache"].get(core.news_cache_key(state), {}).get("timestamp")
    for item in shown:
        item["when"] = core.show_time(state, item["published"]) if item["published"] else "date unknown"
        item["flags"] = core.matched_keywords(item)
        item["safe_url"] = item["url"] if item.get("url", "").startswith(("http://", "https://")) else ""
    return render_template(
        "news.html", items=shown[:30], total=len(shown), note=note.strip(), levels=NEWS_LEVELS,
        level=level, ticker=ticker, show_opinion=show_opinion, sort=sort,
        tickers=[h["ticker"] for h in state["portfolio"]], score_name=SCORE_NAME,
        kind_label=KIND_LABEL, has_holdings=bool(state["portfolio"]),
        updated=core.show_time(state, updated) if updated else "—")


@app.route("/security")
def security():
    state = user_state()
    held_tickers = [h["ticker"] for h in state["portfolio"]]
    ticker = request.args.get("ticker", "").strip().upper() or (held_tickers[0] if held_tickers else "")
    rng = request.args.get("range", "1Y")
    rng = rng if rng in RANGES else "1Y"
    ctx = {"ticker": ticker, "held_tickers": held_tickers, "ranges": RANGES, "rng": rng,
           "error": None, "info": {}, "charts": None}
    if not ticker:
        return render_template("security.html", **ctx)
    if not core.valid_ticker(ticker):
        ctx["error"] = f"'{ticker}' isn't a valid ticker. Use 1-10 letters or numbers, e.g. AAPL or BP.L."
        return render_template("security.html", **ctx)
    info = core.fetch_company_info(state, ticker)
    data = core.fetch_history(state, ticker, rng)
    recent = core.fetch_history(state, ticker, "1M") or data
    # Yahoo answers an unknown ticker with a record full of empty fields, so check for real content.
    if not data and not any(v is not None for v in info.values()):
        ctx["error"] = f"No data found for '{ticker}'. Check the ticker (UK shares end in .L)."
        return render_template("security.html", **ctx)
    if not data and not (info.get("longName") or info.get("shortName") or info.get("marketCap")):
        ctx["error"] = f"No data found for '{ticker}'. Check the ticker (UK shares end in .L)."
        return render_template("security.html", **ctx)
    quotes = core.get_quotes(state)
    metrics = core.compute_metrics(state, quotes)
    held = next((r for r in metrics["rows"] if r["ticker"] == ticker), None)
    holding = next((h for h in state["portfolio"] if h["ticker"] == ticker), None)
    ccy = (recent or data or {}).get("currency") or info.get("currency") or ""
    symbol = CURRENCY_SYMBOL.get(ccy, "")
    last = recent["close"][-1] if recent else None
    prev = recent["close"][-2] if recent and len(recent["close"]) > 1 else None
    change = (last - prev) if last is not None and prev else None
    scale = 0.01 if (info.get("currency") or "") in ("GBp", "GBX") else 1.0
    low, high = info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh")
    rng_pos = None
    if low and high and last and high > low:
        rng_pos = max(0, min(100, (last - low * scale) / ((high - low) * scale) * 100))
    dividend = info["dividendRate"] * scale / last * 100 if info.get("dividendRate") and last else None
    earnings = core.fetch_earnings(state, ticker)
    charts_html = None
    if data and len(data["close"]) > 1:
        dates, close, volume = data["dates"], data["close"], data["volume"]
        draw = core.drawdown_series(close)
        sym = symbol
        tips = [f"{core.fmt_date(d)} · {sym}{c:,.2f} · {dd:+.1f}% from peak" for d, c, dd in zip(dates, close, draw)]
        charts_html = {
            "price": charts.line_chart(dates, close, tips, height=300, label=f"{ticker} price"),
            "volume": charts.bar_chart(dates, volume, [f"{core.fmt_date(d)} · volume {v:,}" for d, v in zip(dates, volume)],
                                       label=f"{ticker} volume"),
            "drawdown": charts.line_chart(dates, draw, tips, height=120, kind="pct", include_zero=True,
                                          colour="var(--red)", label=f"{ticker} drawdown from peak")}
    ctx.update(info=info, held=held, holding=holding, symbol=symbol, ccy=ccy, last=last,
               change=change, change_pct=(change / prev * 100) if change is not None and prev else None,
               last_date=recent["dates"][-1] if recent else None, earnings=earnings,
               dividend=dividend, low=low * scale if low else None, high=high * scale if high else None,
               rng_pos=rng_pos, charts=charts_html, scale=scale)
    return render_template("security.html", **ctx)


@app.route("/history")
def history():
    state = user_state()
    order = request.args.get("sort", "date")
    txs = core.get_transactions(state)
    txs = sorted(txs, key=(lambda t: (t["ticker"], t["date"], t["id"])) if order == "ticker"
                 else (lambda t: (t["date"], t["id"])), reverse=order != "ticker")
    quotes = core.get_quotes(state)
    metrics = core.compute_metrics(state, quotes)
    return render_template("history.html", txs=txs, closed=core.closed_positions(state),
                           checks=core.cost_basis_check(state), order=order, m=metrics,
                           bought=sum(t["total"] for t in txs if t["type"] == "BUY"),
                           sold=sum(t["total"] for t in txs if t["type"] == "SELL"),
                           today=date.today().isoformat())


# ---------------------------------------------------------------------------------------------
# settings, exports, backup
# ---------------------------------------------------------------------------------------------
@app.route("/settings")
def settings():
    state = user_state()
    zones = ["Europe/London", "UTC", "Europe/Paris", "America/New_York", "America/Chicago",
             "America/Los_Angeles", "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney"]
    return render_template("settings.html", cfg=state["config"], zones=zones)


@app.route("/settings/profile", methods=["POST"])
def settings_profile():
    state = user_state()
    f, cfg = request.form, state["config"]
    cfg["display_name"] = f.get("display_name", "").strip()[:40]
    cfg["account_label"] = (f.get("account_label", "").strip() or "ISA")[:20]
    cfg["timezone"] = f.get("timezone") or cfg["timezone"]
    allowance = number(f, "isa_allowance", cfg["isa_allowance"])
    cfg["isa_allowance"] = allowance if allowance and allowance > 0 else cfg["isa_allowance"]
    cfg["tax_year_end"] = f.get("tax_year_end", cfg["tax_year_end"]).strip()[:20]
    core.save_config(cfg, state["dir"])
    return result(True, "Profile saved.", target="settings")


@app.route("/settings/cash", methods=["POST"])
def settings_cash():
    state = user_state()
    cash, used = number(request.form, "cash"), number(request.form, "isa_used")
    if cash is None or used is None or cash < 0 or used < 0:
        return result(False, "Cash and allowance used must be numbers, zero or more.",
                      target=request.form.get("next", "settings"))
    state["config"]["cash"], state["config"]["isa_used"] = cash, used
    core.save_config(state["config"], state["dir"])
    return result(True, "Cash and allowance saved.", target=request.form.get("next", "settings"))


@app.route("/settings/keys", methods=["POST"])
def settings_keys():
    if DEMO:
        abort(404)
    state = user_state()
    f = request.form
    state["config"]["api_keys"].update({"newsapi": f.get("newsapi", "").strip(),
                                        "finnhub": f.get("finnhub", "").strip()})
    state["config"]["sec_contact"] = f.get("sec_contact", "").strip()
    state["cache"].pop(core.news_cache_key(state), None)
    core.save_config(state["config"], state["dir"])
    core.save_cache(state["cache"])
    return result(True, "Keys saved.", target="settings")


@app.route("/settings/password", methods=["POST"])
def settings_password():
    if DEMO:
        abort(404)
    f = request.form
    if f.get("new_password") != f.get("new_password2"):
        return result(False, "The new passwords don't match.", target="settings")
    return result(*auth.change_password(session["username"], f.get("old_password", ""),
                                        f.get("new_password", "")), target="settings")


@app.route("/settings/reset", methods=["POST"])
def settings_reset():
    state = user_state()
    if request.form.get("confirm") != "RESET":
        return result(False, "Type RESET (in capitals) to confirm.", target="settings")
    core.save_portfolio([], state["dir"])
    core.save_transactions([], state["dir"])
    state["config"]["cash"] = state["config"]["isa_used"] = 0.0
    core.save_config(state["config"], state["dir"])
    return result(True, "Everything reset to zero.", target="settings")


@app.route("/settings/delete", methods=["POST"])
def settings_delete():
    if DEMO:
        abort(404)
    f = request.form
    if f.get("confirm") != "DELETE":
        return result(False, "Type DELETE (in capitals) to confirm.", target="settings")
    ok, message = auth.delete_account(session["username"], f.get("password", ""))
    if ok:
        session.clear()
        flash("Account and data deleted.", "success")
        return redirect(url_for("login"))
    return result(False, message, target="settings")


def download(text, name, mime):
    return Response(text, mimetype=mime,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.route("/export/backup.json")
def export_backup():
    stamp = datetime.now().strftime("%Y%m%d")
    return download(json.dumps(core.make_backup(user_state()), indent=2),
                    f"xavi_backup_{stamp}.json", "application/json")


@app.route("/export/<kind>.csv")
def export_csv(kind):
    state = user_state()
    metrics = core.compute_metrics(state, core.get_quotes(state))
    builders = {"holdings": lambda: core.holdings_csv(metrics),
                "transactions": lambda: core.transactions_csv(state),
                "exposure": lambda: core.exposure_csv(metrics)}
    if kind not in builders:
        abort(404)
    return download("﻿" + builders[kind](), f"xavi_{kind}_{datetime.now():%Y%m%d}.csv", "text/csv")


# ---------------------------------------------------------------------------------------------
# demo account + start
# ---------------------------------------------------------------------------------------------
def ensure_demo_account():
    """Create the 'test' demo account (password: password123) once. Local testing only."""
    if "test" in auth.load_users():
        return
    ok, message = auth.create_account("test", "password123", "Test User")
    if not ok:
        return
    folder = auth.user_dir("test")
    state = {"portfolio": core.load_portfolio(folder), "cache": core.load_cache(),
             "config": core.load_config(folder), "dir": folder}
    state["config"].update(cash=5000.0, isa_used=15000.0)
    core.save_config(state["config"], folder)
    core.add_position(state, "AAPL", 10, 150.0, "demo holding", False, "2025-10-01", skip_price_check=True)
    core.add_position(state, "BP.L", 50, 4.50, "demo holding", False, "2025-10-01", skip_price_check=True)


if __name__ == "__main__":
    print("Starting XAVI web app...")
    print(f"Open http://127.0.0.1:{os.environ.get('XAVI_PORT', '5000')} in your browser")
    if DEMO:
        print("Demo (sandbox) mode: no accounts, every visitor gets a private sample portfolio.")
    elif os.environ.get("XAVI_NO_DEMO") != "1":
        ensure_demo_account()
        print("Demo user: test / password123  (set XAVI_NO_DEMO=1 to skip creating it)")
    app.run(debug=os.environ.get("XAVI_DEBUG") == "1", host="127.0.0.1",
            port=int(os.environ.get("XAVI_PORT", "5000")))
