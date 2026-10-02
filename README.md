# XAVI
### Personal ISA Portfolio Dashboard

A terminal app to track your UK investment portfolio in real-time. Holdings, diversification, news, analysis — all from the command line.

**Built by a 17-year-old founder learning Python & AI to build financial tools.**

---

## What it does

- **Portfolio overview**: Total value, P&L, holdings breakdown, ISA allowance tracking
- **Diversification analysis**: HHI score, sector/region exposure, concentration warnings
- **Live news**: Filtered headlines for your holdings (Google News + Yahoo Finance)
- **Company research**: Price charts, analyst consensus, earnings dates
- **Transaction history**: Track buys/sells with cost basis and realized P&L
- **Backup/export**: CSV export + full-data restore

---

## Quick start

```bash
# Install
pip install yfinance requests bcrypt

# Run
python portfolio_tracker.py
```

Create account → add your holdings → done.

---

## Features

- ✅ Login (username/password, bcrypt hashing)
- ✅ 5 tabs: Portfolio, Analysis, News, Security, Settings
- ✅ Edit positions inline (shares, cost, sector, region)
- ✅ Add past trades (backdate to 2020, any date)
- ✅ Sell holdings with realized P&L tracking
- ✅ Real prices (yfinance, cached 60 min)
- ✅ Stale price warnings
- ✅ CSV export & full backup/restore
- ✅ Green terminal UI (dark theme)

---

## Data

- UK Stocks and Shares ISA (or any account: SIPP, GIA)
- Data stored locally (~/users/<username>/)
- Passwords hashed (never stored plain)
- No cloud sync (one machine only, for now)

---

## Known limits

- Not financial advice
- Yahoo Finance data delayed (up to 15 min)
- Prices personal use only (can't resell product without licensed data)
- Balance chart is "what-if" for current holdings, not real history
- Funds not looked through (display single sector label)

---

## Roadmap

**Phase 2** (Weeks 3-6): Web app (Flask + SQLite, same UX)  
**Phase 3** (Weeks 7-12): Pick one:
- AI earnings advisor (parse transcripts, predict beats)
- Monte Carlo simulator (portfolio projections)

---

## Why I built this

I'm learning Python by building real products, not tutorials. XAVI is the first of 3 projects over 4-6 months. It's a tool I actually use daily, and it's sellable as a founder's MVP.

Started as a Claude Code experiment, now it's a real product with login, data persistence, and production-quality UX.

---

## Files

- `portfolio_tracker.py` — Main terminal app (500+ lines)
- `isa_core.py` — Shared data logic (calculations, news, prices)
- `auth.py` — Login & password hashing
- `requirements.txt` — Dependencies

---

## Future

- Web version (Flask, responsive)
- Dark/light themes
- Watchlist (track tickers you don't own)
- Earnings alerts
- Monte Carlo projections

---

**Status**: v1.0 complete. Ready for production use.

Questions? Open an issue or fork it.
