"""Public demo mode (XAVI_DEMO=1): private sandboxes, no accounts, limits against abuse."""

import json
import os
import re
import time

import pytest

from conftest import client_for, csrf_of, load_web_app


def sandbox_id(client):
    with client.session_transaction() as s:
        return s.get("sandbox")


def test_front_door_has_no_login(demo_app):
    c = client_for(demo_app)
    assert c.get("/").headers["Location"].endswith("/dashboard")
    assert c.get("/login").status_code == 302
    assert c.get("/signup").status_code == 302


def test_dashboard_shows_sample_portfolio_and_demo_ui(demo_app):
    html = client_for(demo_app).get("/dashboard").get_data(as_text=True)
    for ticker in ("AAPL", "MSFT", "BP.L", "HSBA.L"):
        assert ticker in html
    assert "Live demo." in html and "30 minutes" in html
    assert "Reset sample" in html and "Log out" not in html


def test_each_visitor_gets_a_private_sandbox_and_no_account_files(demo_app):
    a, b = client_for(demo_app, "10.0.0.1"), client_for(demo_app, "10.0.0.2")
    a.get("/dashboard"), b.get("/dashboard")
    sid_a, sid_b = sandbox_id(a), sandbox_id(b)
    assert re.fullmatch(r"[0-9a-f]{16}", sid_a) and sid_a != sid_b
    folder = os.path.join(demo_app.SANDBOX_DIR, sid_a)
    portfolio = json.load(open(os.path.join(folder, "portfolio.json"), encoding="utf-8"))
    assert len(portfolio) == 4
    assert not os.path.exists(os.path.join(demo_app.DATA_DIR, "accounts", "users.json"))


def test_changes_are_private_and_reset_restores_the_sample(demo_app):
    a, b = client_for(demo_app, "10.0.0.1"), client_for(demo_app, "10.0.0.2")
    html = a.get("/dashboard").get_data(as_text=True)
    reply = a.post("/holdings/add", json={"ticker": "NVDA", "shares": 2, "price": 100, "currency": "GBP"},
                   headers={"X-CSRF-Token": csrf_of(html)})
    assert reply.status_code == 200 and reply.get_json()["success"]
    assert "NVDA" in a.get("/dashboard").get_data(as_text=True)
    assert "NVDA" not in b.get("/dashboard").get_data(as_text=True)          # other visitor can't see it
    old = os.path.join(demo_app.SANDBOX_DIR, sandbox_id(a))
    page = a.post("/demo/reset", data={"_csrf": csrf_of(a.get("/dashboard").get_data(as_text=True))},
                  follow_redirects=True).get_data(as_text=True)
    assert "Sample portfolio restored" in page and "NVDA" not in page
    assert not os.path.isdir(old)                                              # old sandbox deleted


def test_posts_need_a_csrf_token(demo_app):
    c = client_for(demo_app)
    c.get("/dashboard")
    assert c.post("/holdings/add", json={"ticker": "NVDA", "shares": 1, "price": 1}).status_code == 400


@pytest.mark.parametrize("path", ["/settings/keys", "/settings/password", "/settings/delete"])
def test_account_settings_are_switched_off(demo_app, path):
    c = client_for(demo_app)
    html = c.get("/dashboard").get_data(as_text=True)
    assert c.post(path, data={"_csrf": csrf_of(html)}).status_code == 404
    settings = c.get("/settings").get_data(as_text=True)
    assert "NewsAPI key" not in settings and "Change password" not in settings and "Delete my account" not in settings


def test_forged_sandbox_id_is_replaced_not_used(demo_app):
    c = client_for(demo_app)
    c.get("/dashboard")
    with c.session_transaction() as s:
        s["sandbox"] = "../../../etc"
    c.get("/dashboard")
    assert re.fullmatch(r"[0-9a-f]{16}", sandbox_id(c))


def test_demo_is_full_at_the_sandbox_cap_and_frees_up_after_expiry(monkeypatch, tmp_path):
    app = load_web_app(monkeypatch, tmp_path, demo=True, XAVI_MAX_SANDBOXES=2)
    clients = [client_for(app, f"10.0.0.{i}") for i in range(1, 4)]
    assert clients[0].get("/dashboard").status_code == 200
    assert clients[1].get("/dashboard").status_code == 200
    full = clients[2].get("/dashboard")
    assert full.status_code == 503 and "demo is full" in full.get_data(as_text=True)
    old = time.time() - 31 * 60
    for name in os.listdir(app.SANDBOX_DIR):
        os.utime(os.path.join(app.SANDBOX_DIR, name), (old, old))
    assert clients[2].get("/dashboard").status_code == 200                      # idle sandboxes were cleared
    page = clients[0].get("/dashboard").get_data(as_text=True)
    assert "timed out" in page                                                  # the expired visitor is told


def test_ticker_lookups_are_rate_limited(demo_app):
    c = client_for(demo_app)
    codes = [c.get("/security?ticker=ZZZZQ").status_code for _ in range(25)]
    assert 429 in codes and 429 not in codes[:20]


def test_holding_cap(demo_app):
    c = client_for(demo_app)
    html = c.get("/dashboard").get_data(as_text=True)
    headers = {"X-CSRF-Token": csrf_of(html)}
    results = []
    for i in range(25):
        results.append(c.post("/holdings/add", json={"ticker": f"T{i}", "shares": 1, "price": 1, "allow_unlisted": True},
                              headers=headers))
    assert any(r.status_code == 400 and "limited to" in r.get_json()["error"] for r in results)


@pytest.mark.parametrize("path,text", [("/history", "Transaction history"), ("/analysis", "Diversification"),
                                       ("/news", "News feed"), ("/security?ticker=AAPL", "Analyst consensus"),
                                       ("/settings", "Reset everything to zero")])
def test_every_page_renders(demo_app, path, text):
    r = client_for(demo_app).get(path)
    assert r.status_code == 200 and text in r.get_data(as_text=True)


def test_secure_cookie_flag_follows_the_https_setting(monkeypatch, tmp_path):
    assert load_web_app(monkeypatch, tmp_path, demo=True).app.config["SESSION_COOKIE_SECURE"] is False
    hosted = load_web_app(monkeypatch, tmp_path / "x", demo=True, XAVI_HTTPS=1)
    assert hosted.app.config["SESSION_COOKIE_SECURE"] is True
