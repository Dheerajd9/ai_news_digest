"""Short weather line for the podcasts (Hyderabad + Dallas by default), from the free
Open-Meteo API (no key, no sign-up). Never raises: returns "" if anything fails.

  WEATHER_PLACES   "Name:lat:lon;Name:lat:lon" (default Hyderabad and Dallas, city level only)
  WEATHER          set to 0 to switch it off
"""

from __future__ import annotations

import os

import requests

DEFAULT_PLACES = "Hyderabad:17.385:78.487;Dallas:32.777:-96.797"
URL = "https://api.open-meteo.com/v1/forecast"

CODES = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "fog", 48: "fog",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
         65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow",
         75: "heavy snow", 77: "snow", 80: "rain showers", 81: "rain showers",
         82: "heavy rain showers", 85: "snow showers", 86: "snow showers", 95: "thunderstorm",
         96: "thunderstorm with hail", 99: "thunderstorm with hail"}

_CACHE: dict[str, str] = {}


def fetch(lat: float, lon: float) -> dict:
    r = requests.get(URL, timeout=10, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"})
    r.raise_for_status()
    return r.json()


def _place(name: str, d: dict) -> str:
    cur, day = d.get("current", {}), d.get("daily", {})

    def dly(key, i):
        vals = day.get(key) or []
        return vals[i] if len(vals) > i and vals[i] is not None else None

    parts = []
    if cur.get("temperature_2m") is not None:
        parts.append(f"now {round(cur['temperature_2m'])}°C, {CODES.get(cur.get('weather_code'), 'mixed')}")
    for i, label in ((0, "today"), (1, "tomorrow")):
        hi, lo, rain = dly("temperature_2m_max", i), dly("temperature_2m_min", i), \
            dly("precipitation_probability_max", i)
        if hi is None:
            continue
        txt = f"{label} {round(lo)}–{round(hi)}°C, {CODES.get(dly('weather_code', i), 'mixed')}"
        if rain is not None:
            txt += f", rain chance {round(rain)}%"
        parts.append(txt)
    return f"{name}: " + "; ".join(parts) if parts else ""


def line() -> str:
    """e.g. 'Hyderabad: now 28°C, light rain; today 22–31°C, ... | Dallas: ...' (local days)."""
    if os.getenv("WEATHER", "1") == "0":
        return ""
    if "line" in _CACHE:
        return _CACHE["line"]
    out = []
    for spec in (os.getenv("WEATHER_PLACES") or DEFAULT_PLACES).split(";"):
        try:
            name, lat, lon = spec.split(":")
            txt = _place(name.strip(), fetch(float(lat), float(lon)))
            if txt:
                out.append(txt)
        except Exception as exc:
            print(f"  ! weather for one place skipped: {type(exc).__name__}")
    _CACHE["line"] = " | ".join(out)
    return _CACHE["line"]
