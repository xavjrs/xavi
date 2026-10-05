"""Shared test setup. Nothing here touches the internet: Yahoo Finance, Nasdaq and the news feeds
are replaced by small deterministic fakes, and every test gets its own empty data folder."""

import importlib
import importlib.util
import itertools
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
import requests
import yfinance

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
for folder in (WEB, ROOT):                      # isa_core.py / auth.py live in ROOT, xavi_charts.py in web/
    if folder not in sys.path:
        sys.path.insert(0, folder)

# ---------------------------------------------------------------------------------------------
# fake market data
# ---------------------------------------------------------------------------------------------
#            symbol: (currency, latest price in that currency, company name)
PRICES = {"AAPL": ("USD", 250.0, "Apple Inc."), "MSFT": ("USD", 400.0, "Microsoft Corporation"),
          "NVDA": ("USD", 180.0, "NVIDIA Corporation"), "BP.L": ("GBp", 550.0, "BP p.l.c."),
          "HSBA.L": ("GBp", 1400.0, "HSBC Holdings plc"), "GBPUSD=X": ("USD", 1.34, "GBP/USD"),
          "SPY": ("USD", 500.0, "SPDR S&P 500 ETF")}
INFO = {"AAPL": dict(sector="Technology", industry="Consumer Electronics", country="United States", marketCap=3.9e12,
                     trailingPE=31.0, forwardPE=28.0, beta=1.1, fiftyTwoWeekLow=190.0, fiftyTwoWeekHigh=270.0,
                     dividendRate=1.0, recommendationKey="buy", recommendationMean=2.2,
                     numberOfAnalystOpinions=44, targetMeanPrice=275.0, targetLowPrice=200.0, targetHighPrice=320.0),
        "BP.L": dict(sector="Energy", industry="Oil & Gas Integrated", country="United Kingdom", marketCap=8.0e10,
                     trailingPE=18.0, forwardPE=9.0, beta=0.6, fiftyTwoWeekLow=400.0, fiftyTwoWeekHigh=610.0,
                     dividendRate=25.0, recommendationKey="hold", recommendationMean=3.0,
                     numberOfAnalystOpinions=19, targetMeanPrice=600.0, targetLowPrice=500.0, targetHighPrice=700.0)}
SUMMARY = {"AAPL": dict(strongBuy=6, buy=19, hold=13, sell=3, strongSell=3),
           "BP.L": dict(strongBuy=2, buy=4, hold=10, sell=2, strongSell=1)}
NASDAQ = {"AAPL": dict(rating="Buy", analysts=29, buy=15, hold=9, sell=4, target=334.9, low=245.0, high=400.0),
          "MSFT": dict(rating="Strong Buy", analysts=40, buy=32, hold=1, sell=0, target=575.9, low=440.0, high=725.0)}
PERIOD_DAYS = {"1mo": 22, "3mo": 65, "6mo": 130, "1y": 252, "5y": 260, "max": 120}


def fake_series(symbol, period="1y", interval="1d"):
    """Business-day closes ending today and ending at the symbol's latest price (a gentle rise with a wiggle)."""
    currency, last, _ = PRICES.get(symbol, ("USD", 100.0, symbol))
    today = pd.Timestamp(date.today())
    if period == "ytd":
        count = max(len(pd.bdate_range(date(today.year, 1, 1), today)), 3)
    else:
        count = PERIOD_DAYS.get(period, 252)
    index = pd.bdate_range(end=today, periods=count)
    if interval in ("1wk", "1mo"):
        index = index[::5] if interval == "1wk" else index[::21]
        count = len(index)
    if index[-1] != today and today.weekday() < 5:
        index = index.append(pd.DatetimeIndex([today]))
        count = len(index)
    start = last * (1 - 0.25 * min(count, 252) / 252)        # longer ranges start lower, so each range has its own return
    close = [round(start + (last - start) * i / (count - 1) + (3 if i % 7 == 0 else 0) * last / 500
                   * (0 if i == count - 1 else 1), 4) for i in range(count)]
    close[-1] = last
    return pd.DataFrame({"Close": close, "Volume": [1_000_000 + 1000 * i for i in range(count)]}, index=index)


class FakeSettings:
    """Switches the tests flip to imitate a host where Yahoo's session-key calls are blocked."""
    yahoo_blocked = False


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol
        self.known = symbol in PRICES

    @property
    def fast_info(self):
        if not self.known:
            raise KeyError("lastPrice")
        currency, last, _ = PRICES[self.symbol]
        return {"lastPrice": last, "currency": currency}

    def history(self, period="1mo", interval="1d", auto_adjust=True, **kwargs):
        if not self.known:
            return pd.DataFrame({"Close": [], "Volume": []})
        return fake_series(self.symbol, period, interval)

    @property
    def info(self):
        if FakeSettings.yahoo_blocked or not self.known:
            return {}
        currency, _, name = PRICES[self.symbol]
        base = dict(shortName=name, longName=name, currency=currency, fullExchangeName="NasdaqGS",
                    quoteType="EQUITY")
        base.update(INFO.get(self.symbol, {}))
        return base

    @property
    def calendar(self):
        return {"Earnings Date": [date.today() + timedelta(days=24)]} if self.known else {}

    @property
    def recommendations_summary(self):
        if FakeSettings.yahoo_blocked or self.symbol not in SUMMARY:
            return pd.DataFrame()
        return pd.DataFrame([dict(period="0m", **SUMMARY[self.symbol])])

    def get_history_metadata(self):
        if not self.known:
            raise ValueError("no metadata")
        currency, last, name = PRICES[self.symbol]
        return {"currency": currency, "shortName": name, "longName": name, "fullExchangeName": "NasdaqGS",
                "instrumentType": "EQUITY", "fiftyTwoWeekLow": last * 0.75, "fiftyTwoWeekHigh": last * 1.1}


class FakeSearch:
    def __init__(self, *args, **kwargs):
        self.news = []


class FakeResponse:
    def __init__(self, status=200, body=b"", payload=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.text = self.content.decode("utf-8", "replace")
        self._payload = payload

    def json(self):
        return self._payload if self._payload is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def rss(items):
    entries = "".join(
        f"<item><title>{title}</title><link>{link}</link><pubDate>{when}</pubDate>{extra}</item>"
        for title, link, when, extra in items)
    return f'<?xml version="1.0"?><rss version="2.0" xmlns:News="https://www.bing.com/news/search"><channel>{entries}</channel></rss>'


def fake_get(url, params=None, headers=None, timeout=None, **kwargs):
    """Stands in for requests.get: answers the handful of public endpoints XAVI calls."""
    params = params or {}
    stamp = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    names = {symbol: data[2] for symbol, data in PRICES.items()}
    if "api.nasdaq.com" in url:
        match = re.search(r"/analyst/([A-Za-z]+)/(ratings|targetprice)", url)
        data = NASDAQ.get(match.group(1).upper()) if match else None
        if not data:
            return FakeResponse(200, payload={"data": None, "status": {"rCode": 400}})
        if match.group(2) == "ratings":
            return FakeResponse(200, payload={"data": {"meanRatingType": data["rating"],
                                                       "ratingsSummary": f"Based on {data['analysts']} analysts offering recommendations for 'X'."}})
        return FakeResponse(200, payload={"data": {"consensusOverview": {
            "priceTarget": data["target"], "lowPriceTarget": data["low"], "highPriceTarget": data["high"],
            "buy": data["buy"], "hold": data["hold"], "sell": data["sell"]}}})
    if "fundamentals-timeseries" in url:
        symbol = url.rsplit("/", 1)[-1]
        if symbol not in PRICES:
            return FakeResponse(200, payload={"timeseries": {"result": None}})

        def block(kind, value):
            return {"meta": {"type": [kind]}, kind: [{"reportedValue": {"raw": value}}]}
        return FakeResponse(200, payload={"timeseries": {"result": [
            block("trailingMarketCap", 3.5e12), block("trailingPeRatio", 29.5), block("trailingForwardPeRatio", 26.5)]}})
    if "news.google.com" in url or "bing.com/news" in url:
        query = params.get("q", "")
        company = next((n for n in names.values() if n.split()[0].lower() in query.lower()), None)
        if "news.google.com" in url or not company:
            return FakeResponse(200, rss([]))
        first = company.split()[0]
        extra = "<News:Source>Reuters</News:Source>"
        return FakeResponse(200, rss([
            (f"{first} reports record quarterly earnings and raises guidance", "http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fexample.com%2fa", stamp, extra),
            (f"{first} faces antitrust investigation, shares fall", "http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fexample.com%2fb", stamp, extra)]))
    if "feeds.finance.yahoo.com" in url:
        symbol = params.get("s", "")
        company = names.get(symbol)
        if not company:
            return FakeResponse(200, rss([]))
        return FakeResponse(200, rss([(f"{company.split()[0]} announces new dividend and buyback", "https://example.com/c", stamp, "")]))
    raise requests.ConnectionError(f"offline in tests: {url}")


# ---------------------------------------------------------------------------------------------
# loading a fresh copy of the web app
# ---------------------------------------------------------------------------------------------
_counter = itertools.count()


def load_core(monkeypatch, tmp_path, yahoo_blocked=False):
    """Import isa_core fresh, with the fake network in place and its cache / log files in tmp_path."""
    data = tmp_path / "data"
    monkeypatch.setenv("XAVI_DATA_DIR", str(data))
    monkeypatch.setenv("ISA_USERS_DIR", str(data / "accounts"))
    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    monkeypatch.setattr(yfinance, "Search", FakeSearch)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(FakeSettings, "yahoo_blocked", yahoo_blocked)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    core = importlib.import_module("isa_core")
    importlib.reload(core)
    auth = importlib.import_module("auth")
    importlib.reload(auth)
    monkeypatch.setattr(core, "CACHE_FILE", str(tmp_path / "cache.json"))
    monkeypatch.setattr(core, "LOG_FILE", str(tmp_path / "log.txt"))
    return core


def load_web_app(monkeypatch, tmp_path, demo, yahoo_blocked=False, **env):
    """Import web/app.py fresh, with its own empty data folder and the fake network in place."""
    monkeypatch.setenv("XAVI_DEMO", "1" if demo else "0")
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    load_core(monkeypatch, tmp_path, yahoo_blocked)
    spec = importlib.util.spec_from_file_location(f"xavi_web_app_{next(_counter)}", os.path.join(WEB, "app.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module               # Flask finds templates/ and static/ from the module's file
    spec.loader.exec_module(module)
    module.app.config["TESTING"] = True
    return module


def client_for(module, ip="10.0.0.1"):
    client = module.app.test_client()
    client.environ_base["REMOTE_ADDR"] = ip
    return client


def csrf_of(html):
    match = re.search(r'name="_csrf" value="([0-9a-f]+)"', html) or re.search(r'data-csrf="([0-9a-f]+)"', html)
    return match.group(1) if match else ""


@pytest.fixture
def demo_app(monkeypatch, tmp_path):
    return load_web_app(monkeypatch, tmp_path, demo=True)


@pytest.fixture
def demo_app_blocked(monkeypatch, tmp_path):
    """Demo mode on a host where Yahoo refuses its session key (like the free Render server)."""
    return load_web_app(monkeypatch, tmp_path, demo=True, yahoo_blocked=True)


@pytest.fixture
def account_app(monkeypatch, tmp_path):
    return load_web_app(monkeypatch, tmp_path, demo=False)


@pytest.fixture
def core(monkeypatch, tmp_path):
    return load_core(monkeypatch, tmp_path)


@pytest.fixture
def core_blocked(monkeypatch, tmp_path):
    return load_core(monkeypatch, tmp_path, yahoo_blocked=True)


@pytest.fixture
def web():
    """Helpers for tests: SimpleNamespace(csrf_of, client_for)."""
    return SimpleNamespace(csrf_of=csrf_of, client_for=client_for)
