"""
Deeper portfolio analytics used by portfolio_report.py and digest.py:

  - benchmark check: each fund vs an index over rolling 3-year periods
  - Monte Carlo: chances of different outcomes, using the portfolio's real volatility
  - tax calendar: long/short-term lots, unused LTCG exemption, Regular->Direct switch cost
  - portfolio-manager review: Gemini with Google Search grounding (free tier)
  - daily snapshot line and smart alerts for the morning email
"""

import bisect
import json
import math
import os
import random
import re
import time
from datetime import date, timedelta

import requests

from digest import GEMINI_API, gemini_models, gh_annotate

LTCG_RATE, LTCG_EXEMPT = 0.125, 125000
MILESTONES = [5e6, 1e7, 2e7, 3e7]  # ₹50 L, ₹1 Cr, ₹2 Cr, ₹3 Cr

# Index-fund proxies for each category's benchmark (Direct Growth plans).
BENCHMARKS = {
    "large": ("Nifty 50", ["UTI Nifty 50 Index", "HDFC Nifty 50 Index", "ICICI Prudential Nifty 50 Index"]),
    "mid": ("Nifty Midcap 150", ["Motilal Oswal Nifty Midcap 150 Index", "Nippon India Nifty Midcap 150 Index"]),
    "small": ("Nifty Smallcap 250", ["Nippon India Nifty Smallcap 250 Index", "Motilal Oswal Nifty Smallcap 250 Index"]),
    "_broad": ("Nifty 500", ["Motilal Oswal Nifty 500 Index", "ICICI Prudential Nifty 500 Index",
                             "Motilal Oswal Nifty 500"]),
}


# ------------------------------------------------------------------ helpers --

class Series:
    """Fast NAV lookups on a sorted [(date, nav)] history."""

    def __init__(self, hist):
        self.dates = [d for d, _ in hist]
        self.navs = [v for _, v in hist]

    def at(self, day):
        i = bisect.bisect_right(self.dates, day) - 1
        return None if i < 0 else self.navs[i]

    @property
    def first(self):
        return self.dates[0]

    @property
    def last(self):
        return self.dates[-1]


def is_index_fund(f):
    return "index" in f["name"].lower() or "nifty" in f["name"].lower()


# --------------------------------------------------------- benchmark check --

def benchmark_check(funds, snap):
    from portfolio_report import find_scheme  # lazy: avoids circular import

    cache = {}

    def bench(cat):
        key = cat if cat in BENCHMARKS else "_broad"
        if key not in cache:
            label, searches = BENCHMARKS[key]
            try:
                code, name, _, hist = find_scheme({"name": label, "search": searches}, "Direct", snap)
                cache[key] = (label, name, Series(hist))
            except Exception as exc:
                gh_annotate("warning", f"Benchmark {label} not found: {exc}")
                cache[key] = None
        return cache[key]

    out = {}
    for f in funds:
        if is_index_fund(f):
            out[f["name"]] = {"verdict": "Index fund (tracks its index)", "index": "—"}
            continue
        b = bench(f["category"])
        if not b:
            continue
        label, bname, bs = b
        fs = Series(f["hist"])
        end = min(fs.last, bs.last)
        start_min = max(fs.first, bs.first) + timedelta(days=1096)
        diffs = []
        d = end
        while d >= start_min and d >= end - timedelta(days=5 * 365):
            back = d - timedelta(days=1096)
            fa, fb, ba, bb = fs.at(back), fs.at(d), bs.at(back), bs.at(d)
            if fa and fb and ba and bb:
                diffs.append(((fb / fa) ** (1 / 3) - 1 - ((bb / ba) ** (1 / 3) - 1)) * 100)
            d -= timedelta(days=7)
        if len(diffs) < 20:
            out[f["name"]] = {"index": label, "verdict": "Not enough history yet"}
            continue
        beat = sum(x > 0 for x in diffs) / len(diffs) * 100
        avg = sum(diffs) / len(diffs)
        verdict = ("Consistently ahead" if beat >= 65 and avg > 0 else
                   "Mostly ahead" if beat >= 50 else
                   "Mixed" if beat >= 35 else "Trailing its index")
        out[f["name"]] = {"index": label, "beat_pct": beat, "avg_excess": avg, "latest_excess": diffs[0],
                          "windows": len(diffs), "verdict": verdict}
    return out


# -------------------------------------------------------------- Monte Carlo --

def portfolio_volatility(funds, years=3):
    """Yearly volatility of the current mix (current units), from weekly values."""
    series = [(f["units"], Series(f["hist"])) for f in funds]
    end = min(s.last for _, s in series)
    pts = []
    d = end - timedelta(days=round(365.25 * years))
    while d <= end:
        vals = [u * s.at(d) for u, s in series if s.at(d)]
        if len(vals) == len(series):
            pts.append(sum(vals))
        d += timedelta(days=7)
    rets = [math.log(b / a) for a, b in zip(pts, pts[1:])]
    if len(rets) < 30:
        return 0.16, pts
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(52), pts


def monte_carlo(start_value, invested, rate_pct, vol, sip=0.0, horizons=(5, 10, 15, 20), paths=5000, seed=None):
    rng = random.Random(seed)
    mu_m = (math.log(1 + rate_pct / 100) - vol ** 2 / 2) / 12
    sd_m = vol / math.sqrt(12)
    months = max(horizons) * 12
    snap_at = {h * 12: [] for h in horizons}
    for _ in range(paths):
        v = start_value
        for m in range(1, months + 1):
            v = v * math.exp(rng.gauss(mu_m, sd_m)) + sip
            if m in snap_at:
                snap_at[m].append(v)
    rows = []
    for h in horizons:
        vals = sorted(snap_at[h * 12])
        put_in = invested + sip * 12 * h
        q = lambda p: vals[min(len(vals) - 1, int(p * len(vals)))]
        rows.append({
            "years": h, "p10": q(0.10), "p50": q(0.50), "p90": q(0.90),
            "loss_chance": sum(v < put_in for v in vals) / len(vals) * 100,
            "milestones": {m: sum(v >= m for v in vals) / len(vals) * 100 for m in MILESTONES},
        })
    return rows


# ------------------------------------------------------------- tax calendar --

def fy_end(today):
    return date(today.year + (1 if today.month >= 4 else 0), 3, 31)


def tax_calendar(funds, today=None):
    today = today or date.today()
    lots, unknown = [], []
    for f in funds:
        txns = f.get("transactions") or []
        if not txns:
            unknown.append(f["name"])
            continue
        for t in txns:
            bought = date.fromisoformat(t["date"])
            gain = t["units"] * f["nav"] - t["amount"]
            long_term = (today - bought).days > 365
            lots.append({"fund": f["name"], "plan": f["plan"], "bought": bought, "gain": gain,
                         "long_term": long_term, "lt_on": bought + timedelta(days=366)})
    lt_gain = sum(l["gain"] for l in lots if l["long_term"])
    st_gain = sum(l["gain"] for l in lots if not l["long_term"])
    soon = [l for l in lots if not l["long_term"] and (l["lt_on"] - today).days <= 60]
    reg = [l for l in lots if l["plan"].lower() == "regular"]
    reg_lt = sum(l["gain"] for l in reg if l["long_term"])
    reg_st = sum(l["gain"] for l in reg if not l["long_term"])
    switch_tax = max(0.0, reg_lt - LTCG_EXEMPT) * LTCG_RATE + max(0.0, reg_st) * 0.20
    return {
        "fy_end": fy_end(today), "days_left": (fy_end(today) - today).days,
        "lt_gain": lt_gain, "st_gain": st_gain,
        "harvestable": max(0.0, min(lt_gain, LTCG_EXEMPT)),
        "lt_lots": [l for l in lots if l["long_term"]], "st_lots": [l for l in lots if not l["long_term"]],
        "soon": soon, "unknown": unknown,
        "regular_funds": sorted({l["fund"] for l in reg}), "regular_gain": reg_lt + reg_st,
        "switch_tax": switch_tax, "season": today.month in (1, 2, 3),
    }


# ------------------------------------------------------------ Gemini helper --

def gemini_json(prompt, use_search=False, temperature=0.3):
    """Returns (dict, sources, model) or (None, [], None). Tries Google Search grounding first if asked."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None, [], None
    attempts = ([True, False] if use_search else [False])
    last = ""
    for search in attempts:
        if not search and use_search and last:
            gh_annotate("warning", f"Google Search grounding unavailable, using data only. Reason: {last}")
        for model in gemini_models(key)[:3]:
            body = {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": temperature}}
            if search:
                body["tools"] = [{"google_search": {}}]
            else:
                body["generationConfig"]["responseMimeType"] = "application/json"
            try:
                for wait in (0, 35, 70):  # free tier rate limits reset within a minute
                    if wait:
                        time.sleep(wait)
                    r = requests.post(f"{GEMINI_API}/models/{model}:generateContent",
                                      headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                      json=body, timeout=180)
                    if r.status_code != 429:
                        break
                if r.status_code >= 400:
                    last = f"{model}{' +search' if search else ''} HTTP {r.status_code}: {r.text[:120]}"
                    continue
                cand = r.json()["candidates"][0]
                text = "".join(p.get("text", "") for p in cand["content"]["parts"])
                m = re.search(r"\{.*\}", text, re.S)
                data = json.loads(m.group(0) if m else text)
                sources, seen = [], set()
                for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks", []):
                    web = ch.get("web") or {}
                    if web.get("uri") and web.get("title") not in seen:
                        seen.add(web.get("title"))
                        sources.append({"title": web.get("title", "source"), "uri": web["uri"]})
                return data, sources[:10], model + (" with Google Search" if search else "")
            except Exception as exc:
                last = f"{model}: {type(exc).__name__}: {str(exc)[:120]}"
    gh_annotate("warning", f"Gemini call failed. {last}")
    return None, [], None


# -------------------------------------------------- portfolio manager review --

PM_PROMPT = """You are acting as an experienced Indian mutual fund portfolio manager reviewing the
portfolio of an NRI client (lives in Texas, USA; invests via NRO and external folios; long horizon,
10+ years; goals include buying agricultural land in India). Use the DATA below, and use Google Search
to research each fund's current situation: fund manager changes, AUM size, expense ratio (Direct vs
Regular), recent regulatory issues or news, and category outlook. Be specific and honest. Prefer
fewer, better funds. Keep tax costs low: don't suggest selling lots held under 12 months unless there
is a strong reason; Regular-to-Direct switches are best done when gains fit within the yearly
₹1.25 lakh long-term exemption.

Return ONLY a JSON object, no markdown fences:
{{
 "summary": "2-3 sentence overall verdict on the portfolio",
 "funds": [{{"name": "<exact fund name from data>", "action": "Keep | Keep, stop adding | Watch | Consider consolidating | Consider switching to Direct | Consider exiting",
            "why": "max 40 words, cite data and research"}}],
 "target": [{{"bucket": "e.g. Large cap index / Flexi cap / Mid cap / Small cap / Hybrid or debt", "pct": 0,
             "funds": "which existing fund(s) to keep in this bucket, or 'new: <type>'"}}],
 "steps": ["ordered, tax-aware transition steps, max 30 words each, 4-6 steps"],
 "watch": ["2-3 things to watch before the next review"]
}}
Target percentages must add up to 100. Phrase actions as suggestions to consider, not orders.

DATA
{data}"""


def pm_review(funds, bench, mc, taxcal, flags, rates):
    total = sum(f["value"] for f in funds)
    data = {
        "total_value": round(total),
        "assumed_expected_return_pct": round(rates[1], 2),
        "funds": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in {
            "name": f["name"], "scheme": f["scheme"], "plan": f["plan"], "category": f["category"],
            "weight_pct": f["value"] / total * 100, "value": f["value"], "invested": f["invested"],
            "your_xirr_pct": f["xirr"], "first_purchase": str(min(d for d, _ in f["flows"])) if f["flows"] else "unknown",
            "cagr_1y": f["r1y"], "cagr_3y": f["r3y"], "cagr_5y": f["r5y"], "volatility_3y": f["vol"],
            "worst_fall_3y": f["mdd"],
            "benchmark": bench.get(f["name"], {}).get("index"),
            "beat_index_in_pct_of_3y_periods": bench.get(f["name"], {}).get("beat_pct"),
            "avg_yearly_excess_vs_index": bench.get(f["name"], {}).get("avg_excess"),
        }.items()} for f in funds],
        "monte_carlo": [{"years": r["years"], "bad_case_10pct": round(r["p10"]), "median": round(r["p50"]),
                         "good_case_90pct": round(r["p90"])} for r in mc],
        "tax": {"long_term_unrealized_gain": round(taxcal["lt_gain"]),
                "short_term_unrealized_gain": round(taxcal["st_gain"]),
                "regular_plan_funds": taxcal["regular_funds"],
                "est_tax_if_regular_switched_now": round(taxcal["switch_tax"]),
                "funds_with_unknown_purchase_dates": taxcal["unknown"]},
        "rule_based_flags": flags,
    }
    result, sources, model = gemini_json(PM_PROMPT.format(data=json.dumps(data, default=str)), use_search=True)
    if not result or not isinstance(result.get("funds"), list):
        return None
    names = {f["name"] for f in funds}
    result["funds"] = [x for x in result["funds"] if isinstance(x, dict) and x.get("name") in names]
    result["sources"], result["model"] = sources, model
    gh_annotate("notice", f"Portfolio manager review by {model}; {len(sources)} web sources")
    return result


# ------------------------------------------------- daily line and alerts --

def daily_snapshot(funds):
    total = prev = month_start = week_ago = 0.0
    nav_date = max(f["nav_date"] for f in funds)
    first_of_month = nav_date.replace(day=1) - timedelta(days=1)
    for f in funds:
        s = Series(f["hist"])
        u = f["units"]
        total += u * s.navs[-1]
        prev += u * (s.navs[-2] if len(s.navs) > 1 else s.navs[-1])
        month_start += u * (s.at(first_of_month) or s.navs[0])
        week_ago += u * (s.at(nav_date - timedelta(days=7)) or s.navs[0])
    return {"total": total, "day_pct": (total / prev - 1) * 100, "day_abs": total - prev,
            "mtd_pct": (total / month_start - 1) * 100, "week_pct": (total / week_ago - 1) * 100,
            "nav_date": nav_date}


def fund_falls(f, threshold=-15.0, window_days=90):
    """Current fall from the recent high, plus how past falls of this size played out."""
    s = Series(f["hist"])
    recent = [v for d, v in zip(s.dates, s.navs) if d >= s.last - timedelta(days=window_days)]
    fall_now = (s.navs[-1] / max(recent) - 1) * 100
    episodes, i, n = [], 0, len(s.navs)
    peak_i = 0
    while i < n:
        if s.navs[i] >= s.navs[peak_i]:
            peak_i = i
        elif (s.navs[i] / s.navs[peak_i] - 1) * 100 <= threshold:
            j = i
            while j < n and s.navs[j] < s.navs[peak_i]:
                j += 1
            episodes.append((s.dates[i], (s.dates[j] - s.dates[peak_i]).days if j < n else None))
            i, peak_i = j, j if j < n else peak_i
            continue
        i += 1
    recovered = [d for _, d in episodes if d is not None]
    return fall_now, len(episodes), (sorted(recovered)[len(recovered) // 2] if recovered else None)


def alerts(funds, state, today=None):
    """Returns (list of (key, message), new_state). Each alert repeats at most once a week."""
    today = today or date.today()
    found = []
    snap = daily_snapshot(funds)
    if snap["week_pct"] <= -7:
        _, pts = portfolio_volatility(funds, years=3)
        weekly = [(b / a - 1) * 100 for a, b in zip(pts, pts[1:])]
        big = [i for i, w in enumerate(weekly) if w <= -7]
        later = [pts[min(len(pts) - 1, i + 1 + 26)] > pts[i + 1] for i in big]
        found.append(("portfolio_week",
                      f"Your portfolio fell {snap['week_pct']:.1f}% in the past week (now ₹{snap['total']:,.0f}). "
                      f"With today's fund mix, weekly falls of 7%+ happened {len(big)} time(s) in the last 3 years; "
                      f"6 months later the portfolio was higher in {sum(later)} of them. Falls like this are normal "
                      "for an all-equity portfolio; a long horizon usually matters more than any single week."))
    for f in funds:
        fall, count, median_days = fund_falls(f)
        if fall <= -15:
            hist_note = (f"Since launch it has had {count} falls of 15%+ from a peak"
                         + (f"; the typical one took about {median_days // 30} months to fully recover."
                            if median_days else "."))
            found.append((f"fund_{f['name']}",
                          f"{f['name']} is {fall:.1f}% below its 3-month high. {hist_note} "
                          "Check whether the whole category fell (market move) or only this fund (fund-specific issue)."))
    new_state = dict(state)
    fresh = []
    for key, msg in found:
        last = state.get(key)
        if last and (today - date.fromisoformat(last)).days < 7:
            continue
        fresh.append((key, msg))
        new_state[key] = today.isoformat()
    return fresh, new_state
