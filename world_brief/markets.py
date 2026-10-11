"""
Exact numbers box: markets + weather from free, keyless data sources.
The AI never writes these numbers; they come straight from the data, so they're always right.

Markets: Yahoo Finance chart endpoint (unofficial, free), Stooq CSV as a fallback,
         Frankfurter (ECB) as a last-resort fallback for USD/INR.
Weather: Open-Meteo (free, no key).
Any source that fails is simply left out of the box; it never breaks the email.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

UA = {"User-Agent": "Mozilla/5.0 (world-brief personal digest)"}

# label, yahoo symbol, stooq symbol, kind
INSTRUMENTS = [
    ("USD → INR", "INR=X", "usdinr", "fx"),
    ("Nifty 50", "^NSEI", None, "index"),
    ("Sensex", "^BSESN", None, "index"),
    ("S&P 500", "^GSPC", "^spx", "index"),
    ("Nasdaq", "^IXIC", "^ndq", "index"),
    ("Gold ($/oz)", "GC=F", "xauusd", "usd"),
    ("Brent oil ($/bbl)", "BZ=F", "cb.f", "usd"),
]

CITIES = [
    ("Dallas", 32.78, -96.80, "America/Chicago", "F"),
    ("Hyderabad", 17.39, 78.49, "Asia/Kolkata", "C"),
]

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "fog", 48: "fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
       65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "showers",
       81: "showers", 82: "heavy showers", 95: "thunderstorms", 96: "thunderstorms with hail",
       99: "severe thunderstorms"}


def _yahoo(symbol: str) -> list[float]:
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                     params={"range": "1mo", "interval": "1d"}, headers=UA, timeout=15)
    r.raise_for_status()
    closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    return [c for c in closes if c is not None]


def _stooq(symbol: str) -> list[float]:
    r = requests.get("https://stooq.com/q/d/l/", params={"s": symbol, "i": "d"},
                     headers=UA, timeout=15)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    return [float(row["Close"]) for row in rows[-25:] if row.get("Close")]


def _frankfurter_usdinr() -> list[float]:
    end = datetime.utcnow().date()
    start = end - timedelta(days=14)
    r = requests.get(f"https://api.frankfurter.app/{start}..{end}",
                     params={"from": "USD", "to": "INR"}, timeout=15)
    r.raise_for_status()
    rates = r.json()["rates"]
    return [rates[d]["INR"] for d in sorted(rates)]


def closes_for(yahoo: str, stooq: str | None) -> list[float]:
    for fn, sym in ((_yahoo, yahoo), (_stooq, stooq)):
        if not sym:
            continue
        try:
            closes = fn(sym)
            if len(closes) >= 2:
                return closes
        except Exception:
            pass
    if yahoo == "INR=X":
        try:
            return _frankfurter_usdinr()
        except Exception:
            pass
    return []


def fmt_value(v: float, kind: str) -> str:
    if kind == "fx":
        return f"₹{v:,.2f}"
    if kind == "usd":
        return f"${v:,.2f}"
    return f"{v:,.0f}"


def fmt_change(now: float, then: float) -> str:
    pct = (now - then) / then * 100
    arrow = "▲" if pct > 0.005 else "▼" if pct < -0.005 else "■"
    return f"{arrow} {pct:+.2f}%"


def market_rows(weekly: bool = False) -> list[dict]:
    rows = []
    for label, ysym, ssym, kind in INSTRUMENTS:
        closes = closes_for(ysym, ssym)
        if len(closes) < 2:
            continue
        back = 6 if weekly else 2               # ~1 trading week vs previous close
        then = closes[-back] if len(closes) >= back else closes[0]
        rows.append({"label": label, "value": fmt_value(closes[-1], kind),
                     "change": fmt_change(closes[-1], then)})
    return rows


def weather_rows() -> list[dict]:
    rows = []
    for city, lat, lon, tz, unit in CITIES:
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": 2,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max",
                "temperature_unit": "fahrenheit" if unit == "F" else "celsius"},
                timeout=15)
            r.raise_for_status()
            d = r.json()["daily"]
            local = datetime.now(ZoneInfo(tz))
            i = 1 if local.hour >= 15 else 0       # late in their day -> show tomorrow
            day = datetime.fromisoformat(d["time"][i]).strftime("%a %d %b")
            rows.append({
                "city": city, "day": day,
                "text": (f"{WMO.get(d['weather_code'][i], 'mixed')}, "
                         f"{d['temperature_2m_max'][i]:.0f}°/{d['temperature_2m_min'][i]:.0f}°{unit}, "
                         f"rain {d['precipitation_probability_max'][i] or 0:.0f}%")})
        except Exception:
            pass
    return rows


def numbers_box(weekly: bool = False) -> tuple[str, str]:
    """Return (markdown box for the email, plain one-liner for the AI and podcast)."""
    m, w = market_rows(weekly), weather_rows()
    if not (m or w):
        return "", ""
    span = "1 week" if weekly else "1 day"
    lines = ["## Markets & weather", ""]
    plain = []
    if m:
        lines += [f"| | Latest | {span} |", "|---|---:|---:|"]
        lines += [f"| {r['label']} | {r['value']} | {r['change']} |" for r in m]
        plain += [f"{r['label']} {r['value']} ({r['change'].split(' ', 1)[1]} over {span})"
                  for r in m]
    if w:
        lines += [""] + [f"**{r['city']}** ({r['day']}): {r['text']}" for r in w]
        plain += [f"{r['city']} weather {r['day']}: {r['text']}" for r in w]
    lines += ["", "_Latest close or last available price; data from Yahoo Finance / Stooq / "
              "ECB and Open-Meteo._", ""]
    return "\n".join(lines), "; ".join(plain)
