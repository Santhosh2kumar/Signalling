"""
Free NSE candlestick signal bot -> Telegram alerts (runs on GitHub Actions)
Data: yfinance (free, may lag a minute or two) | Risk:Reward = 1:2
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
BOT_TOKEN = os.environ["BOT_TOKEN"]   # set in GitHub Secrets
CHAT_ID = os.environ["CHAT_ID"]       # set in GitHub Secrets

WATCHLIST = [
    # Indices
    "^NSEI", "^NSEBANK", "^BSESN",
    # Nifty 50 stocks
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS",
    "AXISBANK.NS", "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS",
    "BEL.NS", "BHARTIARTL.NS", "CIPLA.NS", "COALINDIA.NS", "DRREDDY.NS",
    "EICHERMOT.NS", "ETERNAL.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS",
    "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS",
    "ICICIBANK.NS", "INDUSINDBK.NS", "INFY.NS", "ITC.NS", "JIOFIN.NS",
    "JSWSTEEL.NS", "KOTAKBANK.NS", "LT.NS", "M&M.NS", "MARUTI.NS",
    "NESTLEIND.NS", "NTPC.NS", "ONGC.NS", "POWERGRID.NS", "RELIANCE.NS",
    "SBILIFE.NS", "SBIN.NS", "SHRIRAMFIN.NS", "SUNPHARMA.NS",
    "TATACONSUM.NS", "TATAMOTORS.NS", "TATASTEEL.NS", "TCS.NS",
    "TECHM.NS", "TITAN.NS", "TRENT.NS", "ULTRACEMCO.NS", "WIPRO.NS",
]

INTERVAL = "5m"          # 5m, 15m, 1h
RR = 2.0                 # risk:reward = 1:2
MAX_AGE_MIN = 10         # only alert for candles that closed in the last 10 min
LOOP_SLEEP_SEC = 60      # how often the loop re-checks
STOP_TIME = (15, 30)     # stop at 3:30 PM IST

# Option idea (indices only). Verify strike steps/expiry rules in your broker app.
STEP = {"^NSEI": 50, "^NSEBANK": 100, "^BSESN": 100}
EXPIRY = {"^NSEI": ("weekly", 1),      # Nifty: Tuesday
          "^BSESN": ("weekly", 3),     # Sensex: Thursday
          "^NSEBANK": ("monthly", 1)}  # Bank Nifty: last Tuesday of month

IST = pytz.timezone("Asia/Kolkata")
sent = set()             # avoids duplicate alerts
# ==========================================


def send(msg):
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
    """Return list of (pattern, side, stop) for the latest completed candle."""
    if len(df) < 3:
        return []
    c, p, pp = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    out = []

    # Engulfing
    if red(p) and green(c) and c.Close >= p.Open and c.Open <= p.Close:
        out.append(("Bullish Engulfing", "BUY", min(c.Low, p.Low)))
    if green(p) and red(c) and c.Open >= p.Close and c.Close <= p.Open:
        out.append(("Bearish Engulfing", "SELL", max(c.High, p.High)))

    r = rng(c)
    if r > 0:
        lower = min(c.Open, c.Close) - c.Low
        upper = c.High - max(c.Open, c.Close)
        # Hammer / Shooting star
        if lower >= 2 * body(c) and upper <= 0.3 * r and body(c) > 0:
            out.append(("Hammer", "BUY", c.Low))
        if upper >= 2 * body(c) and lower <= 0.3 * r and body(c) > 0:
            out.append(("Shooting Star", "SELL", c.High))

    # Morning / Evening star
    if red(pp) and body(p) < 0.5 * body(pp) and green(c) and c.Close > (pp.Open + pp.Close) / 2:
        out.append(("Morning Star", "BUY", min(p.Low, c.Low)))
    if green(pp) and body(p) < 0.5 * body(pp) and red(c) and c.Close < (pp.Open + pp.Close) / 2:
        out.append(("Evening Star", "SELL", max(p.High, c.High)))

    return out


def last_completed(df, minutes):
    now = dt.datetime.now(IST)
    last_ts = df.index[-1].tz_convert(IST)
    if last_ts + dt.timedelta(minutes=minutes) > now:   # candle still forming
        df = df.iloc[:-1]
    return df


def next_expiry(sym):
    kind, wd = EXPIRY[sym]
    today = dt.datetime.now(IST).date()

    def last_wd(y, m):
        d = dt.date(y, m, calendar.monthrange(y, m)[1])
        while d.weekday() != wd:
            d -= dt.timedelta(days=1)
        return d

    if kind == "weekly":
        d = today + dt.timedelta(days=(wd - today.weekday()) % 7)
        if (d - today).days < 2:          # too close to expiry -> next week
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
    for sym in WATCHLIST:
        try:
            df = yf.download(sym, period="5d", interval=INTERVAL,
                             progress=False, auto_adjust=False)
            if df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
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
        except Exception as e:
            print(sym, "error:", e)


if __name__ == "__main__":
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event == "workflow_dispatch":
        send("✅ Test: bot is connected")
    if event == "schedule":
        while dt.datetime.now(IST).time() < dt.time(*STOP_TIME):
            scan()
            time.sleep(LOOP_SLEEP_SEC)
    else:
        scan()
