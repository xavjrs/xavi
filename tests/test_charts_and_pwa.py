"""Server-drawn charts (desktop + phone widths) and the installable-app files."""

import json
import os
import re

import pytest

import xavi_charts as charts
from conftest import ROOT, client_for

DATES = [f"2026-01-{d:02d}" for d in range(1, 29)]
VALUES = [100 + d * 2 for d in range(28)]
TIPS = [f"tip {i}" for i in range(28)]


def spec_of(html):
    return json.loads(re.search(r'data-chart="([^"]+)"', html).group(1).replace("&#34;", '"').replace("&quot;", '"'))


def test_nice_ticks_are_round_and_cover_the_range():
    ticks = charts.nice_ticks(0, 97)
    assert ticks[0] == 0 and 4 <= len(ticks) <= 7 and all(0 <= t <= 97 for t in ticks)
    steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:])}
    assert len(steps) == 1 and steps.pop() in (10, 20, 25)                   # evenly spaced, round-number steps


@pytest.mark.parametrize("value,kind,text", [(1_500_000, "volume", "1.5M"), (2_000_000_000, "volume", "2B"),
                                             (12.34, "pct", "12%"), (1.5, "pct", "1.5%"), (1234.5, "money", "1,234"),
                                             (12.345, "money", "12.35"), (0, "volume", "0")])
def test_axis_labels(value, kind, text):
    assert charts.fmt_axis(value, kind) in (text, "1,235" if text == "1,234" else text)     # 1234.5 rounds either way


def test_line_chart_has_hover_data_for_every_point():
    html = str(charts.line_chart(DATES, VALUES, TIPS, kind="money"))
    spec = spec_of(html)
    assert spec["w"] == charts.WIDTH and len(spec["xs"]) == 28 and spec["tips"][3] == "tip 3"
    ys = json.loads(re.search(r'data-ys="([^"]+)"', html).group(1).replace("&#34;", '"'))
    assert len(ys) == 28 and all(0 <= y <= spec["h"] for y in ys)


def test_too_little_data_gives_a_message_not_a_broken_chart():
    assert "Not enough data" in str(charts.line_chart(["2026-01-01"], [1], ["x"]))


def test_responsive_draws_a_desktop_and_a_narrower_phone_version():
    html = str(charts.responsive(charts.line_chart, DATES, VALUES, TIPS, mobile_height=270, kind="money"))
    assert 'class="chart-desktop"' in html and 'class="chart-mobile"' in html
    desktop, phone = html.split('class="chart-mobile"')
    assert spec_of(desktop)["w"] == charts.WIDTH and spec_of(phone)["w"] == charts.MOBILE_WIDTH
    assert spec_of(phone)["h"] == 270
    assert phone.count('class="xlab"') < desktop.count('class="xlab"')       # fewer axis labels on the phone


def test_responsive_bar_chart_and_empty_data():
    assert 'class="chart-mobile"' in str(charts.responsive(charts.bar_chart, DATES, VALUES, TIPS, mobile_height=90))
    assert str(charts.responsive(charts.line_chart, ["2026-01-01"], [1], ["x"])).count("chart-mobile") == 0


# ---- installable app ---------------------------------------------------------------------------
def test_manifest_is_valid_and_its_icons_exist(demo_app):
    c = client_for(demo_app)
    r = c.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.mimetype == "application/manifest+json"
    manifest = r.get_json()
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/" and manifest["short_name"] == "XAVI"
    purposes = {(i["sizes"], i["purpose"]) for i in manifest["icons"]}
    assert ("192x192", "any") in purposes and ("512x512", "any") in purposes and ("512x512", "maskable") in purposes
    for icon in manifest["icons"]:
        assert c.get(icon["src"]).status_code == 200
        assert os.path.getsize(os.path.join(ROOT, "web", icon["src"].lstrip("/"))) > 500


def test_service_worker_is_served_from_the_root_and_never_cached(demo_app):
    r = client_for(demo_app).get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.mimetype
    assert r.headers["Service-Worker-Allowed"] == "/" and "no-cache" in r.headers["Cache-Control"]
    body = r.get_data(as_text=True)
    assert "addEventListener('fetch'" in body and "request.method !== 'GET'" in body      # never touches form posts


def test_pwa_files_do_not_use_up_demo_sandboxes_or_need_login(demo_app, account_app):
    c = client_for(demo_app)
    for path in ("/manifest.webmanifest", "/sw.js", "/offline"):
        assert c.get(path).status_code == 200
    assert not os.path.isdir(demo_app.SANDBOX_DIR) or not os.listdir(demo_app.SANDBOX_DIR)
    a = client_for(account_app)
    for path in ("/manifest.webmanifest", "/sw.js", "/offline"):
        assert a.get(path).status_code == 200                                # logged out, no redirect to login


def test_pages_link_the_manifest_and_icons(demo_app):
    html = client_for(demo_app).get("/dashboard").get_data(as_text=True)
    for needle in ('rel="manifest"', 'rel="apple-touch-icon"', "serviceWorker.register('/sw.js')",
                   'name="theme-color"', 'id="install-btn"'):
        assert needle in html


def test_offline_page_is_friendly(demo_app):
    html = client_for(demo_app).get("/offline").get_data(as_text=True)
    assert "You're offline" in html and "Try again" in html
