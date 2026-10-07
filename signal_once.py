"""
Free NSE candlestick signal bot -> Instant Pattern Alerts + 30-Min Market Recaps (GitHub Actions)
Data: yfinance | Risk:Reward = 1:2
NOT financial advice. Paper trade first.
"""
import os
import time
import calendar
import datetime as dt
import pandas as pd
import requests
import yfinance as yf
import pytz

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

INDICES = ["^NSEI", "^NSEBANK", "^BSESN"]

STOCKS = [
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS",
    "AXISBANK.NS", "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS",
    "BEL.NS", "BHARTIARTL.NS", "CIPLA.NS", "COALINDIA.NS", "DRREDDY.NS",
    "EICHERMOT.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS",
    "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS",
    "ICICIBANK.NS", "INDUSINDBK.NS", "INFY.NS", "ITC.NS", "JIOFIN.NS",
    "JSWSTEEL.NS", "KOTAKBANK.NS", "LT.NS", "M&M.NS", "MARUTI.NS",
    "NESTLEIND.NS", "NTPC.NS", "ONGC.NS", "POWERGRID.NS", "RELIANCE.NS",
    "SBILIFE.NS", "SBIN.NS", "SHRIRAMFIN.NS", "SUNPHARMA.NS",
    "TATACONSUM.NS", "TATAMOTORS.NS", "TATASTEEL.NS", "TCS.NS",
    "TECHM.NS", "TITAN.NS", "TRENT.NS", "ULTRACEMCO.NS", "WIPRO.NS",
]

WATCHLIST = INDICES + STOCKS

INTERVAL = "5m"          # Candle timeframe: 5m, 15m, 1h
RR = 2.0                 # Risk:Reward = 1:2
MAX_AGE_MIN = 10         # Alert only for candles closed in last 10m
LOOP_SLEEP_SEC = 60      # Loop re-check interval in seconds
STOP_TIME = (15, 30)     # Market closes at 3:30 PM IST
SUMMARY_EVERY_MIN = 30   # Market recap interval

# Option step and expiry mapping for indices
STEP = {"^NSEI": 50, "^NSEBANK": 100, "^BSESN": 100}
EXPIRY = {
    "^NSEI": ("weekly", 3),     # Nifty: Thursday (weekday index 3)
    "^BSESN": ("weekly", 4),    # Sensex: Friday (weekday index 4)
    "^NSEBANK": ("monthly", 3)  # Bank Nifty: Monthly Thursday
}

IST = pytz.timezone("Asia/Kolkata")
sent = set()             # Tracks sent signals to prevent duplicates
recent = []              # Signals queued since last 30-min summary
# ==========================================


def send(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("Telegram error:", e)


def body(c): return abs(c.Close - c.Open)
def rng(c): return c.High - c.Low
def green(c): return c.Close > c.Open
def red(c): return c.Close < c.Open


def detect(df):
    """Detects reversal candlestick patterns on the latest completed candle."""
    if len(df) < 3:
        return []
    c, p, pp = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    out = []

    # Bullish / Bearish Engulfing
    if red(p) and green(c) and c.Close >= p.Open and c.Open <= p.Close:
        out.append(("Bullish Engulfing", "BUY", min(c.Low, p.Low)))
    if green(p) and red(c) and c.Open >= p.Close and c.Close <= p.Open:
        out.append(("Bearish Engulfing", "SELL", max(c.High, p.High)))

    r = rng(c)
    if r > 0:
        lower = min(c.Open, c.Close) - c.Low
        upper = c.High - max(c.Open, c.Close)
        # Hammer / Shooting Star
        if lower >= 2 * body(c) and upper <= 0.3 * r and body(c) > 0:
            out.append(("Hammer", "BUY", c.Low))
        if upper >= 2 * body(c) and lower <= 0.3 * r and body(c) > 0:
            out.append(("Shooting Star", "SELL", c.High))

    # Morning / Evening Star
    if red(pp) and body(p) < 0.5 * body(pp) and green(c) and c.Close > (pp.Open + pp.Close) / 2:
        out.append(("Morning Star", "BUY", min(p.Low, c.Low)))
    if green(pp) and body(p) < 0.5 * body(pp) and red(c) and c.Close < (pp.Open + pp.Close) / 2:
        out.append(("Evening Star", "SELL", max(p.High, c.High)))

    return out


def last_completed(df, minutes):
    now = dt.datetime.now(IST)
    last_ts = df.index[-1].tz_convert(IST)
    if last_ts + dt.timedelta(minutes=minutes) > now:
        df = df.iloc[:-1]
    return df


def next_expiry(sym):
    kind, wd = EXPIRY.get(sym, ("weekly", 3))
    today = dt.datetime.now(IST).date()

    def last_wd(y, m):
        d = dt.date(y, m, calendar.monthrange(y, m)[1])
        while d.weekday() != wd:
            d -= dt.timedelta(days=1)
        return d

    if kind == "weekly":
        d = today + dt.timedelta(days=(wd - today.weekday()) % 7)
        if (d - today).days < 2:
            d += dt.timedelta(days=7)
    else:
        d = last_wd(today.year, today.month)
        if (d - today).days < 2:
            y = today.year + (today.month == 12)
            d = last_wd(y, today.month % 12 + 1)
    return d


def option_idea(sym, side, entry):
    if sym not in STEP:
        return ""
    st = STEP[sym]
    atm = round(entry / st) * st
    typ = "CE" if side == "BUY" else "PE"
    itm = atm - st if side == "BUY" else atm + st
    exp = next_expiry(sym).strftime("%d %b")
    return (f"\n\nOption idea: BUY {atm} {typ} (ATM)"
            f"\nSafer alt: {itm} {typ} (1 step ITM)"
            f"\nExpiry: {exp}")


def scan():
    mins = {"5m": 5, "15m": 15, "1h": 60}[INTERVAL]
    now = dt.datetime.now(IST)

    try:
        # Batch download all symbols to avoid sequential API overhead
        data = yf.download(WATCHLIST, period="2d", interval=INTERVAL,
                           group_by="ticker", progress=False, auto_adjust=False)
        if data.empty:
            return
    except Exception as e:
        print("Batch download error:", e)
        return

    for sym in WATCHLIST:
        try:
            df = data[sym].dropna() if len(WATCHLIST) > 1 else data.dropna()
            if df.empty or len(df) < 3:
                continue

            df = last_completed(df, mins)
            closed_at = df.index[-1].tz_convert(IST) + dt.timedelta(minutes=mins)
            if (now - closed_at).total_seconds() > MAX_AGE_MIN * 60:
                continue

            for name, side, stop in detect(df):
                entry = float(df.iloc[-1].Close)
                risk = abs(entry - stop)
                if risk <= 0:
                    continue

                key = (sym, str(df.index[-1]), name)
                if key in sent:
                    continue
                sent.add(key)

                target = entry + RR * risk if side == "BUY" else entry - RR * risk
                send(f"{'🟢' if side == 'BUY' else '🔴'} {side} {sym.replace('.NS', '')}\n"
                     f"Pattern: {name} ({INTERVAL})\n"
                     f"Entry: {entry:.2f}\nStop-loss: {stop:.2f}\n"
                     f"Target (1:{RR:g}): {target:.2f}\n"
                     f"Risk/share: {risk:.2f}"
                     + option_idea(sym, side, entry))
                recent.append(f"{side} {sym.replace('.NS', '')} ({name})")
        except Exception as e:
            print(f"Scan error for {sym}:", e)


def summary():
    """Compiles 30-minute market recap: Indices, top movers, and triggered signals."""
    now_str = dt.datetime.now(IST).strftime('%H:%M')
    lines = [f"⏱️ 30-MIN MARKET UPDATE ({now_str} IST)\n"]

    # 1. Major index performance
    lines.append("📈 Indices:")
    for sym, label in [("^NSEI", "Nifty 50"), ("^NSEBANK", "Bank Nifty"), ("^BSESN", "Sensex")]:
        try:
            d = yf.download(sym, period="1d", interval="5m", progress=False, auto_adjust=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            last = float(d.Close.iloc[-1])
            first = float(d.Open.iloc[0])
            chg = (last - first) / first * 100
            lines.append(f"• {label}: {last:.2f} ({chg:+.2f}%)")
        except Exception:
            lines.append(f"• {label}: n/a")

    # 2. Watchlist movers (Gainers & Losers)
    try:
        stock_data = yf.download(STOCKS, period="1d", interval="15m",
                                 group_by="ticker", progress=False, auto_adjust=False)
        movers = []
        for s in STOCKS:
            sdf = stock_data[s].dropna()
            if not sdf.empty and len(sdf) > 1:
                cur = float(sdf.Close.iloc[-1])
                opn = float(sdf.Open.iloc[0])
                pct = (cur - opn) / opn * 100
                movers.append((s.replace(".NS", ""), cur, pct))

        movers.sort(key=lambda x: x[2], reverse=True)
        top_gainers = movers[:3]
        top_losers = movers[-3:]

        lines.append("\n🚀 Top Gainers:")
        for name, price, pct in top_gainers:
            lines.append(f"• {name}: {price:.2f} ({pct:+.2f}%)")

        lines.append("\n🔻 Top Draggers:")
        for name, price, pct in reversed(top_losers):
            lines.append(f"• {name}: {price:.2f} ({pct:+.2f}%)")
    except Exception as e:
        print("Summary stocks error:", e)

    # 3. Candlestick patterns detected in this 30m window
    lines.append(f"\n🎯 Patterns Triggered (Last {SUMMARY_EVERY_MIN}m):")
    if recent:
        for r in recent[-10:]:
            lines.append(f"• {r}")
    else:
        lines.append("• None detected")

    send("\n".join(lines))
    recent.clear()


if __name__ == "__main__":
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event == "workflow_dispatch":
        send("✅ Test: bot is online and scanning.")

    last_sum = dt.datetime.now(IST)

    while dt.datetime.now(IST).time() < dt.time(*STOP_TIME):
        scan()

        # Check if 30 minutes have elapsed since the last recap
        if (dt.datetime.now(IST) - last_sum).total_seconds() >= SUMMARY_EVERY_MIN * 60:
            summary()
            last_sum = dt.datetime.now(IST)

        time.sleep(LOOP_SLEEP_SEC)
