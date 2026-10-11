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
import re
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


_CACHE: dict[str, list[float]] = {}


def closes_for(yahoo: str, stooq: str | None) -> list[float]:
    if yahoo not in _CACHE:
        _CACHE[yahoo] = _closes_for(yahoo, stooq)
    return _CACHE[yahoo]


def _closes_for(yahoo: str, stooq: str | None) -> list[float]:
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


OZ = 31.1035  # grams in one troy ounce


def inr(v: float) -> str:
    """Indian digit grouping: 464786.5 -> '₹4,64,787'."""
    n = str(int(round(v)))
    head, tail = n[:-3], n[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join(([head] if head else []) + groups + [tail])


def ibja_per_gram() -> dict[str, float]:
    """India's benchmark gold rate (IBJA), per gram, without 3% GST and making charges."""
    r = requests.get("https://ibjarates.com/", headers=UA, timeout=20)
    r.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", r.text)
    text = re.sub(r"\s+", " ", text)
    out = {}
    for purity in ("999", "916"):
        m = re.search(purity + r" ?Purity\D{0,40}?([\d,]{4,8})\s*\(\s*1\s*Gram", text)
        if m:
            v = float(m.group(1).replace(",", ""))
            if 3000 < v < 100000:           # sanity check: rupees per gram
                out[purity] = v
    return out


def gold_block() -> tuple[str, str]:
    usd_oz = closes_for("GC=F", "xauusd")
    fx = closes_for("INR=X", "usdinr")
    if not usd_oz:
        return "", ""
    oz = usd_oz[-1]
    rows = []
    try:
        ibja = ibja_per_gram()
    except Exception:
        ibja = {}
    if ibja.get("999"):
        rows.append(("🇮🇳 India 24K", inr(ibja["999"]), inr(ibja["999"] * OZ)))
        if ibja.get("916"):
            rows.append(("🇮🇳 India 22K (jewellery)", inr(ibja["916"]), inr(ibja["916"] * OZ)))
        note = "India: IBJA rate, without 3% GST and making charges."
    elif fx:
        g_inr = oz * fx[-1] / OZ
        rows.append(("🇮🇳 India (world price in ₹)", inr(g_inr), inr(g_inr * OZ)))
        note = "India: world price converted to rupees, before import duty and GST (shops charge more)."
    else:
        note = ""
    rows.append(("🇺🇸 USA", f"${oz / OZ:,.2f}", f"${oz:,.2f}"))
    md = ["**Gold today**", "", "| | 1 gram | 1 ounce (31.1 g) |", "|---|---:|---:|"]
    md += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    md += ["", f"_{note} USA: world gold price._" if note else "_USA: world gold price._"]
    plain = "Gold: " + "; ".join(f"{a.split(' ', 1)[1]} {b} per gram, {c} per ounce" for a, b, c in rows)
    return "\n".join(md), plain


def numbers_box(weekly: bool = False) -> tuple[str, str]:
    """Return (markdown box for the email, plain one-liner for the AI and podcast)."""
    m, w = market_rows(weekly), weather_rows()
    try:
        g_md, g_plain = gold_block()
    except Exception:
        g_md, g_plain = "", ""
    if not (m or w or g_md):
        return "", ""
    span = "1 week" if weekly else "1 day"
    lines = ["## Markets & weather", ""]
    plain = []
    if m:
        lines += [f"| | Latest | {span} |", "|---|---:|---:|"]
        lines += [f"| {r['label']} | {r['value']} | {r['change']} |" for r in m]
        plain += [f"{r['label']} {r['value']} ({r['change'].split(' ', 1)[1]} over {span})"
                  for r in m]
    if g_md:
        lines += ["", g_md]
        plain.append(g_plain)
    if w:
        lines += [""] + [f"**{r['city']}** ({r['day']}): {r['text']}" for r in w]
        plain += [f"{r['city']} weather {r['day']}: {r['text']}" for r in w]
    lines += ["", "_Latest close or last available price; data from Yahoo Finance / Stooq / "
              "ECB / IBJA and Open-Meteo._", ""]
    return "\n".join(lines), "; ".join(plain)
