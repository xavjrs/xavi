"""The shared logic in isa_core.py: money maths, FIFO sells, analyst consensus, news sources, timelines."""

import pytest
import requests

from conftest import PRICES, fake_series, rss


def make_state(core, tmp_path, holdings=(), **config):
    """A state dict like the apps build: portfolio + cache + config, stored under tmp_path."""
    folder = str(tmp_path / "user")
    state = {"portfolio": [], "cache": {}, "config": core.load_config(folder), "dir": folder}
    state["config"].update(config)
    for ticker, shares, cost, bought in holdings:
        ok, message = core.add_position(state, ticker, shares, cost, "", False, bought, skip_price_check=True)
        assert ok, message
        core.update_position(state, ticker, name=PRICES[ticker][2])
    return state


# ---- small pure functions -------------------------------------------------------------------
@pytest.mark.parametrize("score,label", [(1.0, "Strong buy"), (1.5, "Strong buy"), (1.51, "Buy"), (2.5, "Buy"),
                                         (2.51, "Hold"), (3.5, "Hold"), (3.51, "Sell"), (4.5, "Sell"),
                                         (4.51, "Strong sell"), (5.0, "Strong sell")])
def test_rating_label_boundaries(core, score, label):
    assert core.rating_label(score) == label


def test_to_gbp(core):
    assert core.to_gbp(550, "GBp", 1.34) == pytest.approx(5.5)
    assert core.to_gbp(134, "USD", 1.34) == pytest.approx(100)
    assert core.to_gbp(7, "GBP", 1.34) == 7
    assert core.to_gbp(1, "JPY", 1.34) is None and core.to_gbp(1, "USD", None) is None


def test_diversification_score(core):
    assert core.diversification_score([100]) == (0, pytest.approx(1.0))
    score, effective = core.diversification_score([25, 25, 25, 25])
    assert score == 75 and effective == pytest.approx(4.0)
    assert core.diversification_score([]) == (0, 0.0)


def test_drawdown_series_is_zero_at_new_highs_and_negative_below(core):
    assert core.drawdown_series([10, 12, 9, 12, 15, 12]) == pytest.approx([0, 0, -25, 0, 0, -20])


# ---- buying and selling ----------------------------------------------------------------------
def test_fifo_sell_uses_oldest_shares_first_and_records_realised_profit(core, tmp_path):
    state = make_state(core, tmp_path, [("BP.L", 10, 4.0, "2025-01-10")], cash=0.0)
    core.add_position(state, "BP.L", 10, 6.0, "", False, "2025-06-10", skip_price_check=True)
    ok, message = core.sell_position(state, "BP.L", 10, price=7.0, sold_on="2025-09-01")
    assert ok, message
    sell = [t for t in core.get_transactions(state) if t["type"] == "SELL"][0]
    assert sell["realised_pl"] == pytest.approx(10 * (7.0 - 4.0))          # the cheaper, older lot went first
    assert state["portfolio"][0]["shares"] == pytest.approx(10)
    assert state["portfolio"][0]["avg_cost"] == pytest.approx(6.0)         # only the newer lot is left
    assert state["config"]["cash"] == pytest.approx(70.0)                  # proceeds added to cash


def test_selling_more_than_held_is_refused(core, tmp_path):
    state = make_state(core, tmp_path, [("AAPL", 5, 150.0, "2025-10-01")])
    ok, message = core.sell_position(state, "AAPL", 6, price=200.0)
    assert not ok and "at most" in message


def test_selling_everything_removes_the_holding_but_keeps_the_ledger(core, tmp_path):
    state = make_state(core, tmp_path, [("AAPL", 5, 150.0, "2025-10-01")])
    assert core.sell_position(state, "AAPL", 5, price=200.0)[0]
    assert state["portfolio"] == [] and len(core.get_transactions(state)) == 2


# ---- timeline --------------------------------------------------------------------------------
def test_timeline_values_follow_dated_buys_and_convert_dollars(core, tmp_path):
    from datetime import date, timedelta
    bought = (date.today() - timedelta(days=120)).isoformat()
    state = make_state(core, tmp_path, [("AAPL", 10, 190.0, bought), ("BP.L", 100, 4.5, bought)])
    timeline = core.portfolio_timeline(state, "1Y")
    assert timeline and timeline["first_activity"] >= bought[:7]
    expected_last = 10 * PRICES["AAPL"][1] / PRICES["GBPUSD=X"][1] + 100 * PRICES["BP.L"][1] / 100
    assert timeline["value"][-1] == pytest.approx(expected_last, rel=0.01)
    assert timeline["profit"][-1] == pytest.approx(timeline["value"][-1] - timeline["invested"][-1], abs=0.05)


# ---- analyst consensus -----------------------------------------------------------------------
def test_consensus_prefers_yahoos_mean_and_keeps_counts_in_gauge_order(core, tmp_path):
    state = make_state(core, tmp_path)
    info = core.fetch_company_info(state, "AAPL")
    result = core.analyst_consensus(state, "AAPL", info)
    assert result["label"] == "Buy" and result["score"] == pytest.approx(2.2) and result["source"] == "Yahoo Finance"
    assert result["counts"] == [("Strong sell", 3), ("Sell", 3), ("Hold", 13), ("Buy", 19), ("Strong buy", 6)]
    assert result["total"] == 44 and 60 <= result["position"] <= 80


def test_consensus_is_worked_out_from_counts_when_no_mean(core, tmp_path):
    state = make_state(core, tmp_path)
    result = core.analyst_consensus(state, "AAPL", {})
    assert result and result["score"] == pytest.approx((6 * 1 + 19 * 2 + 13 * 3 + 3 * 4 + 3 * 5) / 44)


def test_consensus_from_yahoos_key_when_nothing_else(core, tmp_path):
    state = make_state(core, tmp_path)
    result = core.analyst_consensus(state, "BP.L", {"recommendationKey": "underperform", "recommendationMean": 3.9})
    assert result and result["label"] == "Sell"


def test_consensus_is_none_without_any_data(core_blocked, tmp_path):
    state = make_state(core_blocked, tmp_path)
    assert core_blocked.analyst_consensus(state, "BP.L", {}) is None
    assert core_blocked.analyst_consensus(state, "SPY", {}) is None


def test_nasdaq_fallback_for_us_tickers_only(core_blocked, tmp_path):
    state = make_state(core_blocked, tmp_path)
    result = core_blocked.analyst_consensus(state, "AAPL", {})
    assert result["label"] == "Buy" and result["source"] == "Nasdaq.com" and result["total"] == 29
    assert result["counts"] == [("Sell", 4), ("Hold", 9), ("Buy", 15)]
    assert result["target_mean"] == pytest.approx(334.9)
    assert core_blocked.nasdaq_analyst(state, "BP.L") is None             # UK tickers are not covered
    assert core_blocked.analyst_consensus(state, "MSFT", {})["label"] == "Strong buy"


def test_nasdaq_failure_is_swallowed(core_blocked, tmp_path, monkeypatch):
    state = make_state(core_blocked, tmp_path)

    def broken(*args, **kwargs):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "get", broken)
    assert core_blocked.nasdaq_analyst(state, "AAPL") is None


# ---- company info fallback ---------------------------------------------------------------------
def test_company_info_falls_back_to_valuation_data_when_yahoo_blocks(core_blocked, tmp_path):
    state = make_state(core_blocked, tmp_path, [("AAPL", 1, 150.0, "2025-10-01")])
    state["portfolio"][0]["sector"] = "Technology"
    state["cache"].pop("info", None)           # adding the position already cached a partial record
    info = core_blocked.fetch_company_info(state, "AAPL")
    assert info["marketCap"] == 3.5e12 and info["trailingPE"] == 29.5 and info["forwardPE"] == 26.5
    assert info["sector"] == "Technology" and info["currency"] == "USD" and info["_partial"] is True


def test_unknown_ticker_has_no_market_cap(core_blocked, tmp_path):
    state = make_state(core_blocked, tmp_path)
    assert not core_blocked.fetch_company_info(state, "ZZZZQ").get("marketCap")


def test_partial_info_is_retried_but_full_info_is_kept(core, tmp_path):
    state = make_state(core, tmp_path)
    full = core.fetch_company_info(state, "AAPL")
    assert "_partial" not in full and "recommendationMean" in full
    state["cache"]["info"]["AAPL"]["timestamp"] = "2000-01-01T00:00:00Z"
    assert core.fetch_company_info(state, "AAPL")["marketCap"] == 3.9e12    # stale: refetched


# ---- news --------------------------------------------------------------------------------------
def test_rss_items_unwrap_bing_links_and_read_the_outlet_name(core):
    xml = rss([("Apple earnings beat", "http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fexample.com%2fx", "Mon, 05 Oct 2026 09:00:00 GMT",
                "<News:Source>Reuters</News:Source>"),
               ("No date here", "https://example.com/y", "not a date", "")])
    items = core._rss_items("AAPL", xml.encode(), "Bing News",
                            lambda node: next((c.text for c in node if c.tag.endswith("}Source")), ""))
    assert len(items) == 1                                             # the story without a valid date is dropped
    assert items[0]["url"] == "https://example.com/x" and items[0]["source"] == "Reuters"
    assert items[0]["published"] == "2026-10-05T09:00:00Z"


def test_news_still_arrives_when_google_news_is_blocked(core, tmp_path, monkeypatch):
    state = make_state(core, tmp_path, [("AAPL", 1, 150.0, "2025-10-01"), ("BP.L", 1, 4.0, "2025-10-01")])

    def blocked(ticker, query):
        raise requests.ConnectionError("429")
    monkeypatch.setattr(core, "news_from_google", blocked)
    items, note = core.fetch_news(state, force=True)
    assert {"AAPL", "BP.L"} <= {i["ticker"] for i in items}
    assert "Google News unavailable" in note
    assert all(i["url"].startswith("http") and "bing.com/news/apiclick" not in i["url"] for i in items)
    titles = " ".join(i["title"].lower() for i in items)
    assert "earnings" in titles and "investigation" in titles


def test_news_only_keeps_stories_that_name_the_company(core, tmp_path, monkeypatch):
    state = make_state(core, tmp_path, [("AAPL", 1, 150.0, "2025-10-01")])
    from datetime import datetime
    stamp = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")

    def unrelated(ticker, query):
        return core._rss_items(ticker, rss([("Local bakery wins award", "https://e.com/1", stamp, ""),
                                            ("Apple unveils a new phone", "https://e.com/2", stamp, "")]).encode(), "Test")
    monkeypatch.setattr(core, "news_from_google", unrelated)
    items, _ = core.fetch_news(state, force=True)
    titles = [i["title"] for i in items]
    assert "Apple unveils a new phone" in titles and "Local bakery wins award" not in titles


def test_fine_tuning_is_not_mistaken_for_a_regulatory_fine(core):
    from datetime import datetime
    base = {"ticker": "AAPL", "summary": "", "source": "Test", "published": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "url": "https://e.com", "kind": "news", "tier": 2}
    tuning = core.score_item(dict(base, title="Apple is fine-tuning its new AI model"))
    fined = core.score_item(dict(base, title="Apple fined by regulator after antitrust probe"))
    assert fined > tuning


def test_fake_series_helper_is_sane():
    closes = fake_series("AAPL", "1y")["Close"]
    assert len(closes) >= 250 and closes.iloc[-1] == PRICES["AAPL"][1]
