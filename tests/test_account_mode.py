"""Normal mode: accounts with passwords, the full add / sell / history flow."""

import pytest

from conftest import client_for, csrf_of


def sign_up_and_log_in(c, name="alice", password="correct-horse-1"):
    page = c.get("/signup").get_data(as_text=True)
    c.post("/signup", data={"username": name, "password": password, "password_confirm": password,
                            "understood": "1", "_csrf": csrf_of(page)})
    return c.post("/login", data={"username": name, "password": password,
                                  "_csrf": csrf_of(c.get("/login").get_data(as_text=True))}, follow_redirects=True)


def test_login_page_is_mobile_ready(account_app):
    html = client_for(account_app).get("/login").get_data(as_text=True)
    assert "width=device-width, initial-scale=1" in html and "width:400px" not in html


def test_logged_out_visitors_are_sent_to_login(account_app):
    c = client_for(account_app)
    for path in ("/dashboard", "/history", "/settings"):
        assert c.get(path).status_code == 302
    assert c.get("/static/style.css").status_code == 200


def test_signup_login_and_empty_account(account_app):
    c = client_for(account_app)
    reply = sign_up_and_log_in(c)
    html = reply.get_data(as_text=True)
    assert reply.request.path == "/dashboard" and "Portfolio overview" in html
    assert "Your account is empty" in html
    assert 'id="menu-toggle"' in html and "Log out" in html and "Reset sample" not in html
    assert all(f'<span class="lbl">{name}</span>' in html for name in
               ("Dashboard", "History", "Analysis", "News", "Security", "Settings"))


def test_wrong_password_gets_a_generic_message(account_app):
    c = client_for(account_app)
    sign_up_and_log_in(c)
    c.post("/logout", data={"_csrf": csrf_of(c.get("/dashboard").get_data(as_text=True))})
    reply = c.post("/login", data={"username": "alice", "password": "nope-nope-1",
                                   "_csrf": csrf_of(c.get("/login").get_data(as_text=True))}, follow_redirects=True)
    assert "Incorrect username or password" in reply.get_data(as_text=True)


def test_posts_without_csrf_are_rejected(account_app):
    c = client_for(account_app)
    c.get("/login")
    assert c.post("/login", data={"username": "a", "password": "b"}).status_code == 400


@pytest.mark.parametrize("path,text", [("/history", "Transaction history"), ("/analysis", "Diversification"),
                                       ("/news", "News feed"), ("/security", "Security research"),
                                       ("/settings", "Password"), ("/dashboard?metric=value", "Portfolio overview")])
def test_pages_render_for_a_logged_in_user(account_app, path, text):
    c = client_for(account_app)
    sign_up_and_log_in(c)
    r = c.get(path)
    assert r.status_code == 200 and text in r.get_data(as_text=True)


def test_buy_sell_and_history_flow(account_app):
    c = client_for(account_app)
    sign_up_and_log_in(c)
    token = csrf_of(c.get("/dashboard").get_data(as_text=True))
    bad = c.post("/holdings/add", json={"ticker": "bad ticker!", "shares": 1, "price": 1}, headers={"X-CSRF-Token": token})
    assert bad.status_code == 400
    zero = c.post("/holdings/add", json={"ticker": "MSFT", "shares": 0, "price": 400}, headers={"X-CSRF-Token": token})
    assert zero.status_code == 400
    future = c.post("/holdings/add", json={"ticker": "MSFT", "shares": 1, "price": 1, "bought": "2999-01-01"},
                    headers={"X-CSRF-Token": token})
    assert future.status_code == 400
    unknown = c.post("/holdings/add", json={"ticker": "ZZZZQ", "shares": 1, "price": 5}, headers={"X-CSRF-Token": token})
    assert unknown.status_code == 400 and unknown.get_json().get("can_force")
    ok = c.post("/holdings/add", json={"ticker": "MSFT", "shares": 4, "price": 300, "currency": "GBP",
                                       "bought": "2025-12-01", "pay_cash": False}, headers={"X-CSRF-Token": token})
    assert ok.get_json()["success"]
    dashboard = c.get("/dashboard").get_data(as_text=True)
    assert "MSFT" in dashboard
    sold = c.post("/holdings/MSFT/sell", data={"_csrf": csrf_of(dashboard), "shares": "1", "price": "320",
                                               "currency": "GBP", "fees": "0", "date": "", "cash": "auto"},
                  follow_redirects=True)
    assert "Sold 1 x MSFT" in sold.get_data(as_text=True)
    history = c.get("/history").get_data(as_text=True)
    assert "2025-12-01" in history and "SELL" in history and "BUY" in history
    assert "Closed positions" in c.get("/dashboard?range=ALL").get_data(as_text=True)


def test_closed_trade_validation(account_app):
    c = client_for(account_app)
    sign_up_and_log_in(c)
    token = csrf_of(c.get("/history").get_data(as_text=True))
    good = {"_csrf": token, "ticker": "BP.L", "shares": "10", "buy_date": "2025-06-02", "sell_date": "2025-09-01",
            "buy_price": "4.20", "sell_price": "4.80", "currency": "GBP"}
    assert "Recorded 10 x BP.L" in c.post("/trades/closed", data=good, follow_redirects=True).get_data(as_text=True)
    backwards = dict(good, buy_date="2025-09-02", sell_date="2025-06-01")
    assert "before the buy date" in c.post("/trades/closed", data=backwards, follow_redirects=True).get_data(as_text=True)


def test_exports_do_not_leak_api_keys(account_app):
    c = client_for(account_app)
    sign_up_and_log_in(c)
    assert c.get("/export/holdings.csv").status_code == 200
    backup = c.get("/export/backup.json")
    assert backup.status_code == 200 and "api_keys" not in backup.get_data(as_text=True)
