"""Security research page: returns follow the chosen range, the analyst gauge, and the fallbacks used
when Yahoo refuses its session key (as on the free Render host)."""

import re

import pytest

from conftest import PRICES, client_for, fake_series


def page(client, ticker="AAPL", rng="1Y"):
    return client.get(f"/security?ticker={ticker}&range={rng}").get_data(as_text=True)


def range_return(html):
    match = re.search(r'class="v (?:pos|neg)">\s*([+-][\d,.]+)%', html)
    return float(match.group(1).replace(",", "")) if match else None


def expected_return(symbol, period, interval="1d"):
    closes = fake_series(symbol, period, interval)["Close"]
    return (closes.iloc[-1] / closes.iloc[0] - 1) * 100


@pytest.mark.parametrize("symbol", ["AAPL", "BP.L"])
@pytest.mark.parametrize("rng,period", [("1M", "1mo"), ("3M", "3mo"), ("6M", "6mo"), ("1Y", "1y")])
def test_return_matches_the_price_history_for_the_chosen_range(demo_app, symbol, rng, period):
    html = page(client_for(demo_app), symbol, rng)
    assert range_return(html) == pytest.approx(expected_return(symbol, period), abs=0.01)
    assert f"{rng} price return" in html and "excludes dividends" in html


def test_toggling_the_range_changes_the_return_not_the_one_day_move(demo_app):
    c = client_for(demo_app)
    one_year, one_month = page(c, "AAPL", "1Y"), page(c, "AAPL", "1M")
    assert range_return(one_year) != range_return(one_month)
    day = lambda html: re.search(r'\(([+-][\d.]+)%\)</span><span class="ccy">1 day', html).group(1)
    closes = fake_series("AAPL", "1mo")["Close"]
    assert float(day(one_year)) == pytest.approx((closes.iloc[-1] / closes.iloc[-2] - 1) * 100, abs=0.01)
    assert day(one_year) == day(one_month)


def test_ytd_is_measured_from_the_last_close_of_last_year(demo_app):
    from datetime import date
    year_start = f"{date.today().year}-01-01"
    closes = fake_series("AAPL", "1y")["Close"]
    before = closes[closes.index < year_start]
    if before.empty:
        pytest.skip("fake history is shorter than a year-to-date window")
    expected = (closes.iloc[-1] / before.iloc[-1] - 1) * 100
    assert range_return(page(client_for(demo_app), "AAPL", "YTD")) == pytest.approx(expected, abs=0.01)


def test_analyst_gauge_from_yahoo_shows_all_five_counts(demo_app):
    html = page(client_for(demo_app), "AAPL")
    assert re.findall(r'class="seg seg\d on">([^<]+)<', html) == ["Buy"]
    counts = dict((name, int(n)) for n, name in re.findall(r"<b>(\d+)</b> (Strong buy|Buy|Hold|Sell|Strong sell)", html))
    assert counts == {"Strong buy": 6, "Buy": 19, "Hold": 13, "Sell": 3, "Strong sell": 3}
    assert "from Yahoo Finance" in html and "not a recommendation" in html
    assert re.search(r'class="marker" style="left: 6\d\.\d%', html)                # inside the Buy segment (60-80%)


def test_hold_is_highlighted_for_a_mid_score(demo_app):
    assert re.findall(r'class="seg seg\d on">([^<]+)<', page(client_for(demo_app), "BP.L")) == ["Hold"]


def test_nasdaq_fallback_for_us_tickers_when_yahoo_is_blocked(demo_app_blocked):
    c = client_for(demo_app_blocked)
    html = page(c, "AAPL")
    assert re.findall(r'class="seg seg\d on">([^<]+)<', html) == ["Buy"]
    assert "from Nasdaq.com" in html and "29 analysts" in html and "$334.90" in html
    assert re.findall(r'class="seg seg\d on">([^<]+)<', page(c, "MSFT")) == ["Strong buy"]


def test_uk_shares_explain_the_gap_in_the_online_demo_when_yahoo_is_blocked(demo_app_blocked):
    html = page(client_for(demo_app_blocked), "BP.L")
    assert "gauge-card" not in html and "UK-listed shares aren't available in the online demo" in html


def test_funds_have_no_analyst_gauge(demo_app):
    html = page(client_for(demo_app), "SPY")
    assert "No analyst ratings are available" in html and "Traceback" not in html


def test_market_cap_and_pe_fall_back_to_the_valuation_endpoint(demo_app_blocked):
    html = page(client_for(demo_app_blocked), "AAPL")
    assert "3.50T" in html                       # market cap from the fallback
    assert "29.5" in html and "26.5" in html    # trailing and forward P/E


def test_unknown_ticker_is_reported_not_crashed(demo_app):
    html = page(client_for(demo_app), "ZZZZQ")
    assert "No data found for" in html


def test_invalid_ticker_is_rejected(demo_app):
    assert "a valid ticker" in page(client_for(demo_app), "bad ticker!")


def test_every_range_renders_for_every_sample_holding(demo_app):
    visitor = 0
    for symbol in ("AAPL", "MSFT", "BP.L", "HSBA.L"):
        for rng in ("1M", "3M", "6M", "YTD", "1Y", "5Y", "ALL"):
            visitor += 1                                   # a new visitor each time, so the demo's rate limit stays out of the way
            html = page(client_for(demo_app, f"10.9.{visitor // 200}.{visitor % 200 + 1}"), symbol, rng)
            assert "Traceback" not in html and "Security research" in html
            assert 'class="chart-desktop"' in html and 'class="chart-mobile"' in html


def test_prices_table_has_the_fakes_for_the_tickers_used(demo_app):
    assert {"AAPL", "BP.L", "HSBA.L", "MSFT"} <= set(PRICES)
