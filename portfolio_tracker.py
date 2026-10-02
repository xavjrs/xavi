"""ISA Terminal - terminal dashboard.

Run with:  py -3.13 portfolio_tracker.py        (needs: pip install -r requirements.txt)

Log in (or create an account), then use the tabs:
    P) PORT   A) ANLZ   N) NEWS   S) SECT   H) HIST   T) SET
All data and calculations live in isa_core.py; accounts live in auth.py. This file is display
and input only. Information only - not financial advice.
"""

import getpass
import json
import os
import re
import shutil
import sys
import textwrap
import webbrowser
from datetime import date, datetime
from pathlib import Path

import auth
import isa_core as core

# ============================================================================
# STYLE
# ============================================================================
WIDTH = max(80, min(120, shutil.get_terminal_size((120, 30)).columns))
USE_COLOR = bool(sys.stdout.isatty() or os.environ.get("FORCE_COLOR")) \
    and not os.environ.get("NO_COLOR")


def _ansi(code):
    """ANSI escape code, or an empty string when colour is switched off."""
    return code if USE_COLOR else ""


ACCENT, ACCENT_BG = _ansi("\033[38;5;42m"), _ansi("\033[48;5;42m\033[30m")
GREEN, RED, BLUE = _ansi("\033[92m"), _ansi("\033[91m"), _ansi("\033[94m")
YELLOW = _ansi("\033[93m")  # warnings
GRAY, BOLD, UNDERLINE, RESET = _ansi("\033[90m"), _ansi("\033[1m"), _ansi("\033[4m"), _ansi("\033[0m")
ANSI_RE = re.compile(r"\033\[[0-9;]*m")
DISCLAIMER = ("Information only - not financial advice. Prices come from Yahoo Finance (delayed, "
              "may contain errors). Nothing here is a recommendation to buy, sell or hold anything.")

BIG_FONT = {
    "0": ["███", "█ █", "█ █", "█ █", "███"], "1": [" █ ", "██ ", " █ ", " █ ", "███"],
    "2": ["███", "  █", "███", "█  ", "███"], "3": ["███", "  █", "███", "  █", "███"],
    "4": ["█ █", "█ █", "███", "  █", "  █"], "5": ["███", "█  ", "███", "  █", "███"],
    "6": ["███", "█  ", "███", "█ █", "███"], "7": ["███", "  █", "  █", "  █", "  █"],
    "8": ["███", "█ █", "███", "█ █", "███"], "9": ["███", "█ █", "███", "  █", "███"],
}
# The XAVI logo in text form (the A has no crossbar, so a capital lambda stands in for it).
WORDMARK = "X   Λ   V   I"
TABS = [("P", "PORT"), ("A", "ANLZ"), ("N", "NEWS"), ("S", "SECT"), ("H", "HIST"),
        ("T", "SET")]


def colour(text, code):
    """Wrap text in a colour code (no-op when colour is off)."""
    return f"{code}{text}{RESET}" if code else str(text)


def accent(text):
    return colour(text, ACCENT + BOLD)


def gray(text):
    return colour(text, GRAY)


def bold(text):
    return colour(text, BOLD)


def green(text):
    return colour(text, GREEN)


def red(text):
    return colour(text, RED)


def blue(text):
    return colour(text, BLUE)


def vlen(text):
    """Visible length (ignores colour codes)."""
    return len(ANSI_RE.sub("", str(text)))


def pad(text, width, align="left"):
    """Pad to a visible width. Works with coloured text."""
    text = str(text)
    gap = max(0, width - vlen(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        return " " * (gap // 2) + text + " " * (gap - gap // 2)
    return text + " " * gap


def clip(text, width):
    """Shorten text with … if it is longer than width."""
    text = str(text)
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def clear():
    """Clear the screen between pages (only on a real terminal)."""
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def money_c(state, value):
    """Signed money, green for gains and red for losses."""
    text = core.money(state, value, signed=True)
    return green(text) if value > 0 else red(text) if value < 0 else text


def pct_c(value):
    """Signed percentage, green / red."""
    if value is None:
        return "—"
    text = f"{value:+.2f}%"
    return green(text) if value > 0 else red(text) if value < 0 else text


def bar(percent, length=36, colr=None):
    """██████░░░░ bar: filled part coloured, empty part grey."""
    colr = ACCENT if colr is None else colr
    filled = max(0, min(length, round(percent / 100 * length)))
    return colour("█" * filled, colr) + colour("░" * (length - filled), GRAY)


def rule():
    """Thin divider."""
    print(gray("-" * WIDTH))


def section(title, note=""):
    """Two blank lines, then an orange heading with a grey note."""
    print("\n")
    print("  " + accent(title.upper()) + ("  " + gray(note) if note else ""))


def bullets(lines):
    """Orange bullet points with wrapped text."""
    for line in lines:
        wrapped = textwrap.wrap(line, WIDTH - 6) or [""]
        print("  " + accent("•") + " " + wrapped[0])
        for more in wrapped[1:]:
            print("    " + more)


def table(headers, rows, aligns=None, indent=2):
    """Plain-ASCII table with orange headers. Cells may contain colour codes."""
    aligns = aligns or ["left"] * len(headers)
    widths = [max([vlen(h)] + [vlen(r[i]) for r in rows]) for i, h in enumerate(headers)]
    wall = gray("|")
    sep = " " * indent + gray("+" + "+".join("-" * (w + 2) for w in widths) + "+")

    def line(cells, head=False):
        parts = [" " + (accent(pad(c, w, a)) if head else pad(c, w, a)) + " "
                 for c, w, a in zip(cells, widths, aligns)]
        return " " * indent + wall + wall.join(parts) + wall

    print(sep)
    print(line(headers, True))
    print(sep)
    for row in rows:
        print(line(row))
    print(sep)


def cards(items, indent=2):
    """Row(s) of bordered metric cards: (label, value, sub)."""
    per_row = max(1, min(len(items), (WIDTH - indent + 1) // 18))
    width = (WIDTH - indent - (per_row - 1)) // per_row
    inner = width - 4
    edge = gray("+" + "-" * (width - 2) + "+")
    wall = gray("|")
    for start in range(0, len(items), per_row):
        group = items[start:start + per_row]
        print(" " * indent + " ".join(edge for _ in group))
        for position in range(3):
            cells = []
            for item in group:
                text = item[position]
                if position != 1:
                    text = gray(clip(text, inner)) if vlen(text) == len(str(text)) else text
                else:
                    text = bold(text)
                cells.append(wall + " " + pad(text, inner) + " " + wall)
            print(" " * indent + " ".join(cells))
        print(" " * indent + " ".join(edge for _ in group))


def box(lines, colr=None, title="", indent=2):
    """Coloured ASCII box around plain text (long lines wrap)."""
    colr = ACCENT if colr is None else colr
    inner = WIDTH - indent - 4
    body = []
    for text in lines:
        body.extend(textwrap.wrap(text, inner) or [""])
    label = f"- {title} " if title else ""
    print(" " * indent + colour("+" + label + "-" * (inner + 2 - len(label)) + "+", colr))
    for text in body:
        print(" " * indent + colour("|", colr) + " " + pad(text, inner) + " " + colour("|", colr))
    print(" " * indent + colour("+" + "-" * (inner + 2) + "+", colr))


def big_number(text, colr):
    """Digits as 5 lines of block characters."""
    rows = [""] * 5
    for char in text:
        glyph = BIG_FONT.get(char)
        if glyph:
            rows = [row + part + "  " for row, part in zip(rows, glyph)]
    return [colour(row, colr) for row in rows]


def menu_line(options, extras=(("R", "Refresh"), ("Q", "Quit"))):
    """Bottom menu: orange (key) then label."""
    parts = [f"{accent('(' + key + ')')} {label}" for key, label in list(options) + list(extras)]
    lines, current = [], ""
    for part in parts:  # wrap onto several lines instead of running off the screen
        if current and vlen(current) + 3 + vlen(part) > WIDTH - 4:
            lines.append(current)
            current = part
        else:
            current = current + "   " + part if current else part
    lines.append(current)
    print("\n\n  " + "\n  ".join(lines))


def footer():
    """Disclaimer at the bottom of every page."""
    print()
    for line in textwrap.wrap(DISCLAIMER, WIDTH - 4):
        print("  " + gray(line))


# ============================================================================
# INPUT
# ============================================================================
def ask(prompt):
    """input() wrapper. Ctrl+C / Ctrl+D leave the app cleanly."""
    try:
        return input(prompt).strip()
    except EOFError:
        raise KeyboardInterrupt


def secret(prompt):
    """Ask for a password without showing it."""
    try:
        return getpass.getpass(prompt)
    except EOFError:
        raise KeyboardInterrupt


def pause():
    """Wait for Enter."""
    ask(gray("\n  Press Enter to continue..."))


def ask_number(prompt, positive=False, high=None, blank_ok=False):
    """Ask until a valid number is typed. positive=True means above zero. None if blank_ok."""
    while True:
        raw = ask(prompt).replace("£", "").replace("$", "").replace(",", "")
        if raw == "" and blank_ok:
            return None
        try:
            value = float(raw)
        except ValueError:
            print(red(f"  '{raw}' isn't a number. Example: 12.50"))
            continue
        if value < 0 or (positive and value == 0):
            print(red("  Must be " + ("above zero." if positive else "zero or more.")
                  + f" Got: {value:g}"))
        elif high is not None and value > high:
            print(red(f"  Must be at most {high:,.0f}. Got: {value:g}"))
        else:
            return value


def ask_yes_no(prompt, default=None):
    """Y/N question; returns True for yes."""
    hint = "Y/n" if default is True else "y/N" if default is False else "y/n"
    while True:
        answer = ask(f"  {prompt} ({hint}): ").upper()
        if answer in ("Y", "N"):
            return answer == "Y"
        if answer == "" and default is not None:
            return default
        print(red("  Please type Y or N."))


def pick_holding(state, prompt="Choose a holding"):
    """Choose a holding by number or ticker. Returns the holding dict or None."""
    if not state["portfolio"]:
        print("  No holdings yet.")
        return None
    for index, holding in enumerate(state["portfolio"], 1):
        print(f"  {accent('(' + str(index) + ')')} {holding['ticker']:8} {holding.get('name', '')}")
    while True:
        choice = ask(f"  {prompt} (number or ticker, Enter to cancel): ").upper()
        if choice == "":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(state["portfolio"]):
            return state["portfolio"][int(choice) - 1]
        for holding in state["portfolio"]:
            if holding["ticker"] == choice:
                return holding
        print(red("  Not found. Type the list number or the ticker, e.g. NVDA."))


# ============================================================================
# SHARED SCREEN PARTS
# ============================================================================
def header(state, active, metrics=None):
    """Account label, centred XAVI logo, price time, tab strip."""
    cfg = state["config"]
    stamps = [r["timestamp"] for r in metrics["rows"] if r["timestamp"]] if metrics else []
    when = core.show_time(state, min(stamps)) if stamps else core.show_time(state)
    who = cfg.get("display_name") or state.get("user", "")
    left = gray(f"{cfg['account_label']} · {cfg['currency']}")
    mid = accent(WORDMARK)
    right = gray(who)
    gap1 = max(2, (WIDTH - vlen(mid)) // 2 - vlen(left))
    gap2 = max(2, WIDTH - vlen(left) - gap1 - vlen(mid) - vlen(right))
    print()
    print(left + " " * gap1 + mid + " " * gap2 + right)
    rate = state["cache"].get("GBP_USD", {}).get("rate")
    print(gray(f"Prices: {when}" + (f"   ·   GBP/USD {rate:.4f}" if rate else "")))
    print()
    strip, underline = [], []
    for key, name in TABS:
        label = f"{key}) {name}"
        strip.append(accent(label) if name == active else gray(label))
        underline.append(colour("=" * len(label), ACCENT) if name == active else " " * len(label))
    print("  " + "   ".join(strip))
    print("  " + "   ".join(underline))
    rule()


def ago(seconds):
    """'12 min' or '3h' for a number of seconds."""
    minutes = int(seconds // 60)
    return f"{minutes} min" if minutes < 90 else f"{minutes // 60}h"


def data_note(metrics):
    """Banner when Yahoo can't be reached: stale cached prices, or no prices at all."""
    rows = metrics["rows"]
    if not rows:
        return
    if all(r["no_price"] for r in rows):
        print("  " + colour("⚠ Prices unavailable and nothing is cached yet. Press R to retry.",
                            YELLOW))
    elif all(r["stale"] for r in rows):
        stamps = [r["timestamp"] for r in rows if r["timestamp"]]
        print("  " + colour(f"⚠ Prices unavailable. Showing cached data from "
                            f"{ago(core.age_seconds(min(stamps)))} ago. Press R to retry.", YELLOW))
    elif any(r["stale"] for r in rows):
        print("  " + colour("⚠ Some prices could not be refreshed and may be out of date (*). "
                            "Press R to retry.", YELLOW))


# ============================================================================
# PORT
# ============================================================================
def render_port(state, quotes, view):
    """Tab 1: metric cards, latest major news, holdings, allowance."""
    m = core.compute_metrics(state, quotes)
    header(state, "PORT", m)
    data_note(m)
    score, _ = core.diversification_score([r["hold_weight"] for r in m["rows"]])
    rate = state["cache"].get("GBP_USD", {}).get("rate")
    print()
    cards([("TOTAL VALUE", core.money(state, m["total"]), "incl. cash"),
           ("INVESTED", core.money(state, m["invested"]), "cost basis"),
           ("UNREALISED P&L", money_c(state, m["pl"]),
            pct_c(m["pl_pct"]) if m["pl_pct"] is not None else "—"),
           ("REALISED P&L", money_c(state, m["realised_pl"]), "from sales"),
           ("CASH", core.money(state, m["cash"]), f"{m['cash_weight']:.1f}%"),
           ("SPREAD SCORE", f"{score}/100", "see ANLZ")])

    section("Latest major news", "Last few days. Important stories only. See NEWS for all.")
    try:
        items, _ = core.fetch_news(state)
    except Exception:
        items = []
    major = sorted([i for i in items if i["score"] >= 4 and i.get("kind") != "opinion"],
                   key=lambda i: i["published"], reverse=True)[:3]
    for story in major:
        when = core.show_time(state, story["published"])
        room = max(20, WIDTH - 40 - len(story["source"]))
        print(f"  {accent(story['ticker'])}  {gray(when)}  "
              f"{blue(clip(story['title'], room))}  {gray(story['source'])}")
    if not major:
        print("  " + gray("No major news about your holdings in the last few days."))

    section("Holdings", "Costs are in £ per share. Use (3) to edit, sell or delete.")
    if not m["rows"]:
        print("  Your account is empty. Press 2 to add your first position, then use SET (T) to")
        print("  set your cash and yearly allowance.")
    else:
        rows = []
        for r in m["rows"]:
            rows.append([accent(r["ticker"]), clip(r["name"], 20), f"{r['shares']:g}",
                         core.money(state, r["avg_cost"]),
                         "—" if r["no_price"] else core.money(state, r["price"]),
                         core.money(state, r["value"]), money_c(state, r["pl"]),
                         pct_c(r["pl_pct"]),
                         f"{r['weight']:.1f}%", "stale *" if r["stale"] else "ok"])
        table(["Ticker", "Name", "Shares", "Avg cost", "Price", "Value", "P/L", "P/L %",
               "Weight", "Data"], rows,
              ["left", "left", "right", "right", "right", "right", "right", "right", "right",
               "left"])
        print("  " + gray("Weights are % of total value including cash. Source: Yahoo Finance via "
                          "yfinance, cached up to 60 minutes."))

    section("Allowance")
    colr = RED if m["isa_used_pct"] > 100 else ACCENT if m["isa_used_pct"] > 80 else BLUE
    print(f"  {state['config']['account_label']} allowance {core.money(state, m['isa_allowance'])}"
          f"  {bar(min(m['isa_used_pct'], 100), 24, colr)} {m['isa_used_pct']:.1f}% used"
          f"  |  Used {core.money(state, m['isa_used'])}  |  Remaining "
          f"{core.money(state, m['isa_remaining'])}  |  Tax year ends {state['config']['tax_year_end']}")
    if m["isa_used_pct"] > 80:
        print("  " + colour("⚠ More than 80% of this year's allowance is used.", YELLOW))
    footer()


def view_holding_details(state, quotes, view):
    """Full detail for one holding."""
    holding = pick_holding(state)
    if holding is None:
        return
    m = core.compute_metrics(state, quotes)
    r = next(x for x in m["rows"] if x["ticker"] == holding["ticker"])
    clear()
    print()
    print("  " + accent(f"{r['ticker']}  {r['name']}"))
    rule()
    cards([("PRICE", "—" if r["no_price"] else core.money(state, r["price"]), r["source"]),
           ("VALUE", core.money(state, r["value"]), f"{r['weight']:.1f}% of total"),
           ("COST BASIS", core.money(state, r["cost"]),
            f"{r['shares']:g} x {core.money(state, r['avg_cost'])}"),
           ("P/L", money_c(state, r["pl"]), pct_c(r["pl_pct"]))])
    print()
    print(f"  Sector / region : {r['sector']} / {r['region']}")
    if holding.get("bought_on"):
        print(f"  First bought    : {holding['bought_on']}")
    if holding.get("fees_paid"):
        print(f"  Fees paid       : {core.money(state, holding['fees_paid'])}")
    print(f"  Notes           : {holding.get('notes') or '—'}")
    pause()


def add_position_flow(state, quotes, view):
    """Ask how the position was bought, show the cost, then save it."""
    clear()
    print("\n  " + accent("ADD POSITION") + "  " + gray("enter what you actually paid"))
    rule()
    ticker = ask("  Ticker (e.g. AZN.L, BP.L, AAPL): ").upper()
    if not core.valid_ticker(ticker):
        print(red(f"  Ticker '{ticker}' isn't valid. Use 1-10 letters or numbers, e.g. BP.L or AAPL."))
        pause()
        return
    print("\n  What do you know?")
    print(f"  {accent('(1)')} Shares + price per share")
    print(f"  {accent('(2)')} Shares + total paid (£, including fees)")
    print(f"  {accent('(3)')} Amount invested (£) + price per share")
    mode = ""
    while mode not in ("1", "2", "3"):
        mode = ask("  Choose 1-3: ")
    shares = price = total_paid = amount = fx_rate = None
    ccy, fees = "GBP", 0.0
    if mode in ("1", "2"):
        shares = ask_number("  Number of shares: ", positive=True, high=core.MAX_SHARES)
    else:
        amount = ask_number("  Amount invested £ (before fees): ", positive=True)
    if mode == "2":
        total_paid = ask_number("  Total paid £ (everything, incl. fees): ", positive=True)
    else:
        price = ask_number("  Price paid per share: ", positive=True)
        print(f"  Price currency: {accent('(1)')} £ pounds  {accent('(2)')} p pence  "
              f"{accent('(3)')} $ US dollars")
        choice = ""
        while choice not in ("1", "2", "3"):
            choice = ask("  Choose 1-3 [1]: ") or "1"
        ccy = {"1": "GBP", "2": "GBp", "3": "USD"}[choice]
        if ccy == "USD":
            fx_rate = ask_number("  Dollars per £1 when you bought (Enter = today's rate): ",
                                 positive=True, blank_ok=True)
        fees = ask_number("  Fees £ (Enter for none): ", blank_ok=True) or 0.0
    got_shares, total, problem = core.cost_basis_from_inputs(
        state, shares=shares, price=price, price_ccy=ccy, total_paid=total_paid, amount=amount,
        fees=fees, fx_rate=fx_rate)
    if problem:
        print(red("  " + problem))
        pause()
        return
    print(f"\n  This position: {got_shares:,.4g} shares · total cost "
          f"{core.money(state, total)} · average {core.money(state, total / got_shares)} a share")
    while True:
        bought = ask("  Date bought, YYYY-MM-DD (puts it on the returns chart; Enter = today): ")
        try:
            if bought:
                date.fromisoformat(bought)
            break
        except ValueError:
            print(red("  Use the format 2026-03-14."))
    notes = ask("  Notes (optional): ")
    pay_cash = ask_yes_no("Pay from your cash balance (reduces cash)?",
                          default=state["config"]["cash"] >= total)
    if not ask_yes_no("Save this position?", default=True):
        return
    ok, message = core.add_position(state, ticker, got_shares, total / got_shares, notes,
                                    pay_cash, bought_on=bought, fees=fees)
    if not ok and "not found on Yahoo" in message:
        print(red("  " + message))
        if not ask_yes_no("Add it anyway, without live data?", default=False):
            pause()
            return
        ok, message = core.add_position(state, ticker, got_shares, total / got_shares, notes,
                                        pay_cash, bought_on=bought, fees=fees,
                                        skip_price_check=True)
    print((green if ok else red)("  " + message))
    pause()


def sell_flow(state, holding):
    """Ask for the sale details, show the cost basis and profit, then record it (FIFO).
    Returns (ok, message) or None if cancelled."""
    ticker = holding["ticker"]
    while True:
        qty = ask_number(f"  Shares to sell (you own {holding['shares']:g}): ", positive=True)
        if qty <= holding["shares"] + 1e-9:
            break
        print(red(f"  You only own {holding['shares']:g} shares. Got: {qty:g}"))
    rate, _, _ = core.fetch_fx(state["cache"])
    live = core.fetch_price(ticker, state["cache"], rate)["price"]
    hint = f"Enter = live price £{live:,.2f}" if live else "no live price, so enter one"
    price = ask_number(f"  Sale price £ per share ({hint}): ", positive=True, blank_ok=bool(live))
    if price is None:
        price = live
    fees = ask_number("  Fees £ (Enter for none): ", blank_ok=True) or 0.0
    while True:
        sold_on = ask("  Date sold, YYYY-MM-DD (Enter = today): ")
        try:
            if sold_on:
                date.fromisoformat(sold_on)
            break
        except ValueError:
            print(red("  Use the format 2026-03-14."))
    notes = ask("  Notes (optional): ")
    print(f"  {accent('(A)')} Add the proceeds to cash   {accent('(M)')} I'll update cash myself")
    choice = ""
    while choice not in ("A", "M"):
        choice = ask("  Choose A or M [A]: ").upper() or "A"
    remaining, cost_sold = qty, 0.0
    for lot in core.ensure_lots(holding):          # FIFO: oldest purchases are sold first
        take = min(lot["shares"], remaining)
        cost_sold += take * lot["cost"]
        remaining -= take
        if remaining <= 1e-9:
            break
    proceeds = qty * price - fees
    print(f"\n  Proceeds {core.money(state, proceeds)}  ·  cost basis (FIFO) "
          f"{core.money(state, cost_sold)}  ·  realised {money_c(state, proceeds - cost_sold)}")
    if not ask_yes_no("Record this sale?", default=True):
        return None
    return core.sell_position(state, ticker, qty, price, fees, choice == "A", sold_on, notes)


def edit_holding_flow(state, quotes, view):
    """Edit a holding's numbers/labels, sell some shares, or delete it."""
    clear()
    print("\n  " + accent("EDIT / SELL / DELETE"))
    rule()
    holding = pick_holding(state)
    if holding is None:
        return
    ticker = holding["ticker"]
    print(f"\n  {ticker}: {holding['shares']:g} shares @ {core.money(state, holding['avg_cost'])}"
          f"  | sector {holding.get('sector')} | region {holding.get('region')}")
    for key, label in (("1", "Set share count"), ("2", "Set average cost (£/share)"),
                       ("3", "Rename"), ("4", "Set sector"), ("5", "Set region"),
                       ("6", "Edit notes"), ("7", "Sell shares (FIFO)"),
                       ("8", "Delete position (cash unchanged)"), ("9", "Set buy date")):
        print(f"  {accent('(' + key + ')')} {label}")
    choice = ask("  Choice (Enter to cancel): ")
    result = None
    if choice == "1":
        result = core.update_position(state, ticker, shares=ask_number(
            "  New share count: ", positive=True, high=core.MAX_SHARES))
    elif choice == "2":
        result = core.update_position(state, ticker, avg_cost=ask_number(
            "  New average cost £ per share: ", positive=True))
    elif choice == "3":
        result = core.update_position(state, ticker, name=ask("  New name: "))
    elif choice == "4":
        result = core.update_position(state, ticker, sector=ask("  Sector: "))
    elif choice == "5":
        result = core.update_position(state, ticker, region=ask("  Region: "))
    elif choice == "6":
        result = core.update_position(state, ticker, notes=ask("  Notes: "))
    elif choice == "7":
        result = sell_flow(state, holding)
    elif choice == "8":
        if ask_yes_no(f"Delete {ticker} from the tracker?", default=False):
            result = core.remove_position(state, ticker)
    elif choice == "9":
        result = core.set_buy_date(state, ticker, ask_date("  Date bought, YYYY-MM-DD: "))
    if result:
        print((green if result[0] else red)("  " + result[1]))
        pause()


# ============================================================================
# ANLZ
# ============================================================================
def exposure_table(state, m, key, heading):
    """Table of value and weight by sector / region / currency."""
    rows = [[accent(name), core.money(state, g["value"]), f"{g['weight']:.1f}%",
             bar(g["weight"], 20)] for name, g in core.group_by(m["rows"], key).items()]
    table([heading, "Value", "Weight", ""], rows, ["left", "right", "right", "left"])


def render_anlz(state, quotes, view):
    """Tab 2: score, weights, facts, sector + region tables."""
    m = core.compute_metrics(state, quotes)
    header(state, "ANLZ", m)
    data_note(m)
    if not m["rows"]:
        print("\n  Add a holding to see the analysis.")
        footer()
        return
    score, effective = core.diversification_score([r["hold_weight"] for r in m["rows"]])
    colr = GREEN if score >= 70 else ACCENT if score >= 50 else RED
    label = "Well spread" if score >= 70 else "Moderately spread" if score >= 50 else "Concentrated"
    section("Diversification score")
    print()
    for index, line in enumerate(big_number(str(score), colr)):
        print("  " + line + ("  /100   " + gray(label) if index == 2 else ""))
    print(f"\n  {bar(score, 40, colr)} {score}%")
    print("  " + gray(f"Score = (1 - HHI) x 100, where HHI is the sum of squared portfolio weights. "
                      f"Equivalent to {effective:.1f} equal-sized positions (you hold "
                      f"{len(m['rows'])}). Cash is excluded."))

    section("Holding weights", "% of invested holdings")
    for r in sorted(m["rows"], key=lambda x: -x["hold_weight"]):
        print(f"  {r['ticker']:8}{bar(r['hold_weight'], 40, RED if r['hold_weight'] > 40 else ACCENT)}"
              f" {r['hold_weight']:5.1f}%")

    section("Things worth knowing", "Facts about the spread, not suggestions")
    bullets(core.concentration_facts(m))

    section("Sector breakdown", "Holding-level labels; funds are not looked through.")
    exposure_table(state, m, "sector", "Sector")
    section("Geographic exposure", "Holding-level labels; funds are not looked through.")
    exposure_table(state, m, "region", "Region")
    footer()


def view_flags(state, quotes, view):
    """All the spread facts plus the largest-position detail."""
    m = core.compute_metrics(state, quotes)
    clear()
    print("\n  " + accent("THINGS WORTH KNOWING") + "  " + gray("facts, not suggestions"))
    rule()
    print()
    for fact in core.concentration_facts(m):
        box([fact], ACCENT)
    if m["rows"]:
        top = max(m["rows"], key=lambda r: r["hold_weight"])
        print(f"\n  Largest position: {top['ticker']} at {top['hold_weight']:.1f}% of holdings.")
        print(f"  A 20% fall in {top['ticker']} alone would reduce invested value by "
              f"{top['hold_weight'] * 0.2:.1f}%.")
    pause()


def view_breakdown(key, title, heading):
    """Factory: a sub-screen showing one exposure table."""
    def show(state, quotes, view):
        m = core.compute_metrics(state, quotes)
        clear()
        print("\n  " + accent(title))
        rule()
        print()
        exposure_table(state, m, key, heading)
        pause()
    return show


# ============================================================================
# NEWS
# ============================================================================
SCORE_NAME = {5: "CRITICAL", 4: "MAJOR", 3: "NOTABLE", 2: "MINOR", 1: "BACKGROUND"}
KIND_LABEL = {"filing": "SEC FILING", "press_release": "PRESS RELEASE", "company": "COMPANY SITE",
              "opinion": "OPINION / TIPS", "news": ""}


def pill(text, active):
    """Filter button: highlighted when selected."""
    return colour(f" {text} ", ACCENT_BG + BOLD) if active else gray(f" {text} ")


def render_news(state, quotes, view):
    """Tab 3: filters, then stories (importance, date, ticker, headline, flags, sources)."""
    m = core.compute_metrics(state, quotes)
    header(state, "NEWS", m)
    cfg = state["config"]["display"]
    news = view["news"]
    print()
    print("  Importance: " + " ".join([
        pill("All", news["min"] == 1), pill("Notable+ (3-5)", news["min"] == 3),
        pill("Major+ (4-5)", news["min"] == 4), pill("Critical (5)", news["min"] == 5)]))
    print("  Holding:    " + pill(news["ticker"] or "All", True) + "   Sort: " + " ".join([
        pill("Most important", news["sort"] == "important"), pill("Newest", news["sort"] == "new")])
          + "   " + pill("Opinion sites " + ("ON" if news["opinion"] else "OFF"), news["opinion"])
          + "  " + pill("Auto-refresh " + ("ON" if cfg["autorefresh"] else "OFF"), cfg["autorefresh"]))
    key = core.news_cache_key(state)
    entry = state["cache"].get(key)
    force = cfg["autorefresh"] and (not entry or core.age_seconds(entry["timestamp"])
                                    > cfg["refresh_interval_minutes"] * 60)
    print("\n  " + gray("Loading news..."))
    items, note = core.fetch_news(state, force=force or news.pop("force", False))
    if note.strip():
        print("  " + colour(note.strip(), ACCENT))
    items = [i for i in items if i["score"] >= news["min"]
             and (not news["ticker"] or i["ticker"] == news["ticker"])
             and (news["opinion"] or i.get("kind") != "opinion")]
    if news["sort"] == "important":
        items.sort(key=lambda i: (i["score"], i["published"]), reverse=True)
    news["shown"] = items[:15]
    updated = state["cache"].get(key, {}).get("timestamp")
    print("  " + gray(f"Updated {core.show_time(state, updated) if updated else '—'} · {len(items)} "
                      "stories · Sources: Google News (Reuters, BBC, FT, CNBC and others), Yahoo "
                      "Finance" + (", SEC filings" if state["config"].get("sec_contact") else "")))
    print("  " + gray("Only stories that name the company are shown; the same story from several "
                      "outlets is grouped. Importance (1-5) is a keyword-and-source score, not a "
                      "view on what a story means for prices."))
    print()
    if not news["shown"]:
        print("  No stories match this filter.")
    for number, item in enumerate(news["shown"], 1):
        when = core.show_time(state, item["published"]) if item["published"] else "date unknown"
        tone = RED if item["score"] >= 4 else ACCENT if item["score"] == 3 else GRAY
        kind = KIND_LABEL.get(item.get("kind", "news"), "")
        print(f"  {gray('[' + str(number) + ']')} "
              f"{colour('[' + str(item['score']) + '/5 ' + SCORE_NAME[item['score']] + ']', tone + BOLD)}"
              f"  {gray(when)}  {accent(item['ticker'])}" + (f"  {gray(kind)}" if kind else ""))
        print("      " + colour(clip(item["title"], WIDTH - 8), BLUE + UNDERLINE))
        words = core.matched_keywords(item)
        print("      " + gray("Flagged for: " + (", ".join(words) if words else "no key terms")))
        sources = ", ".join([item["source"]] + item.get("also", [])[:4])
        print("      " + gray(("Sources: " if item.get("also") else "Source: ") + clip(sources, WIDTH - 20)))
        print()
    footer()


def news_setter(field, value):
    """Factory: a NEWS action that sets one filter."""
    def action(state, quotes, view):
        view["news"][field] = value
    return action


def news_pick_holding(state, quotes, view):
    """Filter the news by one holding (Enter = all)."""
    holding = pick_holding(state, "Filter to which holding")
    view["news"]["ticker"] = holding["ticker"] if holding else None


def news_toggle_auto(state, quotes, view):
    """Switch news auto-refresh (applies when the page redraws)."""
    cfg = state["config"]["display"]
    cfg["autorefresh"] = not cfg["autorefresh"]
    core.save_config(state["config"], core.state_dir(state))


def news_toggle_opinion(state, quotes, view):
    """Show / hide opinion and stock-tip sites."""
    view["news"]["opinion"] = not view["news"]["opinion"]


def news_toggle_sort(state, quotes, view):
    """Switch between most important and newest first."""
    view["news"]["sort"] = "new" if view["news"]["sort"] == "important" else "important"


def news_open_story(state, quotes, view):
    """Open a story in the default browser."""
    shown = view["news"].get("shown", [])
    number = ask_number("  Story number to open: ", positive=True, blank_ok=True)
    if number and 1 <= int(number) <= len(shown):
        url = shown[int(number) - 1].get("url", "")
        if url.startswith(("http://", "https://")):
            webbrowser.open(url)
        else:
            print(red("  This story has no link."))
            pause()


# ============================================================================
# SECT
# ============================================================================
def current_holding(state, view):
    """The holding shown on SECT (wraps around)."""
    if not state["portfolio"]:
        return None
    view["sect"] %= len(state["portfolio"])
    return state["portfolio"][view["sect"]]


def render_sect(state, quotes, view):
    """Tab 4: company data for one holding."""
    m = core.compute_metrics(state, quotes)
    header(state, "SECT", m)
    holding = current_holding(state, view)
    if holding is None:
        print("\n  Add a holding to see company data.")
        footer()
        return
    r = next(x for x in m["rows"] if x["ticker"] == holding["ticker"])
    print("\n  " + gray("Loading company data..."))
    info = core.fetch_company_info(state, r["ticker"])
    dates = core.fetch_earnings(state, r["ticker"])
    position = f"{view['sect'] + 1} of {len(state['portfolio'])}"
    tickers = " ".join(accent(h["ticker"]) if h is holding else gray(h["ticker"])
                       for h in state["portfolio"])
    print(f"\n  {gray('Holding')}  {accent(position)}   {tickers}")
    print()
    print("  " + accent((info.get("longName") or r["name"]).upper()))
    print("  " + gray(f"{info.get('sector') or r['sector']} / {info.get('industry') or '—'}  ·  "
                      "Source: Yahoo Finance, cached 24h"))
    print()
    arrow = (green("↑ ") if (r["pl_pct"] or 0) >= 0 else red("↓ ")) + pct_c(r["pl_pct"])
    cards([("PRICE", "—" if r["no_price"] else core.money(state, r["price"]), r["source"]),
           ("P/E", f"{info['trailingPE']:.1f}" if info.get("trailingPE") else "—", "trailing"),
           ("FORWARD P/E", f"{info['forwardPE']:.1f}" if info.get("forwardPE") else "—", "expected"),
           ("BETA", f"{info['beta']:.2f}" if info.get("beta") else "—", "market = 1.00"),
           ("YOUR POSITION", core.money(state, r["value"]), arrow)])

    section("Consensus & targets")
    key = info.get("recommendationKey")
    consensus = key.replace("_", " ").upper() if key and key != "none" else "no coverage found"
    line = f"Analyst consensus (Yahoo Finance): {consensus}"
    if info.get("numberOfAnalystOpinions"):
        line += f" from {info['numberOfAnalystOpinions']} analysts"
    target = info.get("targetMeanPrice")
    if target:
        ccy = info.get("currency") or ""
        line += f"; mean target {target:,.2f} {ccy}"
        gbp_target = core.to_gbp(target, ccy, state["cache"].get("GBP_USD", {}).get("rate"))
        if gbp_target and ccy not in ("GBP", "GBp"):
            line += f" (~{core.money(state, gbp_target)})"
    bullets([line, "Your notes: " + (holding.get("notes") or "—")])

    section("Catalysts")
    print("  Next earnings:  " + (", ".join(
        f"{core.fmt_date(d)} (in {core.days_until(d)} days)" for d in dates)
        if dates else "none listed (funds and trusts don't report earnings)"))
    try:
        news, _ = core.fetch_news(state)
    except Exception:
        news = []
    recent = [i for i in news if i["ticker"] == r["ticker"] and i["score"] >= 3
              and i.get("kind") != "opinion"][:3]
    if recent:
        print("  Recent events:")
        for item in recent:
            print(f"    {gray(core.show_time(state, item['published']))}  "
                  f"{clip(item['title'], WIDTH - 36)}")
    footer()


def sect_step(delta):
    """Factory: move to the next / previous holding."""
    def action(state, quotes, view):
        view["sect"] += delta
    return action


def sect_analyst_details(state, quotes, view):
    """Extra analyst data (Finnhub too if a key is set)."""
    holding = current_holding(state, view)
    if holding is None:
        return
    clear()
    print("\n  " + accent(f"ANALYST DETAILS - {holding['ticker']}"))
    rule()
    info = core.fetch_company_info(state, holding["ticker"])
    for label, value in (("Consensus", info.get("recommendationKey")),
                         ("Analysts", info.get("numberOfAnalystOpinions")),
                         ("Mean target", info.get("targetMeanPrice")),
                         ("52-week low", info.get("fiftyTwoWeekLow")),
                         ("52-week high", info.get("fiftyTwoWeekHigh")),
                         ("Dividend yield", info.get("dividendYield"))):
        print(f"  {label:16}{value if value not in (None, '') else '—'}")
    data = core.fetch_finnhub_recommendation(state, holding["ticker"], force=True)
    if data:
        print("\n  Finnhub ratings, " + str(data.get("period")))
        for label, key in (("Strong buy", "strongBuy"), ("Buy", "buy"), ("Hold", "hold"),
                           ("Sell", "sell"), ("Strong sell", "strongSell")):
            print(f"  {label:16}{data.get(key, '—')}")
    pause()


def sect_edit_notes(state, quotes, view):
    """Edit the notes for the holding on screen."""
    holding = current_holding(state, view)
    if holding:
        ok, message = core.update_position(state, holding["ticker"], notes=ask("  Your notes: "))
        print((green if ok else red)("  " + message))
        pause()


def sect_open_yahoo(state, quotes, view):
    """Open the holding's Yahoo Finance page in the browser."""
    holding = current_holding(state, view)
    if holding:
        webbrowser.open(f"https://finance.yahoo.com/quote/{holding['ticker']}")


# ============================================================================
# EXPORT / BACKUP FILES
# ============================================================================
def downloads_dir():
    """The Downloads folder (created if missing)."""
    folder = Path.home() / "Downloads"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_download(stem, extension, text):
    """Write text to ~/Downloads/<stem>_YYYYMMDD<extension>, never overwriting a file."""
    base = f"{stem}_{datetime.now():%Y%m%d}"
    target = downloads_dir() / f"{base}{extension}"
    counter = 2
    while target.exists():
        target = downloads_dir() / f"{base}_{counter}{extension}"
        counter += 1
    target.write_text(text, encoding="utf-8-sig" if extension == ".csv" else "utf-8")
    return target


def export_action(stem, builder):
    """Factory: an action that saves a CSV and says where it went."""
    def action(state, quotes, view):
        try:
            path = save_download(stem, ".csv", builder(state, core.compute_metrics(state, quotes)))
            print(green(f"  Saved {path}"))
        except OSError as err:
            print(red(f"  Couldn't save the file: {err}"))
        pause()
    return action


# ============================================================================
# DATED TRADES AND THE RETURNS CHART
# ============================================================================
def ask_date(prompt, required=True, earliest=None):
    """Ask for a past date as YYYY-MM-DD until it is valid. Returns '' if allowed and blank."""
    while True:
        text = ask(prompt)
        if text == "" and not required:
            return ""
        when = core.parse_date(text)
        if when is None:
            print(red(f"  '{text}' isn't a date. Use the format 2026-03-14."))
        elif when > date.today():
            print(red(f"  {text} is in the future. Got: {text}"))
        elif earliest and text < earliest:
            print(red(f"  That is before the buy date ({earliest}). Got: {text}"))
        else:
            return text


def add_closed_flow(state, quotes, view):
    """Record a trade that is already finished, so its result appears in your returns."""
    clear()
    print("\n  " + accent("ADD CLOSED TRADE") + "  " + gray("bought and sold in the past; holdings and "
                                                           "cash are not changed"))
    rule()
    ticker = ask("  Ticker (e.g. AZN.L, BP.L, AAPL): ").upper()
    if not core.valid_ticker(ticker):
        print(red(f"  Ticker '{ticker}' isn't valid. Use 1-10 letters or numbers, e.g. BP.L or AAPL."))
        pause()
        return
    shares = ask_number("  Number of shares: ", positive=True, high=core.MAX_SHARES)
    buy_date = ask_date("  Date bought, YYYY-MM-DD: ")
    sell_date = ask_date("  Date sold, YYYY-MM-DD: ", earliest=buy_date)
    print(f"  Price currency: {accent('(1)')} £ pounds  {accent('(2)')} p pence  "
          f"{accent('(3)')} $ US dollars")
    choice = ""
    while choice not in ("1", "2", "3"):
        choice = ask("  Choose 1-3 [1]: ") or "1"
    ccy = {"1": "GBP", "2": "GBp", "3": "USD"}[choice]
    prices = []
    for label in ("bought", "sold"):
        price = ask_number(f"  Price per share when {label}: ", positive=True)
        rate = None
        if ccy == "USD":
            rate = ask_number(f"  Dollars per £1 when {label} (Enter = today's rate): ",
                              positive=True, blank_ok=True)
        gbp, problem = core.price_to_gbp(state, price, ccy, rate)
        if problem:
            print(red("  " + problem))
            pause()
            return
        prices.append(gbp)
    buy_fees = ask_number("  Fees when bought £ (Enter for none): ", blank_ok=True) or 0.0
    sell_fees = ask_number("  Fees when sold £ (Enter for none): ", blank_ok=True) or 0.0
    notes = ask("  Notes (optional): ")
    cost = shares * prices[0] + buy_fees
    proceeds = shares * prices[1] - sell_fees
    held = (date.fromisoformat(sell_date) - date.fromisoformat(buy_date)).days
    print(f"\n  Cost {core.money(state, cost)}  ·  proceeds {core.money(state, proceeds)}  ·  "
          f"profit {money_c(state, proceeds - cost)} ({pct_c((proceeds - cost) / cost * 100)})  ·  "
          f"held {held} days")
    if not ask_yes_no("Record this trade?", default=True):
        return
    ok, message = core.add_past_trade(state, ticker, shares, prices[0], buy_date, prices[1],
                                      sell_date, buy_fees, sell_fees, notes)
    print((green if ok else red)("  " + message))
    pause()


def area_chart(values, width=76, rows=7):
    """Text area chart (block characters). Returns (lines, low, high). Zero is always in range."""
    if len(values) > width:
        values = [values[round(i * (len(values) - 1) / (width - 1))] for i in range(width)]
    low, high = min(min(values), 0), max(max(values), 0)
    span = (high - low) or 1
    blocks = " ▁▂▃▄▅▆▇█"
    lines = []
    for row in range(rows):
        cells = ""
        for value in values:
            level = (value - low) / span * rows * 8
            cells += blocks[int(max(0, min(8, level - (rows - 1 - row) * 8)))]
        lines.append(cells)
    return lines, low, high


def hist_chart_section(state, view):
    """Returns over time, built from the dated buys and sells."""
    kind, rng = view.get("hist_chart", "profit"), view.get("hist_range", "1Y")
    titles = {"profit": "Total profit / loss (£)", "value": "Value of holdings (£)",
              "return": "Total return (%)"}
    section("Returns over time", f"{titles[kind]} · range {rng} · from your dated buys and sells")
    try:
        timeline = core.portfolio_timeline(state, rng)
    except Exception as err:
        core.log_event("ERROR", "timeline", str(err))
        timeline = None
    if not timeline:
        print("  No chart yet: add a position with a buy date (or a closed trade), and make "
              "sure prices can load.")
        return
    key = {"profit": "profit", "value": "value", "return": "return_pct"}[kind]
    values = timeline[key]
    lines, low, high = area_chart(values)
    last = values[-1]
    colr = GREEN if (kind == "value" or last >= 0) else RED
    unit = (lambda v: f"{v:+.1f}%") if kind == "return" else (lambda v: core.money(state, v))
    print(f"  {gray(unit(high)):>12}")
    for line in lines:
        print("  " + colour(line, colr))
    print(f"  {gray(unit(low)):>12}")
    print("  " + gray(f"{timeline['dates'][0]}{' ' * max(1, 76 - 20)}{timeline['dates'][-1]}"))
    print(f"  Now: value {core.money(state, timeline['value'][-1])}  ·  net invested "
          f"{core.money(state, timeline['invested'][-1])}  ·  profit "
          f"{money_c(state, timeline['profit'][-1])}  ·  return {pct_c(timeline['return_pct'][-1])}")
    print("  " + gray("Return = total profit (realised + unrealised) / everything bought so far. "
                      "Shares with no buy date count as held from the start."))
    if timeline["skipped"]:
        print("  " + colour("Left out (no price history): " + ", ".join(timeline["skipped"]), YELLOW))


def hist_chart_kind(state, quotes, view):
    """Cycle the chart between profit, value and return %."""
    order = ["profit", "value", "return"]
    view["hist_chart"] = order[(order.index(view.get("hist_chart", "profit")) + 1) % 3]


def hist_chart_range(state, quotes, view):
    """Cycle the chart range."""
    order = list(core.HISTORY_RANGES)
    view["hist_range"] = order[(order.index(view.get("hist_range", "1Y")) + 1) % len(order)]


def hist_redate(state, quotes, view):
    """Change a transaction's date (for example a purchase that defaulted to today)."""
    number = ask_number("  Transaction # to change (see the # column): ", positive=True,
                        blank_ok=True)
    if number is None:
        return
    ok, message = core.redate_transaction(state, int(number),
                                          ask_date("  Correct date, YYYY-MM-DD: "))
    print((green if ok else red)("  " + message))
    pause()


# ============================================================================
# HIST (transaction history)
# ============================================================================
def render_hist(state, quotes, view):
    """Tab: every buy and sell, realised profit, and a check of cost basis against the ledger."""
    m = core.compute_metrics(state, quotes)
    header(state, "HIST", m)
    transactions = core.get_transactions(state)
    bought = sum(t["total"] for t in transactions if t["type"] == "BUY")
    sold = sum(t["total"] for t in transactions if t["type"] == "SELL")
    print()
    cards([("REALISED P&L", money_c(state, m["realised_pl"]), "from sales"),
           ("UNREALISED P&L", money_c(state, m["pl"]), "on holdings"),
           ("TOTAL BOUGHT", core.money(state, bought), "incl. fees"),
           ("TOTAL SOLD", core.money(state, sold), "after fees"),
           ("TRANSACTIONS", str(len(transactions)), f"{sum(t['type'] == 'SELL' for t in transactions)} sold")])
    hist_chart_section(state, view)
    ticker = view.get("hist_ticker")
    shown = [t for t in transactions if not ticker or t["ticker"] == ticker]
    if view.get("hist_sort") == "ticker":
        shown.sort(key=lambda t: (t["ticker"], t["date"], t["id"]))
    else:
        shown.sort(key=lambda t: (t["date"], t["id"]), reverse=True)
    section("Transactions", f"sorted by {view.get('hist_sort', 'date')} · "
            f"{'all holdings' if not ticker else ticker}")
    if not shown:
        print("  No transactions yet. Adding a position records a BUY; selling records a SELL.")
    else:
        rows = []
        for t in shown[:40]:
            kind = green("BUY ") if t["type"] == "BUY" else red("SELL")
            rows.append([str(t["id"]), t["date"], accent(t["ticker"]), kind, f"{t['shares']:g}",
                         core.money(state, t["price"]), core.money(state, t["fees"]),
                         core.money(state, t["total"]),
                         money_c(state, t["realised_pl"]) if t["type"] == "SELL" else "",
                         clip(t.get("notes", ""), 24)])
        table(["#", "Date", "Ticker", "Type", "Shares", "Price", "Fees", "Total", "Realised",
               "Notes"], rows, ["right", "left", "left", "left", "right", "right", "right", "right",
                                "right", "left"])
        if len(shown) > 40:
            print("  " + gray(f"Showing the latest 40 of {len(shown)}. Export CSV (E) for all."))
        print("  " + gray("Price is per share before fees. Total = shares x price + fees for a buy, "
                          "minus fees for a sale."))

    closed = core.closed_positions(state)
    if ticker:
        closed = [c for c in closed if c["ticker"] == ticker]
    section("Closed positions", "every sale: when it was opened and closed, and the return")
    if not closed:
        print("  Nothing sold yet. Selling a holding, or adding a closed trade, shows its return here.")
    else:
        rows = [[accent(c["ticker"]), c["opened"] or "—", c["closed"],
                 "—" if c["days"] is None else str(c["days"]), f"{c['shares']:g}",
                 core.money(state, c["cost"]), core.money(state, c["proceeds"]),
                 money_c(state, c["profit"]), pct_c(c["return_pct"])] for c in closed[:20]]
        table(["Ticker", "Opened", "Closed", "Days", "Shares", "Cost", "Proceeds", "Profit",
               "Return"], rows, ["left", "left", "left", "right", "right", "right", "right", "right",
                                 "right"])

    section("Cost basis check", "what the ledger says you paid vs the cost on each holding")
    if not m["rows"]:
        print("  No holdings.")
    else:
        rows = []
        for check in core.cost_basis_check(state):
            if not check["has_ledger"]:
                status = gray("no ledger")
            elif abs(check["difference"]) < 0.01:
                status = green("matches")
            else:
                status = colour("differs", YELLOW)
            rows.append([accent(check["ticker"]), core.money(state, check["bought"]),
                         core.money(state, check["sold_cost"]),
                         core.money(state, check["ledger_remaining"]),
                         core.money(state, check["holding_cost"]),
                         core.money(state, 0.0 if abs(check["difference"]) < 0.005 else check["difference"]),
                         status])
        table(["Ticker", "Bought", "Cost of sold", "Ledger left", "Holding cost", "Difference",
               "Status"], rows, ["left", "right", "right", "right", "right", "right", "left"])
        print("  " + gray("'differs' means a holding was edited by hand or entered before the ledger "
                          "existed. Sales use FIFO (oldest shares first)."))
    footer()


def hist_sort(kind):
    """Factory: choose the sort order."""
    def action(state, quotes, view):
        view["hist_sort"] = kind
    return action


def hist_filter(state, quotes, view):
    """Show one holding's transactions (Enter = all)."""
    holding = pick_holding(state, "Filter to which holding")
    view["hist_ticker"] = holding["ticker"] if holding else None


# ============================================================================
# SET (settings + account)
# ============================================================================
def render_set(state, quotes, view):
    """Tab 5: your settings and account."""
    m = core.compute_metrics(state, quotes)
    header(state, "SET", m)
    cfg = state["config"]
    keys = cfg["api_keys"]
    section("Profile & account", "Make it yours - press the number to change a setting")
    table(["#", "Setting", "Value"], [
        [accent("1"), "Display name", cfg["display_name"] or "—"],
        [accent("2"), "Account label", cfg["account_label"]],
        [accent("3"), "Yearly allowance", core.money(state, cfg["isa_allowance"])],
        [accent("4"), "Tax year ends", cfg["tax_year_end"]],
        [accent("5"), "Cash in account", core.money(state, cfg["cash"])],
        [accent("6"), "Allowance used", core.money(state, cfg["isa_used"])],
        [accent("7"), "Display currency", cfg["currency"]],
        [accent("8"), "Timezone", cfg["timezone"]],
        [accent("9"), "API keys / SEC email",
         f"NewsAPI {'set' if keys['newsapi'] else '-'} | Finnhub {'set' if keys['finnhub'] else '-'}"
         f" | SEC {'set' if cfg.get('sec_contact') else '-'}"]], ["left", "left", "left"])
    print()
    print("  " + gray("(B) backup   (R) restore   (C) change password   (Z) reset portfolio "
                  "to zero   (D) delete account"))
    footer()


def save_cfg(state):
    """Write config.json for this person."""
    core.save_config(state["config"], core.state_dir(state))


def set_backup(state, quotes, view):
    """Save everything (holdings, transactions, settings; never API keys) to Downloads."""
    try:
        path = save_download("xavi_backup", ".json", json.dumps(core.make_backup(state), indent=2))
        print(green(f"  Backup saved: {path}"))
        print(gray("  API keys are not included. Keep the file somewhere safe: it isn't encrypted."))
    except OSError as err:
        print(red(f"  Couldn't save the backup: {err}"))
    pause()


def set_restore(state, quotes, view):
    """Restore from a backup file. Checks the whole file first and keeps a safety copy."""
    raw = ask("  Path to the backup file (.json): ").strip().strip('"').strip("'")
    if not raw:
        return
    path = Path(raw).expanduser()
    if not path.is_file():
        print(red(f"  File not found: {path}"))
        pause()
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as err:
        print(red(f"  Couldn't read that file as JSON: {err}"))
        pause()
        return
    if isinstance(data, dict) and data.get("app") == "xavi":
        print(f"  Backup made {data.get('created', 'unknown')} · "
              f"{len(data.get('portfolio') or [])} holdings · "
              f"{len(data.get('transactions') or [])} transactions")
    print(colour("  This REPLACES your current holdings, transactions and settings "
                 "(your API keys are kept).", YELLOW))
    if ask("  Type YES to restore: ").upper() != "YES":
        print("  Cancelled.")
        pause()
        return
    try:
        safety = save_download("xavi_before_restore", ".json",
                               json.dumps(core.make_backup(state), indent=2))
        print(gray(f"  Safety copy of your current data: {safety}"))
    except OSError:
        pass
    ok, message = core.restore_backup(state, data)
    print((green if ok else red)("  " + message))
    pause()


def set_text(field, prompt, limit=40):
    """Factory: edit a text setting."""
    def action(state, quotes, view):
        value = ask(f"  {prompt}: ")[:limit]
        if value:
            state["config"][field] = value
            save_cfg(state)
    return action


def set_number(field, prompt, positive=False):
    """Factory: edit a number setting."""
    def action(state, quotes, view):
        value = ask_number(f"  {prompt}: ", positive=positive, blank_ok=True)
        if value is not None:
            state["config"][field] = value
            save_cfg(state)
    return action


def set_currency(state, quotes, view):
    """GBP or USD display."""
    choice = ask("  Display currency (GBP/USD): ").upper()
    if choice in ("GBP", "USD"):
        state["config"]["currency"] = choice
        save_cfg(state)
    else:
        print(red("  Expected GBP or USD."))
        pause()


def set_timezone(state, quotes, view):
    """Pick a timezone."""
    zones = ["Europe/London", "UTC", "Europe/Paris", "America/New_York", "America/Chicago",
             "America/Los_Angeles", "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney"]
    for index, zone in enumerate(zones, 1):
        print(f"  {accent('(' + str(index) + ')')} {zone}")
    choice = ask("  Choice: ")
    if choice.isdigit() and 1 <= int(choice) <= len(zones):
        state["config"]["timezone"] = zones[int(choice) - 1]
        save_cfg(state)


def set_keys(state, quotes, view):
    """NewsAPI / Finnhub keys and the SEC contact email."""
    print(f"  {accent('(1)')} NewsAPI key  {accent('(2)')} Finnhub key  "
          f"{accent('(3)')} SEC contact email")
    choice = ask("  Choice: ")
    if choice in ("1", "2"):
        name = "newsapi" if choice == "1" else "finnhub"
        state["config"]["api_keys"][name] = secret(f"  {name} key (blank removes): ").strip()
    elif choice == "3":
        print(gray("  The SEC requires this to be sent with requests. It goes to sec.gov only."))
        state["config"]["sec_contact"] = ask("  Email (blank removes): ")
    else:
        return
    state["cache"].pop(core.news_cache_key(state), None)
    save_cfg(state)


def set_password(state, quotes, view):
    """Change password (needs the current one)."""
    old = secret("  Current password: ")
    new = secret("  New password: ")
    if new != secret("  Repeat new password: "):
        print(red("  The new passwords don't match."))
    else:
        ok, message = auth.change_password(state["user"], old, new)
        print((green if ok else red)("  " + message))
    pause()


def set_reset(state, quotes, view):
    """Remove every holding and set cash + allowance used to zero."""
    if ask_yes_no("Remove ALL holdings and set cash and allowance used to £0?", default=False):
        state["portfolio"] = []
        state["config"]["cash"] = state["config"]["isa_used"] = 0.0
        core.save_portfolio([], core.state_dir(state))
        save_cfg(state)
        print(green("  Reset."))
        pause()


def set_delete(state, quotes, view):
    """Delete this account and all its data (needs the password)."""
    print(red("  This permanently deletes your account and all data. It can't be recovered."))
    password = secret("  Password: ")
    if ask("  Type DELETE to confirm: ") != "DELETE":
        print("  Cancelled.")
        pause()
        return
    ok, message = auth.delete_account(state["user"], password)
    print((green if ok else red)("  " + message))
    pause()
    if ok:
        view["logout"] = True


# ============================================================================
# TAB TABLE + MAIN LOOP
# ============================================================================
def tab_definitions():
    """code -> (render function, {key: (label, action)})."""
    return {
        "PORT": (render_port, {"1": ("View holding details", view_holding_details),
                               "2": ("Add new position", add_position_flow),
                               "3": ("Edit / sell / delete", edit_holding_flow),
                               "4": ("Add closed trade", add_closed_flow),
                               "E": ("Export CSV", export_action("xavi_holdings", lambda s, m: core.holdings_csv(m)))}),
        "HIST": (render_hist, {"1": ("Sort by date", hist_sort("date")),
                               "2": ("Sort by ticker", hist_sort("ticker")),
                               "3": ("Filter by holding", hist_filter),
                               "4": ("Chart: profit / value / return", hist_chart_kind),
                               "5": ("Chart range", hist_chart_range),
                               "6": ("Change a date", hist_redate),
                               "7": ("Add closed trade", add_closed_flow),
                               "E": ("Export CSV", export_action("xavi_transactions", lambda s, m: core.transactions_csv(s)))}),
        "ANLZ": (render_anlz, {
            "1": ("View flags", view_flags),
            "2": ("Sector details", view_breakdown("sector", "SECTOR DETAILS", "Sector")),
            "3": ("Geographic", view_breakdown("region", "GEOGRAPHIC EXPOSURE", "Region")),
            "4": ("Currency", view_breakdown("currency", "CURRENCY EXPOSURE", "Currency")),
            "E": ("Export CSV", export_action("xavi_exposure", lambda s, m: core.exposure_csv(m)))}),
        "NEWS": (render_news, {"1": ("Critical only", news_setter("min", 5)),
                               "2": ("Major+", news_setter("min", 4)),
                               "3": ("All", news_setter("min", 1)),
                               "4": ("Filter by holding", news_pick_holding),
                               "5": ("Auto-refresh", news_toggle_auto),
                               "6": ("Sort", news_toggle_sort),
                               "7": ("Opinion sites", news_toggle_opinion),
                               "O": ("Open story", news_open_story)}),
        "SECT": (render_sect, {"1": ("Next holding", sect_step(1)),
                               "2": ("Previous holding", sect_step(-1)),
                               "3": ("Analyst details", sect_analyst_details),
                               "4": ("Your notes", sect_edit_notes),
                               "O": ("Open on Yahoo Finance", sect_open_yahoo),
                               "E": ("Export CSV", export_action("xavi_research", lambda s, m: core.research_csv(s, m)))}),
        "SET": (render_set, {
            "1": ("Name", set_text("display_name", "Display name")),
            "2": ("Label", set_text("account_label", "Account label (e.g. S&S ISA)", 20)),
            "3": ("Allowance", set_number("isa_allowance", "Yearly allowance £", True)),
            "4": ("Tax year end", set_text("tax_year_end", "Tax year ends (e.g. 5 Apr 2027)", 20)),
            "5": ("Cash", set_number("cash", "Cash in account £")),
            "6": ("Used", set_number("isa_used", "Allowance used £")),
            "7": ("Currency", set_currency), "8": ("Timezone", set_timezone),
            "9": ("Keys", set_keys), "C": ("Password", set_password),
            "Z": ("Reset", set_reset), "D": ("Delete account", set_delete),
            "B": ("Backup", set_backup), "R": ("Restore", set_restore)}),
    }


def login_screen():
    """Log in or create an account. Returns the username, or None to quit."""
    while True:
        clear()
        print()
        print("\n  " + accent(WORDMARK) + "\n")
        print(f"  {accent('(L)')} Log in   {accent('(C)')} Create account   {accent('(Q)')} Quit")
        choice = ask("\n  Select: ").upper()
        if choice == "Q":
            return None
        if choice == "L":
            user = ask("  Username: ")
            ok, message = auth.verify_login(user, secret("  Password: "))
            if ok:
                return auth.clean_username(user)
            print(red("  " + message))
            pause()
        elif choice == "C":
            name = ask("  Display name (optional): ")
            user = ask("  Choose a username (3-20 letters/numbers . _ -): ")
            password = secret(f"  Choose a password (at least {auth.MIN_PASSWORD} characters): ")
            if password != secret("  Repeat password: "):
                print(red("  The passwords don't match."))
            else:
                print("\n  " + colour(auth.NO_RECOVERY, ACCENT))
                if ask("  Type YES to confirm you understand: ").upper() != "YES":
                    print("  Cancelled.")
                else:
                    ok, message = auth.create_account(user, password, name)
                    print((green if ok else red)("  " + message))
                    if ok:
                        return auth.clean_username(user)
            pause()


def run(state):
    """Main loop for a logged-in person. Returns 'quit' or 'logout'."""
    tabs = tab_definitions()
    view = {"tab": "PORT", "sect": 0, "logout": False, "hist_sort": "date", "hist_ticker": None, "hist_chart": "profit", "hist_range": "1Y",
            "news": {"min": 1, "ticker": None, "sort": "important", "opinion": False}}
    letters = {key: name for key, name in TABS}
    force = False
    while not view["logout"]:
        clear()
        quotes = core.get_quotes(state, force=force)
        if force:
            view["news"]["force"] = True
        force = False
        render, actions = tabs[view["tab"]]
        render(state, quotes, view)
        extras = (("L", "Log out"), ("Q", "Quit")) if "R" in actions else \
            (("R", "Refresh"), ("L", "Log out"), ("Q", "Quit"))
        menu_line([(k, label) for k, (label, _) in actions.items()], extras=extras)
        print("  " + gray("Switch tab: " + "  ".join(f"{k}={n}" for k, n in TABS)))
        choice = ask(accent("  Select: ")).upper()
        if choice == "Q":
            return "quit"
        if choice == "L":
            return "logout"
        if choice in actions:
            actions[choice][1](state, quotes, view)
        elif choice == "R":
            force = True
        elif choice in letters:
            view["tab"] = letters[choice]
        elif choice == "B":
            view["tab"] = "PORT"
    return "logout"


def main():
    """Log in, then run the dashboard. Ctrl+C leaves cleanly."""
    os.system("")  # enables ANSI colours in Windows terminals
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    core.notify = lambda message: print(colour("  " + message, YELLOW))
    try:
        while True:
            user = login_screen()
            if user is None:
                break
            folder = auth.user_dir(user)
            state = {"portfolio": core.load_portfolio(folder), "cache": core.load_cache(),
                     "transactions": core.load_transactions(folder),
                     "config": core.load_config(folder), "dir": folder, "user": user}
            if run(state) == "quit":
                break
    except KeyboardInterrupt:
        pass
    print("\n  Goodbye!")


if __name__ == "__main__":
    main()
