"""Small server-side SVG charts for the XAVI web app.

They are drawn with CSS variables (var(--accent) etc.), so they follow the page's light / dark
theme instantly. Hover tooltips are added by a few lines of JavaScript in base.html that read
the data-chart attribute written here.
"""

import json
import math
from datetime import datetime

from markupsafe import Markup, escape

WIDTH = 1000
PAD_L, PAD_R, PAD_T, PAD_B = 8, 74, 12, 28


def nice_ticks(low, high, target=5):
    """Round-number tick values covering low..high."""
    if high <= low:
        high = low + 1
    span = high - low
    magnitude = 10 ** math.floor(math.log10(span / target))
    step = magnitude
    for multiple in (1, 2, 2.5, 5, 10):
        step = multiple * magnitude
        if span / step <= target + 1:
            break
    value = math.floor(low / step) * step
    ticks = []
    while value <= high + step * 0.001:
        if value >= low - step * 0.001:
            ticks.append(round(value, 10))
        value += step
    return ticks


def fmt_axis(value, kind):
    """Axis label text for a value."""
    if kind == "pct":
        return f"{value:.0f}%" if abs(value) >= 10 else f"{value:.1f}%"
    if kind == "volume":
        for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(value) >= limit:
                return f"{value / limit:.1f}{suffix}".replace(".0", "")
        return f"{value:.0f}"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}"


def date_labels(dates, count=6):
    """(index, label) pairs spread along the x axis."""
    n = len(dates)
    if n < 2:
        return []
    first = datetime.strptime(dates[0], "%Y-%m-%d")
    last = datetime.strptime(dates[-1], "%Y-%m-%d")
    long_span = (last - first).days > 300
    out = []
    for k in range(count):
        i = round(k * (n - 1) / (count - 1))
        d = datetime.strptime(dates[i], "%Y-%m-%d")
        out.append((i, d.strftime("%b %Y") if long_span else f"{d.day} {d:%b}"))
    return out


def _wrap(svg_body, height, xs, tips, label, ys=None):
    """Put the svg and tooltip container together. ys = y positions for the hover dot."""
    data = json.dumps({"xs": xs, "tips": tips, "w": WIDTH, "h": height})
    ys_attr = f' data-ys="{escape(json.dumps(ys))}"' if ys else ""
    return Markup(
        f'<div class="chart-wrap" data-chart="{escape(data)}"{ys_attr}>'
        f'<svg class="chart" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{escape(label)}">'
        f'{svg_body}'
        f'<line class="xhair" x1="0" y1="{PAD_T}" x2="0" y2="{height - PAD_B}" visibility="hidden"/>'
        f'<circle class="dot" r="4.5" cx="0" cy="0" visibility="hidden"/></svg>'
        f'<div class="chart-tip" hidden></div></div>')


def line_chart(dates, values, tips, *, height=300, kind="money", include_zero=False,
               guide=None, area=True, colour="var(--accent)", label="chart"):
    """A line (optionally filled) chart with a right-hand axis.

    guide: optional second series drawn as a dashed step line (e.g. money invested).
    tips: one short text per point, shown on hover.
    """
    n = len(values)
    if n < 2:
        return Markup('<div class="empty">Not enough data to draw a chart yet.</div>')
    series = values + (guide or [])
    low, high = min(series), max(series)
    if include_zero:
        low, high = min(low, 0), max(high, 0)
    pad = (high - low) * 0.06 or max(abs(high) * 0.05, 1)
    low, high = low - pad, high + pad
    plot_w, plot_h = WIDTH - PAD_L - PAD_R, height - PAD_T - PAD_B

    def x(i):
        return PAD_L + i * plot_w / (n - 1)

    def y(v):
        return PAD_T + (high - v) / (high - low) * plot_h

    parts = []
    for tick in nice_ticks(low, high):
        ty = y(tick)
        parts.append(f'<line class="grid" x1="{PAD_L}" x2="{WIDTH - PAD_R}" y1="{ty:.1f}" y2="{ty:.1f}"/>'
                     f'<text class="ylab" x="{WIDTH - PAD_R + 8}" y="{ty + 4:.1f}">{escape(fmt_axis(tick, kind))}</text>')
    for i, text in date_labels(dates):
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        parts.append(f'<text class="xlab" x="{x(i):.1f}" y="{height - 8}" text-anchor="{anchor}">{escape(text)}</text>')
    if include_zero and low < 0 < high:
        parts.append(f'<line class="zero" x1="{PAD_L}" x2="{WIDTH - PAD_R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    line = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    if area:
        base = y(0) if (include_zero and low < 0 < high) else PAD_T + plot_h
        parts.append(f'<path class="area" d="{line} L{x(n - 1):.1f},{base:.1f} L{x(0):.1f},{base:.1f} Z" '
                     f'style="fill:{colour}"/>')
    if guide:
        step = f"M{x(0):.1f},{y(guide[0]):.1f}"
        for i in range(1, n):
            step += f" L{x(i):.1f},{y(guide[i - 1]):.1f} L{x(i):.1f},{y(guide[i]):.1f}"
        parts.append(f'<path class="guide" d="{step}"/>')
    parts.append(f'<path class="line" d="{line}" style="stroke:{colour}"/>')
    xs = [round(x(i), 1) for i in range(n)]
    ys = [round(y(v), 1) for v in values]
    return _wrap("".join(parts), height, xs, [f"{t}" for t in tips], label, ys)


def bar_chart(dates, values, tips, *, height=80, kind="volume", label="bars"):
    """Vertical bars (for volume)."""
    n = len(values)
    if n < 2:
        return Markup("")
    high = max(values) * 1.08 or 1
    plot_w, plot_h = WIDTH - PAD_L - PAD_R, height - PAD_T - 6
    bar_w = max(plot_w / n * 0.7, 1)
    parts = []
    for tick in nice_ticks(0, high, 3):
        ty = PAD_T + (high - tick) / high * plot_h
        parts.append(f'<line class="grid" x1="{PAD_L}" x2="{WIDTH - PAD_R}" y1="{ty:.1f}" y2="{ty:.1f}"/>'
                     f'<text class="ylab" x="{WIDTH - PAD_R + 8}" y="{ty + 4:.1f}">{escape(fmt_axis(tick, kind))}</text>')
    xs = []
    for i, v in enumerate(values):
        cx = PAD_L + i * plot_w / (n - 1)
        h = v / high * plot_h
        xs.append(round(cx, 1))
        parts.append(f'<rect class="bar" x="{cx - bar_w / 2:.1f}" y="{PAD_T + plot_h - h:.1f}" '
                     f'width="{bar_w:.1f}" height="{max(h, 0.5):.1f}"/>')
    data = json.dumps({"xs": xs, "tips": tips, "w": WIDTH, "h": height})
    return Markup(f'<div class="chart-wrap" data-chart="{escape(data)}" data-nodot="1">'
                  f'<svg class="chart" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{escape(label)}">'
                  f'{"".join(parts)}<line class="xhair" x1="0" y1="{PAD_T}" x2="0" y2="{height - 6}" '
                  f'visibility="hidden"/></svg><div class="chart-tip" hidden></div></div>')
