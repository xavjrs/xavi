"""ISA Terminal core: data, calculations and storage. No input(), no styling.

Used by both front ends:
    portfolio_tracker.py   terminal app
    app.py                 Streamlit web app
Everything works on a `state` dict: {"portfolio": [...], "cache": {...}, "config": {...}}.
"""

import json
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import requests
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
except ImportError:  # very old Python
    ZoneInfo = None


def notify(message):
    """User-facing message hook. Front ends replace this (the terminal prints in colour, the
    web app shows a banner). The default just prints."""
    print(message)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


PORTFOLIO_FILE = os.path.join(BASE_DIR, "portfolio.json")


CACHE_FILE = os.path.join(BASE_DIR, "data_cache.json")


CONFIG_FILE = os.path.join(BASE_DIR, "config.json")


LOG_FILE = os.path.join(BASE_DIR, "log.txt")


PRICE_CACHE_SEC = 3600          # prices are fresh for 60 minutes


NEWS_CACHE_SEC = 900            # news re-fetched after 15 min; kept 24h as fallback


NEWS_MAX_AGE_SEC = 86400


INFO_CACHE_SEC = 86400          # company research: 1 day


ANALYST_CACHE_SEC = 604800      # analyst data: 1 week


FETCH_RETRIES = 3


RETRY_BACKOFF_SEC = 3


ISA_ALLOWANCE = 20000.0


MAX_SHARES = 10000


DEFAULT_PORTFOLIO = []  # new accounts start empty


DEFAULT_CONFIG = {
    "display_name": "",
    "account_label": "S&S ISA",     # shown in the header, e.g. "S&S ISA", "SIPP", "GIA"
    "timezone": "Europe/London",
    "currency": "GBP",
    "cash": 0.0,
    "isa_allowance": 20000.0,       # yearly allowance for this account (GBP)
    "isa_used": 0.0,                # paid in this tax year so far
    "tax_year_end": "5 Apr 2027",
    "api_keys": {"newsapi": "", "finnhub": ""},
    "sec_contact": "",              # optional: email sent to the SEC (required by their rules)
    "display": {"autorefresh": False, "refresh_interval_minutes": 5},
    "last_updated": "",
    "app_version": "1.1",
}


def now_utc():
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def iso_now():
    """Current UTC time as an ISO 8601 string (the format we store)."""
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text):
    """Turn a stored ISO string back into a datetime (None if it can't be read)."""
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def age_seconds(text):
    """Seconds since a stored ISO timestamp (very large if missing)."""
    when = parse_iso(text)
    return (now_utc() - when).total_seconds() if when else 10 ** 9


def show_time(state, text=None):
    """Format a stored ISO time for display in the user's timezone."""
    when = parse_iso(text) if text else now_utc()
    if when is None:
        return "unknown"
    try:
        if ZoneInfo:
            when = when.astimezone(ZoneInfo(state["config"]["timezone"]))
    except Exception:
        pass  # missing tz database: fall back to UTC
    return f"{when.day} {when:%b %Y, %H:%M} {when.tzname()}"


def log_event(severity, component, message):
    """Append one line to log.txt. Never raises - logging must not crash the app."""
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {severity}: {component}: {message}\n")
    except OSError:
        pass


def recent_error_count():
    """How many ERROR lines were logged in the last hour."""
    count = 0
    cutoff = datetime.now() - timedelta(hours=1)
    try:
        with open(LOG_FILE, encoding="utf-8") as handle:
            for line in handle:
                if " ERROR:" in line:
                    try:
                        if datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S") > cutoff:
                            count += 1
                    except ValueError:
                        continue
    except OSError:
        pass
    return count


def money(state, gbp_amount, signed=False):
    """Format a GBP amount in the display currency (GBP or USD)."""
    amount, symbol = gbp_amount, "£"
    if state["config"].get("currency") == "USD":
        rate = state["cache"].get("GBP_USD", {}).get("rate")
        if rate:
            amount, symbol = gbp_amount * rate, "$"
    sign = "-" if amount < 0 else ("+" if signed else "")
    return f"{sign}{symbol}{abs(amount):,.2f}"


def write_json(path, data):
    """Write JSON safely (temp file then rename) with friendly errors."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temp, path)
        return True
    except PermissionError:
        notify("Permission denied; check the folder permissions.")
    except OSError as err:
        message = "Disk full; clean up and retry." if getattr(err, "errno", 0) == 28 else str(err)
        notify(f"Could not save {os.path.basename(path)}: {message}")
    log_event("ERROR", "file_io", f"failed to write {path}")
    return False


def read_json(path, default, use_backup=False):
    """Read JSON; on a parse error try path.bak (if allowed); else return default."""
    for candidate in ([path, path + ".bak"] if use_backup else [path]):
        try:
            with open(candidate, encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, OSError) as err:
            log_event("ERROR", "file_io", f"cannot read {candidate}: {err}")
            notify(f"Problem reading {os.path.basename(candidate)}: {err}")
    return default


def state_dir(state):
    """Folder holding this person's files (the app folder if no account is used)."""
    return state.get("dir") or BASE_DIR


def load_portfolio(user_dir=None):
    """Load this person's portfolio.json (restoring from backup if corrupt); empty if new."""
    path = os.path.join(user_dir or BASE_DIR, "portfolio.json")
    if not os.path.exists(path) and not os.path.exists(path + ".bak"):
        write_json(path, DEFAULT_PORTFOLIO)
    data = read_json(path, None, use_backup=True)
    return data if isinstance(data, list) else []


def save_portfolio(portfolio, user_dir=None):
    """Back up the old file, then write the new portfolio."""
    path = os.path.join(user_dir or BASE_DIR, "portfolio.json")
    if os.path.exists(path):
        try:
            shutil.copyfile(path, path + ".bak")
        except OSError:
            pass
    return write_json(path, portfolio)


def load_cache():
    """Load data_cache.json ({} if missing)."""
    data = read_json(CACHE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_cache(cache):
    """Write the cache to disk."""
    return write_json(CACHE_FILE, cache)


def load_config(user_dir=None):
    """Load this person's config.json, filling in any missing keys from the defaults."""
    path = os.path.join(user_dir or BASE_DIR, "config.json")
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    saved = read_json(path, {}, use_backup=True)
    if isinstance(saved, dict):
        for key, value in saved.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value
    if not os.path.exists(path):
        write_json(path, config)
    return config


def save_config(config, user_dir=None):
    """Write config.json (keeping one backup)."""
    path = os.path.join(user_dir or BASE_DIR, "config.json")
    config["last_updated"] = iso_now()
    if os.path.exists(path):
        try:
            shutil.copyfile(path, path + ".bak")
        except OSError:
            pass
    return write_json(path, config)


def to_gbp(price, currency, gbp_usd):
    """Convert a quoted price to pounds. 'GBp' means pence; USD uses the live rate."""
    if currency == "GBp" or currency == "GBX":
        return price / 100
    if currency == "USD":
        return price / gbp_usd if gbp_usd else None
    if currency == "GBP":
        return price
    return None  # unsupported currency


def fetch_fx(cache, force=False):
    """Return (rate, timestamp, source_note) for GBP/USD, using the cache if fresh."""
    entry = cache.get("GBP_USD", {})
    if entry and not force and age_seconds(entry.get("timestamp")) < PRICE_CACHE_SEC:
        return entry["rate"], entry["timestamp"], "cache"
    for attempt in range(FETCH_RETRIES):
        try:
            rate = float(yf.Ticker("GBPUSD=X").fast_info["lastPrice"])
            cache["GBP_USD"] = {"rate": rate, "timestamp": iso_now(), "source": "yfinance"}
            save_cache(cache)
            return rate, cache["GBP_USD"]["timestamp"], "yfinance"
        except Exception as err:
            log_event("ERROR", "yfinance", f"GBPUSD=X failed (try {attempt + 1}): {err}")
            if attempt < FETCH_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SEC)
    if entry:
        return entry["rate"], entry["timestamp"], "stale"
    return None, None, "unavailable"


def fetch_price(ticker, cache, gbp_usd, force=False):
    """Get a GBP price for ticker. Returns dict: price, timestamp, source, stale, currency.

    Uses the cache if it is under 60 minutes old (unless force). If yfinance fails we fall
    back to the last cached price and flag it stale. price is None if we have nothing.
    """
    entry = cache.get(ticker)
    if entry and not force and age_seconds(entry.get("timestamp")) < PRICE_CACHE_SEC:
        return {"price": entry["price"], "timestamp": entry["timestamp"], "source": "cache",
                "stale": False, "currency": entry.get("native_currency", "GBP")}
    for attempt in range(FETCH_RETRIES):
        try:
            info = yf.Ticker(ticker).fast_info
            native, currency = float(info["lastPrice"]), info["currency"]
            price = to_gbp(native, currency, gbp_usd)
            if price is None or price != price or price <= 0:
                raise ValueError(f"no usable price (currency {currency})")
            stamp = iso_now()
            cache[ticker] = {"price": price, "native_price": native,
                             "native_currency": currency, "timestamp": stamp,
                             "source": "yfinance"}
            save_cache(cache)
            return {"price": price, "timestamp": stamp, "source": "yfinance",
                    "stale": False, "currency": currency}
        except Exception as err:
            log_event("ERROR", "yfinance", f"{ticker} failed (try {attempt + 1}): {err}")
            if attempt < FETCH_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SEC)
    if entry:
        return {"price": entry["price"], "timestamp": entry["timestamp"], "source": "cache",
                "stale": True, "currency": entry.get("native_currency", "GBP")}
    return {"price": None, "timestamp": None, "source": "unavailable", "stale": True,
            "currency": "?"}


def get_quotes(state, force=False):
    """Fetch (or load from cache) a quote for every holding. Returns {ticker: quote}."""
    rate, _, _ = fetch_fx(state["cache"], force)
    quotes = {}
    for holding in state["portfolio"]:
        quotes[holding["ticker"]] = fetch_price(holding["ticker"], state["cache"], rate, force)
    return quotes


def fetch_company_info(state, ticker, force=False):
    """Fundamentals from yfinance .info (cached 1 day). Returns dict or {} on failure."""
    store = state["cache"].setdefault("info", {})
    entry = store.get(ticker)
    if entry and ("recommendationMean" in entry["data"] or entry["data"].get("_partial")):
        # a partial (fallback) result is retried after an hour; a full one is kept a day
        # (entries saved before analyst fields existed are refetched)
        limit = 3600 if entry["data"].get("_partial") else INFO_CACHE_SEC
        if not force and age_seconds(entry["timestamp"]) < limit:
            return entry["data"]
    data = None
    try:
        raw = yf.Ticker(ticker).info
        keys = ["shortName", "longName", "sector", "industry", "country", "marketCap",
                "trailingPE", "forwardPE", "beta", "fiftyTwoWeekLow", "fiftyTwoWeekHigh",
                "dividendYield", "dividendRate", "fullExchangeName", "targetMeanPrice",
                "recommendationKey", "recommendationMean", "targetLowPrice", "targetHighPrice",
                "numberOfAnalystOpinions", "currency", "quoteType"]
        data = {key: raw.get(key) for key in keys}
    except Exception as err:
        log_event("ERROR", "yfinance", f"info for {ticker} failed: {err}")
    if data is None or (data.get("marketCap") is None and data.get("trailingPE") is None):
        partial = info_fallback(state, ticker)       # Yahoo's detailed endpoint may be blocked
        if partial:
            data = {**(data or {}), **{k: v for k, v in partial.items() if v is not None}, "_partial": True}
    if data is None:
        return entry["data"] if entry else {}
    store[ticker] = {"timestamp": iso_now(), "data": data}
    save_cache(state["cache"])
    return data


def info_fallback(state, ticker):
    """Basic company facts from Yahoo's price and valuation endpoints (market cap, P/E, forward P/E,
    52-week range, currency, name). Used when the full info call is blocked or empty."""
    out = {}
    holding = next((h for h in state.get("portfolio", []) if h["ticker"] == ticker), None)
    if holding:
        out.update(shortName=holding.get("name"), longName=holding.get("name"),
                   sector=(holding.get("sector") if holding.get("sector") != "Unknown" else None),
                   country=(holding.get("region") if holding.get("region") != "Unknown" else None))
    try:
        handle = yf.Ticker(ticker)
        meta = handle.get_history_metadata() or {}
        out.setdefault("shortName", None)
        out["shortName"] = out.get("shortName") or meta.get("shortName")
        out["longName"] = out.get("longName") or meta.get("longName") or meta.get("shortName")
        out.update(currency=meta.get("currency"), fullExchangeName=meta.get("fullExchangeName"),
                   quoteType=meta.get("instrumentType"),
                   fiftyTwoWeekLow=meta.get("fiftyTwoWeekLow"), fiftyTwoWeekHigh=meta.get("fiftyTwoWeekHigh"))
    except Exception as err:
        log_event("ERROR", "yfinance", f"metadata for {ticker} failed: {err}")
    try:
        now = int(time.time())
        resp = requests.get(
            f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}",
            params={"symbol": ticker, "type": "trailingMarketCap,trailingPeRatio,trailingForwardPeRatio",
                    "merge": "false", "period1": now - 86400 * 20, "period2": now},
            headers={"User-Agent": WEB_USER_AGENT}, timeout=12)
        resp.raise_for_status()
        wanted = {"trailingMarketCap": "marketCap", "trailingPeRatio": "trailingPE",
                  "trailingForwardPeRatio": "forwardPE"}
        for block in resp.json().get("timeseries", {}).get("result", []) or []:
            kind = (block.get("meta", {}).get("type") or [""])[0]
            points = block.get(kind) or []
            if kind in wanted and points:
                value = ((points[-1] or {}).get("reportedValue") or {}).get("raw")
                if value:
                    out[wanted[kind]] = value
    except Exception as err:
        log_event("ERROR", "yahoo", f"valuation for {ticker} failed: {err}")
    return {k: v for k, v in out.items() if v is not None} or None


def fetch_finnhub_recommendation(state, ticker, force=False):
    """Latest analyst buy/hold/sell counts from Finnhub (cached 1 week). None if unavailable."""
    key = state["config"]["api_keys"].get("finnhub", "") or os.environ.get("FINNHUB_API_KEY", "")
    store = state["cache"].setdefault("analyst", {})
    entry = store.get(ticker)
    if entry and not force and age_seconds(entry["timestamp"]) < ANALYST_CACHE_SEC:
        return entry["data"]
    if not key:
        notify("No Finnhub key set (Settings > 1). Showing cached data if any.")
        return entry["data"] if entry else None
    try:
        url = "https://finnhub.io/api/v1/stock/recommendation"
        resp = requests.get(url, params={"symbol": ticker, "token": key}, timeout=10)
        if resp.status_code == 401:
            notify("Finnhub rejected the key (401). Check Settings > 1.")
            return entry["data"] if entry else None
        resp.raise_for_status()
        rows = resp.json()
        data = rows[0] if rows else None
        store[ticker] = {"timestamp": iso_now(), "data": data}
        save_cache(state["cache"])
        return data
    except Exception as err:
        log_event("ERROR", "finnhub", f"{ticker}: {err}")
        notify("Analyst data unavailable; using cached data if any.")
        return entry["data"] if entry else None


RATING_NAMES = ["Strong sell", "Sell", "Hold", "Buy", "Strong buy"]          # left to right on the gauge
_KEY_SCORE = {"strong_buy": 1.0, "buy": 2.0, "hold": 3.0, "underperform": 4.0, "sell": 4.0,
              "strong_sell": 5.0}
_COUNT_KEYS = ("strongBuy", "buy", "hold", "sell", "strongSell")
ANALYST_COUNTS_SEC = 12 * 3600


def rating_label(score):
    """Analyst mean score (1 = strong buy ... 5 = strong sell) -> Strong buy / Buy / Hold / Sell / Strong sell."""
    if score <= 1.5:
        return "Strong buy"
    if score <= 2.5:
        return "Buy"
    if score <= 3.5:
        return "Hold"
    if score <= 4.5:
        return "Sell"
    return "Strong sell"


def _analyst_counts(state, ticker):
    """{'strongBuy': n, 'buy': n, 'hold': n, 'sell': n, 'strongSell': n} or None. Yahoo first, then Finnhub if a key is set."""
    store = state["cache"].setdefault("analyst_counts", {})
    entry = store.get(ticker)
    if entry:
        limit = ANALYST_COUNTS_SEC if entry["data"] else 3600      # retry a miss after an hour
        if age_seconds(entry["timestamp"]) < limit:
            return entry["data"]
    counts = None
    try:
        summary = yf.Ticker(ticker).recommendations_summary
        if summary is not None and len(summary):
            row = summary.iloc[0]
            counts = {k: int(row.get(k) or 0) for k in _COUNT_KEYS}
    except Exception as err:
        log_event("ERROR", "yfinance", f"analyst counts for {ticker}: {err}")
    if not counts or not sum(counts.values()):
        counts = None
        if state["config"]["api_keys"].get("finnhub") or os.environ.get("FINNHUB_API_KEY"):
            row = fetch_finnhub_recommendation(state, ticker) or {}
            found = {k: int(row.get(k) or 0) for k in _COUNT_KEYS}
            counts = found if sum(found.values()) else None
    store[ticker] = {"timestamp": iso_now(), "data": counts}
    save_cache(state["cache"])
    return counts


NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0 Safari/537.36",
                  "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com",
                  "Referer": "https://www.nasdaq.com/"}
_NASDAQ_SCORE = {"strong buy": 1.0, "buy": 2.0, "hold": 3.0, "neutral": 3.0, "sell": 4.0, "strong sell": 5.0}


def nasdaq_analyst(state, ticker):
    """Analyst consensus for a US-listed ticker from Nasdaq.com's public data (works when Yahoo's
    session-key calls are blocked, e.g. on cloud hosts). None for UK/other tickers or if unavailable."""
    if not re.fullmatch(r"[A-Za-z]{1,5}", ticker):
        return None
    store = state["cache"].setdefault("analyst_nasdaq", {})
    entry = store.get(ticker)
    if entry and age_seconds(entry["timestamp"]) < (ANALYST_COUNTS_SEC if entry["data"] else 3600):
        return entry["data"]
    result = None
    try:
        base = f"https://api.nasdaq.com/api/analyst/{ticker.upper()}"
        ratings = requests.get(base + "/ratings", headers=NASDAQ_HEADERS, timeout=12).json().get("data") or {}
        score = _NASDAQ_SCORE.get((ratings.get("meanRatingType") or "").strip().lower())
        if score:
            match = re.search(r"Based on (\d+) analysts", ratings.get("ratingsSummary") or "")
            result = {"score": score, "total": int(match.group(1)) if match else None, "counts": None,
                      "target_mean": None, "target_low": None, "target_high": None}
            targets = requests.get(base + "/targetprice", headers=NASDAQ_HEADERS,
                                   timeout=12).json().get("data") or {}
            overview = targets.get("consensusOverview") or {}
            if overview:
                result.update(target_mean=overview.get("priceTarget"), target_low=overview.get("lowPriceTarget"),
                              target_high=overview.get("highPriceTarget"))
                if all(overview.get(k) is not None for k in ("buy", "hold", "sell")):
                    result["counts"] = [("Sell", int(overview["sell"])), ("Hold", int(overview["hold"])),
                                        ("Buy", int(overview["buy"]))]
    except Exception as err:
        log_event("ERROR", "nasdaq", f"analyst data for {ticker}: {err}")
        return entry["data"] if entry else None
    store[ticker] = {"timestamp": iso_now(), "data": result}
    save_cache(state["cache"])
    return result


def analyst_consensus(state, ticker, info):
    """Analyst consensus for the gauge, or None if no analyst data is available.

    {'score': 2.2 (1 strong buy .. 5 strong sell), 'label': 'Buy', 'position': 10-90 (% along the gauge; segment centres),
     'counts': [(name, n), ...] in gauge order, 'total': n, 'target_mean/low/high': per-share or None}
    Third-party opinion from Yahoo Finance (or Finnhub); we never rate anything ourselves.
    """
    counts = _analyst_counts(state, ticker)
    score = info.get("recommendationMean")
    if not score and counts and sum(counts.values()):
        weights = (1, 2, 3, 4, 5)
        score = sum(w * counts[k] for w, k in zip(weights, _COUNT_KEYS)) / sum(counts.values())
    if not score:
        score = _KEY_SCORE.get(info.get("recommendationKey") or "")
    if not score:
        alt = nasdaq_analyst(state, ticker)                  # Yahoo gave nothing: try Nasdaq (US tickers)
        if not alt:
            return None
        score = alt["score"]
        return {"score": score, "label": rating_label(score), "position": 10.0 + (5.0 - score) / 4.0 * 80.0,
                "counts": alt["counts"], "total": alt["total"], "source": "Nasdaq.com",
                "target_mean": alt["target_mean"], "target_low": alt["target_low"],
                "target_high": alt["target_high"]}
    score = max(1.0, min(5.0, float(score)))
    ordered = [("Strong sell", "strongSell"), ("Sell", "sell"), ("Hold", "hold"), ("Buy", "buy"),
               ("Strong buy", "strongBuy")]
    return {"score": score, "label": rating_label(score), "position": 10.0 + (5.0 - score) / 4.0 * 80.0,
            "counts": [(name, counts[key]) for name, key in ordered] if counts else None,
            "total": sum(counts.values()) if counts else info.get("numberOfAnalystOpinions"),
            "source": "Yahoo Finance",
            "target_mean": info.get("targetMeanPrice"), "target_low": info.get("targetLowPrice"),
            "target_high": info.get("targetHighPrice")}


HIGH_WORDS = ["earnings", "results", "guidance", "profit warning", "investigation", "probe",
              "lawsuit", "sues", "sued", "antitrust", "fined", "fine", "ruling", "acquisition",
              "acquires", "merger", "takeover", "bankruptcy", "recall", "ban", "banned",
              "sanctions", "export controls", "steps down", "resigns", "buyback", "dividend",
              "outage", "breach", "hack", "downgrade", "upgrade", "exit", "departs",
              "to leave", "quarterly results", "record"]


MEDIUM_WORDS = ["revenue", "forecast", "analyst", "price target", "launch", "partnership",
                "regulation", "tariff", "deal", "stake", "ceo", "layoffs", "contract",
                "shares fall", "shares jump", "shares surge", "shares plunge", "outlook",
                "quarter", "chief"]


def news_from_yfinance(ticker):
    """Headlines from Yahoo Finance search for one ticker."""
    items = []
    for raw in yf.Search(ticker, news_count=10).news or []:
        title = raw.get("title") or ""
        if not title:
            continue
        epoch = raw.get("providerPublishTime")
        published = datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") \
            if epoch else ""
        items.append({"ticker": ticker, "title": title, "summary": "",
                      "source": raw.get("publisher", "Yahoo Finance"),
                      "published": published, "url": raw.get("link", "")})
    return items


WEB_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36")


def _rss_items(ticker, content, default_source, source_of=None):
    """Turn an RSS document into our news item dicts."""
    items = []
    for node in ET.fromstring(content).iter("item"):
        title = (node.findtext("title") or "").strip()
        try:
            when = parsedate_to_datetime(node.findtext("pubDate")).astimezone(timezone.utc)
            published = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            published = ""
        source = (source_of(node) if source_of else "") or default_source
        link = node.findtext("link") or ""
        if "bing.com/news/apiclick" in link:             # use the real article address
            link = unquote(parse_qs(urlparse(link).query).get("url", [link])[0])
        if title and published:
            items.append({"ticker": ticker, "title": title, "summary": "", "source": source,
                          "published": published, "url": link})
    return items


def news_from_bing(ticker, query):
    """Headlines from Bing News RSS (a second wide net, works from cloud hosts)."""
    resp = requests.get("https://www.bing.com/news/search",
                        params={"q": query + " stock", "format": "rss", "setmkt": "en-GB"},
                        headers={"User-Agent": WEB_USER_AGENT}, timeout=12)
    resp.raise_for_status()
    return _rss_items(ticker, resp.content, "Bing News", lambda node: next(
        ((c.text or "").strip() for c in node if c.tag.endswith("}Source")), ""))


def news_from_yahoo_rss(ticker):
    """Headlines from Yahoo Finance's own RSS feed for one ticker."""
    resp = requests.get("https://feeds.finance.yahoo.com/rss/2.0/headline",
                        params={"s": ticker, "region": "US", "lang": "en-US"},
                        headers={"User-Agent": WEB_USER_AGENT}, timeout=12)
    resp.raise_for_status()
    return _rss_items(ticker, resp.content, "Yahoo Finance")


def news_from_newsapi(key, ticker, name):
    """Headlines from NewsAPI for one holding. Raises on HTTP errors."""
    resp = requests.get("https://newsapi.org/v2/everything",
                        params={"q": f'"{name}" OR {ticker}', "sortBy": "publishedAt",
                                "language": "en", "pageSize": 10, "apiKey": key}, timeout=10)
    resp.raise_for_status()
    items = []
    for art in resp.json().get("articles", []):
        items.append({"ticker": ticker, "title": art.get("title") or "",
                      "summary": art.get("description") or "",
                      "source": (art.get("source") or {}).get("name", "NewsAPI"),
                      "published": (art.get("publishedAt") or "")[:19] + "Z",
                      "url": art.get("url") or ""})
    return items


def news_cache_key(state):
    """Cache key for this set of holdings (so different people never share news)."""
    return "news:" + ",".join(sorted(h["ticker"] for h in state["portfolio"]))


def fetch_news(state, force=False):
    """Collect, clean, de-duplicate and score news for all holdings.

    Sources (fetched in parallel): Google News, SEC filings (US tickers), Yahoo Finance,
    NewsAPI (if a key is set). Stories that don't mention the company are dropped. Cached
    15 minutes; the last result is kept 24h as an offline fallback.
    Returns (items, note); note explains failures (or '').
    """
    if not state["portfolio"]:
        return [], "Add a holding to see news about it."
    news_key = news_cache_key(state)
    entry = state["cache"].get(news_key)
    if entry and not force and age_seconds(entry["timestamp"]) < NEWS_CACHE_SEC:
        return entry["items"], ""
    key = state["config"]["api_keys"].get("newsapi", "")
    holdings = state["portfolio"]
    notes, ciks = set(), {}
    # SEC filings need a contact email in the request (their rule), so they are opt-in.
    if state["config"].get("sec_contact") and any("." not in h["ticker"] for h in holdings):
        try:
            ciks = sec_cik_map(state)
        except Exception as err:
            log_event("ERROR", "sec", f"ticker map: {err}")
            notes.add("SEC filings unavailable.")

    def job(label, holding):
        """Run one source for one holding. Returns (label, items or exception)."""
        ticker = holding["ticker"]
        name = clean_company_name(holding.get("name") or ticker) or ticker
        try:
            if label == "Google News":
                base = ticker.split(".")[0]
                query = f'"{name}"' if "." in ticker else f'("{name}" OR {base})'
                return label, news_from_google(ticker, query)
            if label == "Bing News":
                base = ticker.split(".")[0]
                query = f'"{name}"' if "." in ticker else f'("{name}" OR {base})'
                return label, news_from_bing(ticker, query)
            if label == "Yahoo RSS":
                return label, news_from_yahoo_rss(ticker)
            if label == "SEC":
                return label, news_from_sec(state, ticker, ciks) if ciks else []
            if label == "Yahoo Finance":
                return label, news_from_yfinance(ticker)
            return label, news_from_newsapi(key, ticker, name)
        except Exception as err:
            log_event("ERROR", label, f"{ticker}: {err}")
            return label, err

    labels = ["Google News", "Bing News", "Yahoo Finance", "Yahoo RSS"] + (["SEC"] if ciks else []) + \
        (["NewsAPI"] if key else [])
    items = []
    fetched = {label: 0 for label in labels}          # raw stories per source, for the thin-feed hint
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(job, label, h) for h in holdings for label in labels]
        for future in futures:
            label, result = future.result()
            if isinstance(result, Exception):
                notes.add(f"{label} unavailable for some holdings.")
            else:
                fetched[label] += len(result)
                items.extend(result)

    aliases = {h["ticker"]: holding_aliases(h) for h in holdings}
    cleaned = []
    for item in items:
        item["kind"], item["tier"] = classify_source(item["source"], item.get("url", ""))
        if item["kind"] == "news" and any(a in item["source"].lower()
                                          for a in aliases.get(item["ticker"], [])):
            item["kind"] = "company"  # the company's own blog / site
        title_lower = item["title"].lower()
        if item["kind"] != "filing" and not any(has_word(title_lower, a)
                                                for a in aliases.get(item["ticker"], [])):
            continue  # not actually about this company
        if not item["published"]:
            continue
        item["score"] = score_item(item)
        cleaned.append(item)
    cleaned = group_duplicates(cleaned)
    cleaned.sort(key=lambda i: i["published"], reverse=True)
    if len(cleaned) < 2 * len(holdings):
        counts = ", ".join(f"{label} {count}" for label, count in fetched.items())
        notes.add(f"Few stories came back (raw stories fetched: {counts}).")
        log_event("WARN", "news", f"thin feed: fetched {counts}; kept {len(cleaned)}")
    if cleaned:
        for old_key in [k for k, v in state["cache"].items() if k.startswith("news:")
                        and age_seconds(v.get("timestamp")) > 2 * 86400]:
            state["cache"].pop(old_key)
        state["cache"][news_key] = {"timestamp": iso_now(), "items": cleaned[:120]}
        save_cache(state["cache"])
        return cleaned, " ".join(sorted(notes))
    if entry and age_seconds(entry["timestamp"]) < NEWS_MAX_AGE_SEC:
        return entry["items"], "News unavailable; showing cached headlines. " + " ".join(notes)
    return [], "No recent news found. " + " ".join(sorted(notes))


def compute_metrics(state, quotes):
    """Turn holdings + quotes into rows and portfolio totals (all in GBP)."""
    cash = state["config"].get("cash", 0.0)
    rows = []
    for holding in state["portfolio"]:
        quote = quotes.get(holding["ticker"], {})
        price = quote.get("price")
        shares, avg_cost = holding["shares"], holding["avg_cost"]
        cost = shares * avg_cost
        no_price = price is None
        value = cost if no_price else shares * price          # no data: assume at cost
        pl = value - cost
        rows.append({
            "ticker": holding["ticker"], "name": holding.get("name", holding["ticker"]),
            "shares": shares, "avg_cost": avg_cost, "price": price, "value": value,
            "cost": cost, "pl": pl, "pl_pct": (pl / cost * 100) if cost else None,
            "stale": quote.get("stale", True), "source": quote.get("source", "?"),
            "timestamp": quote.get("timestamp"), "no_price": no_price,
            "currency": holding.get("currency") or quote.get("currency", "GBP"),
            "sector": holding.get("sector", "Unknown"), "region": holding.get("region", "Unknown"),
        })
    holdings_value = sum(r["value"] for r in rows)
    invested = sum(r["cost"] for r in rows)
    total = holdings_value + cash
    for r in rows:
        r["weight"] = (r["value"] / total * 100) if total else 0.0          # of total incl. cash
        r["hold_weight"] = (r["value"] / holdings_value * 100) if holdings_value else 0.0
    isa_used = state["config"].get("isa_used", 0.0)
    allowance = float(state["config"].get("isa_allowance") or ISA_ALLOWANCE)
    return {
        "rows": rows, "cash": cash, "holdings_value": holdings_value, "total": total,
        "realised_pl": realised_pl_total(state),
        "invested": invested, "pl": holdings_value - invested,
        "pl_pct": ((holdings_value - invested) / invested * 100) if invested else None,
        "cash_weight": (cash / total * 100) if total else 0.0,
        "isa_used": isa_used, "isa_remaining": allowance - isa_used,
        "isa_allowance": allowance, "isa_used_pct": isa_used / allowance * 100,
    }


def hhi_from_weights(weights_pct):
    """Herfindahl-Hirschman Index (0-1) from a list of percentage weights."""
    return sum((w / 100) ** 2 for w in weights_pct)


def diversification_score(weights_pct):
    """Score 0-100 = (1 - HHI) * 100, capped. Also returns 'effective positions' = 1/HHI."""
    if not weights_pct:
        return 0, 0.0
    hhi = hhi_from_weights(weights_pct)
    return max(0, min(100, round((1 - hhi) * 100))), (1 / hhi if hhi else 0.0)


def group_by(rows, key):
    """Sum value and hold_weight by a row field (sector / region / currency)."""
    groups = {}
    for row in rows:
        entry = groups.setdefault(row[key], {"value": 0.0, "weight": 0.0})
        entry["value"] += row["value"]
        entry["weight"] += row["hold_weight"]
    return dict(sorted(groups.items(), key=lambda kv: -kv[1]["value"]))


def fmt_date(iso_date):
    """'2026-11-17' -> '17 Nov 2026'."""
    when = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"{when.day} {when:%b %Y}"


def days_until(iso_date):
    """Whole days from today until a 'YYYY-MM-DD' date."""
    return (datetime.strptime(iso_date, "%Y-%m-%d").date() - now_utc().date()).days


def fetch_earnings(state, ticker):
    """Next earnings date(s) as 'YYYY-MM-DD' strings (cached 1 day). [] if none/unavailable."""
    store = state["cache"].setdefault("calendar", {})
    entry = store.get(ticker)
    if entry and age_seconds(entry["timestamp"]) < INFO_CACHE_SEC:
        return entry["dates"]
    try:
        cal = yf.Ticker(ticker).calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        found = [d.strftime("%Y-%m-%d") for d in dates] if dates else []
        store[ticker] = {"timestamp": iso_now(), "dates": found}
        save_cache(state["cache"])
        return found
    except Exception as err:
        log_event("ERROR", "yfinance", f"calendar {ticker}: {err}")
        return entry["dates"] if entry else []


def data_signals(state, row, info):
    """Positives and risks worked out from the data (returns two lists of text)."""
    good, risks = [], []
    native = state["cache"].get(row["ticker"], {}).get("native_price")
    if row["pl_pct"] is not None and row["pl_pct"] > 0:
        good.append(f"Your position is up {row['pl_pct']:.1f}% on cost.")
    target = info.get("targetMeanPrice")
    if target and native:
        upside = (target / native - 1) * 100
        if upside > 10:
            good.append(f"Analyst mean target is {upside:.0f}% above the current price.")
        elif upside < -5:
            risks.append(f"Price is {-upside:.0f}% above the analyst mean target.")
    if (info.get("recommendationKey") or "") in ("buy", "strong_buy"):
        good.append("Analyst consensus is a buy.")
    if info.get("beta") and info["beta"] > 1.5:
        risks.append(f"High volatility: beta {info['beta']:.2f} (market = 1.00).")
    if info.get("forwardPE") and info["forwardPE"] > 30:
        risks.append(f"Forward P/E of {info['forwardPE']:.1f} is demanding.")
    high = info.get("fiftyTwoWeekHigh")
    if high and native and native / high > 0.95:
        risks.append("Trading within 5% of its 52-week high.")
    if row["weight"] > 40:
        risks.append(f"Concentration: {row['weight']:.1f}% of your portfolio.")
    return good, risks


def matched_keywords(item):
    """Keywords that raised an item's importance score (shown as 'Flagged for')."""
    text = f"{item['title']} {item['summary']}".lower()
    words = [w for w in HIGH_WORDS + MEDIUM_WORDS if has_word(text, w)][:4]
    return (["SEC filing"] + words)[:4] if item.get("kind") == "filing" else words


def valid_ticker(ticker):
    """True for 1-10 characters of letters, digits, '.' or '-'."""
    return 0 < len(ticker) <= 10 and all(c.isalnum() or c in ".-" for c in ticker)


def cost_basis_from_inputs(state, shares=None, price=None, price_ccy="GBP", total_paid=None,
                           amount=None, fees=0.0, fx_rate=None):
    """Work out (shares, total_cost_gbp, error) from whichever numbers the person knows.

    Supply one of these combinations (0 / None means "not given"):
      shares + price        -> cost = shares x price (+ fees)
      shares + total_paid   -> cost = total_paid (in GBP, already includes any fees)
      amount + price        -> shares = amount / price, cost = amount (+ fees)
    price_ccy is 'GBP' (pounds), 'GBp' (pence) or 'USD' (converted using fx_rate, the dollars
    per pound you got at the time, or today's live rate if left blank).
    Returns (shares, total_cost_gbp, error_message_or_None).
    """
    values = [v for v in (shares, price, total_paid, amount, fees) if v is not None]
    if any(v < 0 for v in values):
        return None, None, "Numbers can't be negative."
    fees = fees or 0.0
    price_gbp = None
    if price:
        if price_ccy == "GBp":
            price_gbp = price / 100
        elif price_ccy == "USD":
            rate = fx_rate or fetch_fx(state["cache"])[0]
            if not rate:
                return None, None, "No exchange rate available; enter the rate you paid."
            price_gbp = price / rate
        else:
            price_gbp = price
    if shares and total_paid:
        return shares, total_paid, None
    if shares and price_gbp:
        return shares, shares * price_gbp + fees, None
    if amount and price_gbp:
        return amount / price_gbp, amount + fees, None
    return None, None, ("Enter shares and a price, shares and the total paid, or the amount "
                        "invested and a price.")


def concentration_facts(m):
    """Plain statements about how the portfolio is spread (facts, not suggestions)."""
    rows, facts = m["rows"], []
    for r in rows:
        if r["hold_weight"] > 40 and len(rows) > 1:
            facts.append(f"{r['ticker']} makes up {r['hold_weight']:.1f}% of invested holdings "
                         "(a common rule of thumb flags anything over 40%).")
    for key in ("sector", "region"):
        for name, group in group_by(rows, key).items():
            if group["weight"] > 60 and len(rows) > 1:
                facts.append(f"{group['weight']:.1f}% of invested holdings share the {key} "
                             f"label '{name}'.")
    if m["cash_weight"] > 40:
        facts.append(f"Cash is {m['cash_weight']:.1f}% of the total portfolio.")
    return facts or ["No single holding, sector or region is above 40-60% of your invested "
                     "holdings."]


def remove_position(state, ticker):
    """Remove a holding from the tracker (cash is not changed)."""
    before = len(state["portfolio"])
    state["portfolio"] = [h for h in state["portfolio"] if h["ticker"] != ticker]
    if len(state["portfolio"]) == before:
        return False, f"{ticker} not found."
    save_portfolio(state["portfolio"], state_dir(state))
    return True, f"Removed {ticker}."


OPINION_OUTLETS = ["motley fool", "zacks", "benzinga", "investorplace", "24/7 wall st",
                   "stocktwits", "trefis", "tipranks", "seeking alpha", "gurufocus",
                   "simply wall st", "marketbeat", "insider monkey", "barchart", "tikr",
                   "the street", "thestreet", "kiplinger", "investing.com analysis"]


WIRE_OUTLETS = ["globenewswire", "pr newswire", "prnewswire", "business wire", "businesswire",
                "accesswire", "newsfile"]


TIER1_OUTLETS = ["reuters", "bloomberg", "associated press", "ap news", "financial times",
                 "wall street journal", "wsj", "bbc", "cnbc", "the guardian", "the times",
                 "sky news", "the economist", "the telegraph", "city a.m", "sec edgar",
                 "nikkei", "new york times", "washington post", "axios", "cnn"]


TIER2_OUTLETS = ["yahoo finance", "marketwatch", "techcrunch", "the verge", "ars technica",
                 "wired", "forbes", "fortune", "business insider", "investing.com",
                 "morningstar", "proactive", "london stock exchange", "rns", "investopedia",
                 "barron's", "the information", "tom's hardware", "engadget", "geekwire"]


NEWS_USER_AGENT = "Mozilla/5.0 (compatible; XJS-Financials ISA Terminal personal research tool)"


SEC_ITEM_TEXT = {
    "1.01": "entered a material agreement", "1.02": "terminated a material agreement",
    "1.03": "bankruptcy or receivership", "2.01": "completed an acquisition or disposal",
    "2.02": "results of operations (earnings)", "2.05": "restructuring costs",
    "3.01": "listing notice", "4.02": "financial statements should not be relied on",
    "5.02": "director or executive change", "5.07": "shareholder vote results",
    "7.01": "Regulation FD disclosure", "8.01": "other events"}


SEC_ITEM_SCORE = {"1.03": 5, "4.02": 5, "2.02": 5, "2.01": 5, "3.01": 5, "1.01": 4, "1.02": 4,
                  "2.05": 4, "5.02": 4, "5.07": 3, "7.01": 3, "8.01": 3}


# A story needs at least one of these (money / markets / corporate-event words) to score above 2.
# This stops product reviews and gadget news from ranking as important company news.
FINANCE_WORDS = ["earnings", "results", "revenue", "profit", "loss", "sales", "shares", "stock",
                 "investor", "investors", "analyst", "analysts", "market", "valuation", "guidance",
                 "outlook", "forecast", "quarter", "quarterly", "dividend", "buyback", "price target",
                 "rating", "downgrade", "upgrade", "debt", "bond", "lawsuit", "sues", "sued",
                 "settlement", "antitrust", "regulator", "regulators", "fine", "fined", "probe",
                 "investigation", "acquisition", "acquires", "acquire", "merger", "takeover", "deal",
                 "ipo", "layoffs", "job cuts", "ceo", "chief executive", "cfo", "chairman", "tariff",
                 "sanctions", "export controls", "ban", "bankruptcy", "recall", "contract", "stake",
                 "billion", "million", "trillion", "capex", "margin", "cash flow", "output",
                 "production", "oil", "gas", "refinery", "refining"]
# 'upgrade' / 'downgrade' only mean an analyst rating change when one of these is nearby.
RATING_CONTEXT = ["analyst", "analysts", "rating", "price target", "to buy", "to sell", "to hold",
                  "overweight", "underweight", "outperform", "underperform", "neutral", "target"]


def has_word(text, word):
    """True if `word` appears in text as a whole word / phrase (so 'ban' doesn't match 'bank')."""
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


def clean_company_name(name):
    """'BP PLC $0.25' -> 'BP'; 'Scottish Mortgage Ord' -> 'Scottish Mortgage'."""
    return re.sub(r"\s*\b(plc|p\.l\.c\.?|inc\.?|corp\.?|corporation|ltd|ord|co\.?|n\.?v\.?)\b.*$|"
                  r"\s*\$?[0-9.]+.*$", "", (name or "").strip(), flags=re.I).strip()


def holding_aliases(holding):
    """Lower-case names a story must mention to count as being about this holding."""
    names = [holding.get("name", "")] + list(holding.get("aliases", []))
    aliases = set()
    for name in names:
        cleaned = clean_company_name(name).lower()
        if len(cleaned) >= 2:
            aliases.add(cleaned)
    base = holding["ticker"].split(".")[0].lower()
    if len(base) >= 3:
        aliases.add(base)
    return sorted(aliases)


def classify_source(source, url=""):
    """Returns (kind, tier). kind: filing / opinion / press_release / news. tier 1 = best."""
    name = source.lower()
    if "sec.gov" in url or "sec edgar" in name:
        return "filing", 1
    if any(o in name for o in OPINION_OUTLETS):
        return "opinion", 3
    if any(o in name for o in WIRE_OUTLETS):
        return "press_release", 2
    if any(o in name for o in TIER1_OUTLETS):
        return "news", 1
    if any(o in name for o in TIER2_OUTLETS):
        return "news", 2
    return "news", 3


def score_item(item):
    """Importance 1-5 from event words, source quality and age (a transparent heuristic, not a
    forecast of what a story does to a share price)."""
    if item["kind"] == "filing":
        score = item.get("filing_score", 3)
    else:
        text = f"{item['title']} {item['summary']}".lower()
        text = re.sub(r"fine[- ]tun\w*", " ", text)     # AI "fine-tuning" is not a regulatory fine
        has_rating_context = any(has_word(text, w) for w in RATING_CONTEXT)
        points = sum(2 for w in HIGH_WORDS
                     if has_word(text, w) and (w not in ("upgrade", "downgrade") or has_rating_context)) + \
            sum(1 for w in MEDIUM_WORDS if has_word(text, w))
        score = 1 + min(points, 3)
        if item["tier"] == 1:
            score += 1
        if not any(has_word(text, w) for w in FINANCE_WORDS):
            score = min(score, 2)           # no money / market / corporate-event words at all
        if item["kind"] == "opinion":
            score = min(score, 2)
        elif item["kind"] in ("press_release", "company"):
            score = min(score, 3)
    age_days = age_seconds(item["published"]) / 86400
    if age_days > 10:
        score -= 2
    elif age_days > 5:
        score -= 1
    return max(1, min(5, score))


STOP_WORDS = {"the", "a", "an", "of", "to", "in", "for", "on", "and", "is", "as", "at", "by",
              "with", "from", "its", "after", "amid", "says", "new", "stock", "shares", "this"}


def title_tokens(title):
    """Significant lower-case words in a headline, for spotting duplicate stories."""
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in STOP_WORDS}


def same_story(a, b):
    """True if two items about the same ticker are almost certainly the same story: very
    similar headlines, or two shared words including an event word, within 48 hours."""
    if a["ticker"] != b["ticker"]:
        return False
    common = a["_tokens"] & b["_tokens"]
    if len(common) < 2:
        return False
    if len(common) / len(a["_tokens"] | b["_tokens"]) >= 0.55 or \
            len(common) / min(len(a["_tokens"]), len(b["_tokens"])) >= 0.8:
        return True
    close_in_time = abs(age_seconds(a["published"]) - age_seconds(b["published"])) < 48 * 3600
    shared_event = any(has_word(" ".join(common), w) for w in HIGH_WORDS)
    return close_in_time and shared_event


def group_duplicates(items):
    """Keep the best item of each duplicate group and record the other outlets in 'also'."""
    items = sorted(items, key=lambda i: (-i["score"], i["tier"], i["published"]), reverse=False)
    kept = []
    for item in items:
        tokens = title_tokens(item["title"])
        for existing in kept:
            if same_story(existing, {"ticker": item["ticker"], "_tokens": tokens,
                                     "published": item["published"]}):
                if item["source"] != existing["source"] and item["source"] not in existing["also"]:
                    existing["also"].append(item["source"])
                break
        else:
            item["_tokens"], item["also"] = tokens, []
            kept.append(item)
    for item in kept:
        del item["_tokens"]
    return kept


def news_from_google(ticker, query):
    """Headlines from Google News RSS (many outlets: Reuters, BBC, FT, CNBC ...)."""
    resp = requests.get("https://news.google.com/rss/search",
                        params={"q": query + " when:7d", "hl": "en-GB", "gl": "GB",
                                "ceid": "GB:en"},
                        headers={"User-Agent": NEWS_USER_AGENT}, timeout=12)
    resp.raise_for_status()
    items = []
    for node in ET.fromstring(resp.content).iter("item"):
        title = (node.findtext("title") or "").strip()
        source = (node.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        try:
            when = parsedate_to_datetime(node.findtext("pubDate")).astimezone(timezone.utc)
            published = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            published = ""
        if title and published:
            items.append({"ticker": ticker, "title": title, "summary": "",
                          "source": source or "Google News", "published": published,
                          "url": node.findtext("link") or ""})
    return items


def sec_headers(state):
    """SEC asks automated clients to identify themselves."""
    contact = state["config"].get("sec_contact", "")
    return {"User-Agent": f"XJS-Financials ISA Terminal {contact}".strip(),
            "Accept-Encoding": "gzip, deflate"}


def sec_cik_map(state):
    """{TICKER: [cik, company name]} for US-listed companies (cached 30 days)."""
    store = state["cache"].setdefault("sec_ciks", {"timestamp": "", "map": {}})
    if store["map"] and age_seconds(store["timestamp"]) < 30 * 86400:
        return store["map"]
    resp = requests.get("https://www.sec.gov/files/company_tickers.json",
                        headers=sec_headers(state), timeout=15)
    resp.raise_for_status()
    store["map"] = {v["ticker"].upper(): [v["cik_str"], v["title"]] for v in resp.json().values()}
    store["timestamp"] = iso_now()
    save_cache(state["cache"])
    return store["map"]


def news_from_sec(state, ticker, ciks):
    """Recent official SEC filings (8-K, 10-Q, 10-K, last 14 days) for a US ticker."""
    entry = ciks.get(ticker.upper())
    if not entry:
        return []
    cik, company = entry
    resp = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                        headers=sec_headers(state), timeout=15)
    resp.raise_for_status()
    recent = resp.json()["filings"]["recent"]
    items = []
    for form, date, acc, doc, codes in zip(recent["form"], recent["filingDate"],
                                           recent["accessionNumber"], recent["primaryDocument"],
                                           recent["items"]):
        published = f"{date}T00:00:00Z"
        if age_seconds(published) > 14 * 86400:
            break  # newest first
        if form not in ("8-K", "10-Q", "10-K"):
            continue
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        if form == "8-K":
            parts = [SEC_ITEM_TEXT[c] for c in code_list if c in SEC_ITEM_TEXT]
            what = "; ".join(parts) or "current report"
            score = max([SEC_ITEM_SCORE.get(c, 3) for c in code_list] or [3])
            title = f"{company} files 8-K: {what}"
        else:
            title = f"{company} files {'quarterly' if form == '10-Q' else 'annual'} report ({form})"
            score = 4
        items.append({"ticker": ticker, "title": title, "summary": "", "source": "SEC EDGAR",
                      "published": published, "filing_score": score,
                      "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                             f"{acc.replace('-', '')}/{doc}"})
    return items


# ============================================================================
# PRICE HISTORY  (charts)
# ============================================================================
HISTORY_RANGES = {"1M": ("1mo", "1d"), "3M": ("3mo", "1d"), "6M": ("6mo", "1d"),
                  "YTD": ("ytd", "1d"), "1Y": ("1y", "1d"), "5Y": ("5y", "1wk"), "ALL": ("max", "1mo")}
HISTORY_CACHE_SEC = 3600


def fetch_history(state, ticker, range_key="1Y", force=False):
    """Closing prices and volume for a chart. Pence prices are converted to pounds.

    Returns {"dates": [...], "close": [...], "volume": [...], "currency": "USD"} or None.
    Cached for an hour in the shared price cache.
    """
    period, interval = HISTORY_RANGES.get(range_key, HISTORY_RANGES["1Y"])
    key = f"hist:{ticker}:{range_key}"
    entry = state["cache"].get(key)
    if entry and not force and age_seconds(entry["timestamp"]) < HISTORY_CACHE_SEC:
        return entry["data"]
    try:
        handle = yf.Ticker(ticker)
        frame = handle.history(period=period, interval=interval, auto_adjust=False).dropna(
            subset=["Close"])
        if frame.empty:
            raise ValueError("no price history returned")
        currency = (state["cache"].get(ticker) or {}).get("native_currency") or \
            handle.fast_info["currency"]
        scale = 0.01 if currency in ("GBp", "GBX") else 1.0
        data = {"dates": [d.strftime("%Y-%m-%d") for d in frame.index],
                "close": [round(float(v) * scale, 4) for v in frame["Close"]],
                "volume": [int(v) if v == v else 0 for v in frame["Volume"]],
                "currency": "GBP" if scale != 1.0 else currency}
        for old_key in [k for k, v in state["cache"].items() if k.startswith("hist:")
                        and age_seconds(v.get("timestamp")) > 86400]:
            state["cache"].pop(old_key)
        state["cache"][key] = {"timestamp": iso_now(), "data": data}
        save_cache(state["cache"])
        return data
    except Exception as err:
        log_event("ERROR", "yfinance", f"history {ticker}: {err}")
        return entry["data"] if entry else None


def drawdown_series(closes):
    """Percentage below the highest close so far, for each point (0 at a new high)."""
    peak, out = 0.0, []
    for value in closes:
        peak = max(peak, value)
        out.append(round((value / peak - 1) * 100, 3) if peak else 0.0)
    return out


def portfolio_history(state, range_key="1Y"):
    """What TODAY'S holdings would have been worth through time, in GBP.

    This is not your real past balance: it applies today's share counts to past prices.
    Returns {"dates": [...], "value": [...], "cost": float, "skipped": [tickers]} or None.
    """
    import pandas as pd
    series, skipped, fx_series = {}, [], None
    for holding in state["portfolio"]:
        data = fetch_history(state, holding["ticker"], range_key)
        if not data:
            skipped.append(holding["ticker"])
            continue
        prices = pd.Series(data["close"], index=pd.to_datetime(data["dates"]))
        if data["currency"] == "USD":
            if fx_series is None:
                fx = fetch_history(state, "GBPUSD=X", range_key)
                if fx:
                    fx_series = pd.Series(fx["close"], index=pd.to_datetime(fx["dates"]))
            if fx_series is None:
                skipped.append(holding["ticker"])
                continue
            prices = prices / fx_series.reindex(prices.index, method="ffill").bfill()
        elif data["currency"] != "GBP":
            skipped.append(holding["ticker"])
            continue
        series[holding["ticker"]] = prices * holding["shares"]
    if not series:
        return None
    frame = pd.DataFrame(series).sort_index().ffill().bfill()
    total = frame.sum(axis=1)
    cost = sum(h["shares"] * h["avg_cost"] for h in state["portfolio"]
               if h["ticker"] in series)
    return {"dates": [d.strftime("%Y-%m-%d") for d in total.index],
            "value": [round(float(v), 2) for v in total], "cost": round(cost, 2),
            "skipped": skipped}


def today_iso():
    """Today's date as YYYY-MM-DD."""
    return now_utc().date().isoformat()


# ============================================================================
# TRANSACTIONS  (ledger, FIFO lots, realised P&L)
# ============================================================================
def load_transactions(user_dir=None):
    """Load transactions.json (a list); empty if the person has none yet."""
    data = read_json(os.path.join(user_dir or BASE_DIR, "transactions.json"), [], use_backup=True)
    return data if isinstance(data, list) else []


def save_transactions(transactions, user_dir=None):
    """Write transactions.json (keeping one backup)."""
    path = os.path.join(user_dir or BASE_DIR, "transactions.json")
    if os.path.exists(path):
        try:
            shutil.copyfile(path, path + ".bak")
        except OSError:
            pass
    return write_json(path, transactions)


def get_transactions(state):
    """This person's transactions, loaded once and kept on the state dict."""
    if "transactions" not in state:
        state["transactions"] = load_transactions(state_dir(state))
    return state["transactions"]


def record_transaction(state, kind, ticker, shares, price, fees, notes="", date="",
                       cost_basis=None, realised_pl=None, opened="", historical=False):
    """Append a BUY or SELL to the ledger and save it. price is GBP per share before fees."""
    transactions = get_transactions(state)
    total = shares * price + fees if kind == "BUY" else shares * price - fees
    entry = {"id": max([t.get("id", 0) for t in transactions] + [0]) + 1,
             "date": date or today_iso(), "ticker": ticker, "type": kind,
             "shares": round(shares, 6), "price": round(price, 6), "fees": round(fees or 0.0, 2),
             "total": round(total, 2), "notes": notes or "", "created": iso_now()}
    if kind == "SELL":
        entry["cost_basis"] = round(cost_basis or 0.0, 2)
        entry["realised_pl"] = round(realised_pl or 0.0, 2)
        entry["opened"] = opened or ""
    if historical:
        entry["historical"] = True   # a finished trade: shown in history and returns, no effect on holdings or cash
    transactions.append(entry)
    save_transactions(transactions, state_dir(state))
    return entry


def realised_pl_total(state):
    """Sum of profit/loss on everything sold so far (after fees)."""
    return round(sum(t.get("realised_pl", 0.0) for t in get_transactions(state)
                     if t.get("type") == "SELL"), 2)


def ensure_lots(holding):
    """Make sure a holding has FIFO lots that add up to its share count. Holdings entered or
    edited by hand get a single lot at their average cost."""
    lots = holding.get("lots") or []
    if not lots or abs(sum(l["shares"] for l in lots) - holding["shares"]) > 1e-6:
        lots = [{"date": holding.get("bought_on", ""), "shares": holding["shares"],
                 "cost": holding["avg_cost"]}]
    lots.sort(key=lambda l: l.get("date") or "")   # oldest first; blank (unknown) dates first
    holding["lots"] = lots
    return lots


def refresh_from_lots(holding):
    """Set shares and average cost from the remaining lots."""
    lots = holding["lots"]
    holding["shares"] = sum(l["shares"] for l in lots)
    if holding["shares"] > 0:
        holding["avg_cost"] = round(sum(l["shares"] * l["cost"] for l in lots)
                                    / holding["shares"], 6)


def cost_basis_check(state):
    """Compare what the ledger says you paid with the cost basis held on each holding."""
    rows = []
    for holding in state["portfolio"]:
        ticker = holding["ticker"]
        mine = [t for t in get_transactions(state) if t["ticker"] == ticker]
        bought = sum(t["total"] for t in mine if t["type"] == "BUY")
        sold_cost = sum(t.get("cost_basis", 0.0) for t in mine if t["type"] == "SELL")
        held = holding["shares"] * holding["avg_cost"]
        rows.append({"ticker": ticker, "has_ledger": bool(mine), "bought": bought,
                     "sold_cost": sold_cost, "ledger_remaining": bought - sold_cost,
                     "holding_cost": held, "difference": held - (bought - sold_cost)})
    return rows


def add_position(state, ticker, shares, avg_cost=None, notes="", deduct_cash=True,
                 bought_on="", fees=0.0, skip_price_check=False):
    """Buy shares: add to a holding (creating it if new) and record a BUY in the ledger.

    avg_cost is GBP per share including fees (None = today's price). deduct_cash takes the cost
    out of the cash balance. Allowance used is NOT changed: only money paid INTO the account
    counts towards the yearly allowance, so set that separately. skip_price_check lets you add
    a ticker Yahoo doesn't know (you must give avg_cost). Returns (ok, message).
    """
    ticker = (ticker or "").strip().upper()
    if not valid_ticker(ticker):
        return False, f"Ticker '{ticker}' isn't valid. Use 1-10 letters or numbers, e.g. BP.L or AAPL."
    if not (0 < shares <= MAX_SHARES):
        return False, f"Shares must be above 0 and at most {MAX_SHARES:,}. Got: {shares:g}"
    rate, _, _ = fetch_fx(state["cache"])
    quote = fetch_price(ticker, state["cache"], rate)
    if quote["price"] is None and not (skip_price_check and avg_cost):
        return False, (f"Ticker {ticker} not found on Yahoo. Try another ticker (UK shares end in "
                       ".L) or add it without live data.")
    cost = avg_cost if avg_cost else quote["price"]
    if not (0 < cost <= 100000):
        return False, f"Average cost must be above 0. Got: {cost:g}"
    fees = fees or 0.0
    total_cost = shares * cost
    price_ex_fees = max(cost - fees / shares, 0.0)
    existing = next((h for h in state["portfolio"] if h["ticker"] == ticker), None)
    if existing:
        holding = existing
        ensure_lots(holding)
        if notes:
            holding["notes"] = notes
    else:
        info = fetch_company_info(state, ticker) if quote["price"] is not None else {}
        is_fund = (info.get("quoteType") or "") in ("ETF", "MUTUALFUND")
        holding = {
            "ticker": ticker, "name": clean_company_name(info.get("shortName")) or ticker,
            "shares": 0.0, "avg_cost": cost,
            "sector": info.get("sector") or ("Fund/ETF" if is_fund else "Unknown"),
            "region": info.get("country") or "Unknown",
            "currency": "GBP" if quote["currency"] in ("GBp", "GBX", "?") else quote["currency"],
            "notes": notes, "lots": []}
        state["portfolio"].append(holding)
    holding["lots"].append({"date": bought_on or today_iso(), "shares": shares, "cost": cost})
    refresh_from_lots(holding)
    if bought_on and (not holding.get("bought_on") or bought_on < holding["bought_on"]):
        holding["bought_on"] = bought_on
    if fees:
        holding["fees_paid"] = round(holding.get("fees_paid", 0.0) + fees, 2)
    record_transaction(state, "BUY", ticker, shares, price_ex_fees, fees, notes, bought_on)
    msg = f"Bought {shares:,.4g} x {ticker}: total cost £{total_cost:,.2f} (£{cost:,.2f} a share)."
    if deduct_cash:
        cash = state["config"]["cash"]
        state["config"]["cash"] = max(0.0, cash - total_cost)
        if cash < total_cost:
            msg += " Your cash balance was lower than this, so cash is now £0 - update it if wrong."
    save_portfolio(state["portfolio"], state_dir(state))
    save_config(state["config"], state_dir(state))
    return True, msg


def sell_position(state, ticker, shares, price=None, fees=0.0, add_to_cash=True, sold_on="",
                  notes=""):
    """Sell shares using FIFO (oldest purchases first) and record a SELL in the ledger.

    price is GBP per share (None = live price). proceeds = shares x price - fees. With
    add_to_cash the proceeds go into the cash balance, otherwise cash is left for you to update.
    A holding that is fully sold is removed from the portfolio (it stays in the ledger).
    Returns (ok, message).
    """
    holding = next((h for h in state["portfolio"] if h["ticker"] == ticker), None)
    if holding is None:
        return False, f"{ticker} not found in your portfolio."
    if not (0 < shares <= holding["shares"] + 1e-9):
        return False, f"Shares to sell must be above 0 and at most {holding['shares']:g}. Got: {shares:g}"
    fees = fees or 0.0
    if fees < 0:
        return False, f"Fees can't be negative. Got: {fees:g}"
    if price is None:
        rate, _, _ = fetch_fx(state["cache"])
        price = fetch_price(ticker, state["cache"], rate)["price"]
        if price is None:
            return False, "No live price available. Enter the sale price yourself."
    lots = ensure_lots(holding)
    remaining, cost_sold, opened_dates = shares, 0.0, []
    for lot in lots:
        take = min(lot["shares"], remaining)
        cost_sold += take * lot["cost"]
        if take > 0 and lot.get("date"):
            opened_dates.append(lot["date"])
        lot["shares"] -= take
        remaining -= take
        if remaining <= 1e-9:
            break
    holding["lots"] = [l for l in lots if l["shares"] > 1e-9]
    proceeds = shares * price - fees
    realised = proceeds - cost_sold
    if holding["lots"]:
        refresh_from_lots(holding)
    else:
        state["portfolio"] = [h for h in state["portfolio"] if h["ticker"] != ticker]
    record_transaction(state, "SELL", ticker, shares, price, fees, notes, sold_on,
                       cost_basis=cost_sold, realised_pl=realised,
                       opened=min(opened_dates) if opened_dates else "")
    if add_to_cash:
        state["config"]["cash"] += proceeds
    save_portfolio(state["portfolio"], state_dir(state))
    save_config(state["config"], state_dir(state))
    sign = "+" if realised >= 0 else "-"
    return True, (f"Sold {shares:g} x {ticker} at £{price:,.2f}: proceeds £{proceeds:,.2f}, realised "
                  f"{sign}£{abs(realised):,.2f}." + (" Proceeds added to cash." if add_to_cash else
                                                  " Update your cash balance yourself."))


def update_position(state, ticker, shares=None, avg_cost=None, name=None, notes=None,
                    sector=None, region=None):
    """Change a holding's share count, average cost (GBP), name, notes, sector or region.
    Changing shares or cost resets its FIFO lots to a single lot at the new numbers."""
    holding = next((h for h in state["portfolio"] if h["ticker"] == ticker), None)
    if holding is None:
        return False, f"{ticker} not found."
    if shares is not None and not (0 < shares <= MAX_SHARES):
        return False, f"{ticker}: shares must be above 0 and at most {MAX_SHARES:,}. Got: {shares:g}"
    if avg_cost is not None and not (0 < avg_cost <= 100000):
        return False, f"{ticker}: average cost must be above 0. Got: {avg_cost:g}"
    if notes is not None:
        holding["notes"] = notes
    if sector is not None and sector.strip():
        holding["sector"] = sector.strip()
    if region is not None and region.strip():
        holding["region"] = region.strip()
    if name is not None and name.strip():
        holding["name"] = name.strip()
    if shares is not None:
        holding["shares"] = shares
    if avg_cost is not None:
        holding["avg_cost"] = avg_cost
    if shares is not None or avg_cost is not None:
        holding.pop("lots", None)
    save_portfolio(state["portfolio"], state_dir(state))
    return True, f"Updated {ticker}."


# ============================================================================
# EXPORT (CSV) AND BACKUP / RESTORE
# ============================================================================
def to_csv(headers, rows):
    """Rows of values -> CSV text."""
    import csv
    import io
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue()


def holdings_csv(metrics):
    """Current holdings as CSV."""
    return to_csv(["ticker", "name", "shares", "avg_cost_gbp", "price_gbp", "value_gbp", "pl_gbp",
                   "pl_pct", "weight_pct", "sector", "region"],
                  [[r["ticker"], r["name"], f"{r['shares']:g}", f"{r['avg_cost']:.4f}",
                    "" if r["no_price"] else f"{r['price']:.4f}", f"{r['value']:.2f}",
                    f"{r['pl']:.2f}", "" if r["pl_pct"] is None else f"{r['pl_pct']:.2f}",
                    f"{r['weight']:.2f}", r["sector"], r["region"]] for r in metrics["rows"]])


def exposure_csv(metrics):
    """Sector, region and currency breakdowns as one CSV."""
    rows = []
    for key in ("sector", "region", "currency"):
        for name, group in group_by(metrics["rows"], key).items():
            rows.append([key, name, f"{group['value']:.2f}", f"{group['weight']:.2f}"])
    return to_csv(["dimension", "label", "value_gbp", "weight_pct_of_holdings"], rows)


def transactions_csv(state):
    """The whole ledger as CSV."""
    fields = ["date", "ticker", "type", "shares", "price", "fees", "total", "cost_basis",
              "realised_pl", "opened", "notes"]
    return to_csv(fields, [[t.get(f, "") for f in fields] for t in
                           sorted(get_transactions(state), key=lambda t: (t["date"], t["id"]))])


def research_csv(state, metrics):
    """A snapshot of company data for each holding (from the cached Yahoo data)."""
    rows = []
    for r in metrics["rows"]:
        info = fetch_company_info(state, r["ticker"])
        dates = fetch_earnings(state, r["ticker"])
        rows.append([r["ticker"], r["name"], info.get("sector") or r["sector"],
                     info.get("trailingPE") or "", info.get("forwardPE") or "",
                     info.get("beta") or "", info.get("recommendationKey") or "",
                     info.get("numberOfAnalystOpinions") or "", info.get("targetMeanPrice") or "",
                     info.get("currency") or "", dates[0] if dates else ""])
    return to_csv(["ticker", "name", "sector", "pe", "forward_pe", "beta", "consensus",
                   "analysts", "mean_target", "target_currency", "next_earnings"], rows)


BACKUP_VERSION = 1
BACKUP_SKIP_KEYS = ("api_keys", "sec_contact")   # secrets are never written to backups


def make_backup(state):
    """Everything about this account as a dict, ready to save as JSON. API keys are left out."""
    return {"app": "xavi", "version": BACKUP_VERSION, "created": iso_now(),
            "portfolio": state["portfolio"], "transactions": get_transactions(state),
            "config": {k: v for k, v in state["config"].items() if k not in BACKUP_SKIP_KEYS}}


def restore_backup(state, data):
    """Validate a backup and, if it is sound, replace this account's data with it.
    Nothing is changed unless the whole file checks out. Returns (ok, message)."""
    if not isinstance(data, dict) or data.get("app") != "xavi":
        return False, "This doesn't look like a XAVI backup file."
    if data.get("version") != BACKUP_VERSION:
        return False, f"Unsupported backup version: {data.get('version')}."
    portfolio, transactions, config = data.get("portfolio"), data.get("transactions", []), \
        data.get("config", {})
    if not isinstance(portfolio, list) or not isinstance(transactions, list) \
            or not isinstance(config, dict):
        return False, "The backup is damaged (wrong structure)."
    clean_portfolio = []
    for number, h in enumerate(portfolio, 1):
        try:
            ticker, shares, cost = str(h["ticker"]).upper(), float(h["shares"]), float(h["avg_cost"])
        except (KeyError, TypeError, ValueError):
            return False, f"Holding {number}: missing or unreadable ticker, shares or cost."
        if not valid_ticker(ticker):
            return False, f"Holding {number}: '{ticker}' isn't a valid ticker."
        if not (0 < shares <= MAX_SHARES) or cost <= 0:
            return False, f"Holding {number} ({ticker}): shares and cost must be above 0."
        entry = {k: h[k] for k in ("name", "sector", "region", "currency", "notes", "bought_on",
                                   "fees_paid") if k in h}
        entry.update(ticker=ticker, shares=shares, avg_cost=cost)
        lots = []
        for lot in h.get("lots") or []:
            try:
                lots.append({"date": str(lot.get("date", "")), "shares": float(lot["shares"]),
                             "cost": float(lot["cost"])})
            except (KeyError, TypeError, ValueError, AttributeError):
                lots = []
                break
        if lots:
            entry["lots"] = lots
        clean_portfolio.append(entry)
    clean_transactions = []
    for number, t in enumerate(transactions, 1):
        try:
            entry = {"id": int(t["id"]), "date": str(t["date"]), "ticker": str(t["ticker"]).upper(),
                     "type": t["type"], "shares": float(t["shares"]), "price": float(t["price"]),
                     "fees": float(t.get("fees", 0.0)), "total": float(t["total"]),
                     "notes": str(t.get("notes", "")), "created": str(t.get("created", ""))}
        except (KeyError, TypeError, ValueError):
            return False, f"Transaction {number}: missing or unreadable fields."
        if entry["type"] not in ("BUY", "SELL") or entry["shares"] <= 0:
            return False, f"Transaction {number}: type must be BUY or SELL with shares above 0."
        if entry["type"] == "SELL":
            entry["cost_basis"] = float(t.get("cost_basis", 0.0))
            entry["realised_pl"] = float(t.get("realised_pl", 0.0))
            entry["opened"] = str(t.get("opened", ""))
        if t.get("historical"):
            entry["historical"] = True
        clean_transactions.append(entry)
    new_config = {}
    for key, default in DEFAULT_CONFIG.items():
        if key in BACKUP_SKIP_KEYS or key not in config:
            continue
        value = config[key]
        if isinstance(default, (int, float)) and not isinstance(default, bool):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return False, f"Setting '{key}' isn't a number."
        elif not isinstance(value, type(default)):
            return False, f"Setting '{key}' has the wrong type."
        new_config[key] = value
    state["portfolio"] = clean_portfolio
    state["transactions"] = clean_transactions
    state["config"].update(new_config)
    save_portfolio(clean_portfolio, state_dir(state))
    save_transactions(clean_transactions, state_dir(state))
    save_config(state["config"], state_dir(state))
    return True, (f"Restored {len(clean_portfolio)} holdings and {len(clean_transactions)} "
                  f"transactions from the backup made {data.get('created', 'earlier')}.")


# ============================================================================
# DATED HISTORY: closed trades, re-dating, and the returns timeline
# ============================================================================
def parse_date(text):
    """'YYYY-MM-DD' -> date, or None if it isn't a valid date."""
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def price_to_gbp(state, price, price_ccy="GBP", fx_rate=None):
    """Convert a price in pounds, pence ('GBp') or US dollars ('USD') to pounds.
    fx_rate is dollars per pound (None = today's rate). Returns (gbp_price, error_or_None)."""
    if price_ccy == "GBp":
        return price / 100, None
    if price_ccy == "USD":
        rate = fx_rate or fetch_fx(state["cache"])[0]
        if not rate:
            return None, "No exchange rate available; enter the rate."
        return price / rate, None
    return price, None


def add_past_trade(state, ticker, shares, buy_price, buy_date, sell_price, sell_date,
                   buy_fees=0.0, sell_fees=0.0, notes=""):
    """Record a trade that is already finished (bought AND sold in the past) so its result
    appears in your returns and charts. Prices are GBP per share before fees. This does not
    change your current holdings or cash. Returns (ok, message)."""
    ticker = (ticker or "").strip().upper()
    if not valid_ticker(ticker):
        return False, f"Ticker '{ticker}' isn't valid. Use 1-10 letters or numbers, e.g. BP.L or AAPL."
    if not (0 < shares <= MAX_SHARES):
        return False, f"Shares must be above 0 and at most {MAX_SHARES:,}. Got: {shares:g}"
    if buy_price <= 0 or sell_price <= 0:
        return False, "Prices must be above 0."
    if buy_fees < 0 or sell_fees < 0:
        return False, "Fees can't be negative."
    bought, sold = parse_date(buy_date), parse_date(sell_date)
    if bought is None or sold is None:
        return False, "Dates must look like 2026-03-14."
    if bought > sold:
        return False, f"The sell date ({sell_date}) can't be before the buy date ({buy_date})."
    if sold > now_utc().date():
        return False, f"The sell date ({sell_date}) is in the future."
    cost = shares * buy_price + buy_fees
    proceeds = shares * sell_price - sell_fees
    record_transaction(state, "BUY", ticker, shares, buy_price, buy_fees, notes, buy_date,
                       historical=True)
    record_transaction(state, "SELL", ticker, shares, sell_price, sell_fees, notes, sell_date,
                       cost_basis=cost, realised_pl=proceeds - cost, opened=buy_date,
                       historical=True)
    profit = proceeds - cost
    sign = "+" if profit >= 0 else "-"
    return True, (f"Recorded {shares:g} x {ticker}: bought {buy_date}, sold {sell_date}. Profit "
                  f"{sign}£{abs(profit):,.2f} ({profit / cost * 100:+.1f}%). Holdings and cash "
                  "are unchanged.")


def closed_positions(state):
    """Every sale as a row: when it was opened and closed, cost, proceeds, profit and return %."""
    rows = []
    for t in sorted(get_transactions(state), key=lambda t: (t["date"], t["id"]), reverse=True):
        if t["type"] != "SELL":
            continue
        cost, opened = t.get("cost_basis", 0.0), t.get("opened", "")
        start, end = parse_date(opened), parse_date(t["date"])
        rows.append({"id": t["id"], "ticker": t["ticker"], "shares": t["shares"],
                     "opened": opened, "closed": t["date"],
                     "days": (end - start).days if start and end else None,
                     "cost": cost, "proceeds": t["total"], "profit": t.get("realised_pl", 0.0),
                     "return_pct": (t.get("realised_pl", 0.0) / cost * 100) if cost else None,
                     "historical": bool(t.get("historical"))})
    return rows


def redate_transaction(state, tx_id, new_date):
    """Change the date of one transaction (to fix a purchase that defaulted to today).
    Keeps the holding's FIFO lots and 'first bought' date in step. Returns (ok, message)."""
    when = parse_date(new_date)
    if when is None:
        return False, "Dates must look like 2026-03-14."
    if when > now_utc().date():
        return False, f"{new_date} is in the future."
    transactions = get_transactions(state)
    tx = next((t for t in transactions if t.get("id") == tx_id), None)
    if tx is None:
        return False, f"Transaction {tx_id} not found."
    old_date = tx["date"]
    tx["date"] = new_date
    holding = next((h for h in state["portfolio"] if h["ticker"] == tx["ticker"]), None)
    if holding is not None and tx["type"] == "BUY" and not tx.get("historical"):
        for lot in holding.get("lots") or []:
            if lot.get("date") == old_date and abs(lot["shares"] - tx["shares"]) < 1e-6:
                lot["date"] = new_date
                break
        dates = [t["date"] for t in transactions if t["ticker"] == tx["ticker"]
                 and t["type"] == "BUY" and not t.get("historical")]
        holding["bought_on"] = min(dates)
        if holding.get("lots"):
            holding["lots"].sort(key=lambda l: l.get("date") or "")
        save_portfolio(state["portfolio"], state_dir(state))
    save_transactions(transactions, state_dir(state))
    return True, f"Changed the date of {tx['type']} {tx['ticker']} from {old_date} to {new_date}."


def set_buy_date(state, ticker, new_date):
    """Give a holding a buy date. Shares that aren't in the ledger (entered by hand or before
    dates existed) become a dated opening purchase; otherwise a single purchase is re-dated.
    Returns (ok, message)."""
    holding = next((h for h in state["portfolio"] if h["ticker"] == ticker), None)
    if holding is None:
        return False, f"{ticker} not found."
    when = parse_date(new_date)
    if when is None or when > now_utc().date():
        return False, "Enter a past date like 2026-03-14."
    mine = [t for t in get_transactions(state) if t["ticker"] == ticker and not t.get("historical")]
    net = sum(t["shares"] if t["type"] == "BUY" else -t["shares"] for t in mine)
    unexplained = holding["shares"] - net
    if unexplained > 1e-6:
        record_transaction(state, "BUY", ticker, unexplained, holding["avg_cost"], 0.0,
                           "opening balance (dated by you)", new_date)
        lots = ensure_lots(holding)
        if len(lots) == 1:
            lots[0]["date"] = new_date
        holding["bought_on"] = min(new_date, holding.get("bought_on") or new_date)
        save_portfolio(state["portfolio"], state_dir(state))
        return True, f"{ticker}: {unexplained:g} shares dated {new_date}."
    buys = [t for t in mine if t["type"] == "BUY"]
    if len(buys) == 1:
        return redate_transaction(state, buys[0]["id"], new_date)
    return False, (f"{ticker} has {len(buys)} purchases. Change each one's date in the History "
                   "tab so the right shares get the right date.")


def portfolio_timeline(state, range_key="1Y"):
    """Value, money invested, profit and return % through time, built from the dated ledger.

    Shares held on each day come from your BUY and SELL dates; shares that aren't in the ledger
    (entered by hand) are treated as held from the start, like the old what-if chart. Past
    closed trades are included, so past results show up. Return % is total profit (realised +
    unrealised) divided by everything bought so far (simple, not time-weighted).

    Returns {"dates", "value", "invested" (net money in), "profit", "return_pct", "skipped",
    "first_activity"} or None.
    """
    import pandas as pd
    transactions = get_transactions(state)
    holdings = {h["ticker"]: h for h in state["portfolio"]}
    tickers = sorted({t["ticker"] for t in transactions} | set(holdings))
    if not tickers:
        return None
    prices, skipped, fx_series = {}, [], None
    for ticker in tickers:
        data = fetch_history(state, ticker, range_key)
        if not data:
            skipped.append(ticker)
            continue
        series = pd.Series(data["close"], index=pd.to_datetime(data["dates"]))
        if data["currency"] == "USD":
            if fx_series is None:
                fx = fetch_history(state, "GBPUSD=X", range_key)
                if fx:
                    fx_series = pd.Series(fx["close"], index=pd.to_datetime(fx["dates"]))
            if fx_series is None:
                skipped.append(ticker)
                continue
            series = series / fx_series.reindex(series.index, method="ffill").bfill()
        elif data["currency"] != "GBP":
            skipped.append(ticker)
            continue
        prices[ticker] = series
    if not prices:
        return None
    frame = pd.DataFrame(prices).sort_index().ffill().bfill()
    index = frame.index
    shares = pd.DataFrame(0.0, index=index, columns=frame.columns)
    bought = pd.Series(0.0, index=index)
    sold = pd.Series(0.0, index=index)
    for ticker, holding in holdings.items():           # shares the ledger can't explain: held from the start
        if ticker not in frame.columns:
            continue
        net = sum(t["shares"] if t["type"] == "BUY" else -t["shares"]
                  for t in transactions if t["ticker"] == ticker and not t.get("historical"))
        opening = holding["shares"] - net
        if opening > 1e-6:
            shares[ticker] += opening
            bought += opening * holding["avg_cost"]
    for t in transactions:
        if t["ticker"] not in frame.columns:
            continue
        mask = index >= pd.Timestamp(t["date"])
        if t["type"] == "BUY":
            shares.loc[mask, t["ticker"]] += t["shares"]
            bought[mask] += t["total"]
        else:
            shares.loc[mask, t["ticker"]] -= t["shares"]
            sold[mask] += t["total"]
    value = (shares * frame).sum(axis=1)
    invested = bought - sold
    profit = value - invested
    return_pct = (profit / bought.where(bought > 0) * 100).fillna(0.0)
    active = bought > 0
    if not active.any():
        return None
    first = active.idxmax()
    keep = index >= first
    return {"dates": [d.strftime("%Y-%m-%d") for d in index[keep]],
            "value": [round(float(v), 2) for v in value[keep]],
            "invested": [round(float(v), 2) for v in invested[keep]],
            "profit": [round(float(v), 2) for v in profit[keep]],
            "return_pct": [round(float(v), 2) for v in return_pct[keep]],
            "skipped": skipped, "first_activity": first.strftime("%Y-%m-%d")}
