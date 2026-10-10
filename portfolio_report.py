"""
Fortnightly mutual fund portfolio report.

Reads portfolio.json, pulls full NAV history for every fund from MFapi (free,
AMFI data), works out returns, risk and 5/10/15/20-year projections, asks
Gemini (free tier) for an expert-style review, and emails the report.

Local preview without email:  python portfolio_report.py --dry-run
"""

import argparse
import html
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta

import requests

from digest import GEMINI_API, LOCAL_TZ, gemini_models, gh_annotate, send_email
import portfolio_analytics as pa
import report_sections as rs

MFAPI = "https://api.mfapi.in/mf"
HORIZONS = [5, 10, 15, 20]
LTCG_RATE, LTCG_EXEMPT = 0.125, 125000  # equity funds held > 12 months (verify yearly)

# Long-run return assumptions (% a year): conservative / expected / optimistic.
# Based on long-term Indian equity history and deliberately below the recent bull run.
CATEGORY_RATES = {
    "large":        (8.0, 10.5, 12.5),
    "large_mid":    (8.5, 11.5, 13.5),
    "flexi":        (8.5, 11.5, 13.5),
    "multi":        (8.5, 11.5, 13.5),
    "value_contra": (8.5, 11.5, 13.5),
    "mid":          (9.0, 12.5, 15.0),
    "small":        (9.0, 13.0, 16.0),
    "sectoral":     (7.5, 11.0, 14.0),
}
CATEGORY_LABEL = {
    "large": "Large cap", "large_mid": "Large & mid cap", "flexi": "Flexi cap", "multi": "Multi cap",
    "value_contra": "Contra/value", "mid": "Mid cap", "small": "Small cap", "sectoral": "Sectoral/thematic",
}
EXCLUDE = re.compile(r"idcw|dividend|payout|reinvest|bonus|segregated|institutional", re.I)


def inr(x):
    """Indian digit grouping: 2577706 -> ₹25,77,706."""
    neg, x = x < 0, abs(round(x))
    s = str(x)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = f"{head},{tail}"
    return ("-₹" if neg else "₹") + s


def short_inr(x):
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:.2f} Cr"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:.1f} L"
    return inr(x)


def pct(x, digits=1):
    return "—" if x is None else f"{x:+.{digits}f}%"


# ------------------------------------------------------------- fund data --

STOPWORDS = {"fund", "plan", "option", "the", "growth", "direct", "regular", "scheme", "an", "open",
             "ended", "equity", "formerly", "known", "as", "erstwhile"}


def tokens(text):
    text = re.sub(r"\(.*?\)", " ", text.lower())  # drop "(formerly ...)" notes
    text = text.replace("&", " and ").replace("midcap", "mid cap").replace("smallcap", "small cap") \
               .replace("largecap", "large cap").replace("multicap", "multi cap").replace("flexicap", "flexi cap")
    return set(re.findall(r"[a-z0-9]+", text)) - STOPWORDS


CATEGORY_WORDS = {"large", "mid", "small", "multi", "flexi", "micro", "focused", "contra", "value",
                  "nifty", "index", "sensex", "etf", "fof"}  # index funds never stand in for active funds
_ALL_SCHEMES = None


def all_schemes():
    """Full AMFI scheme list from MFapi (about 40k schemes), fetched once per run."""
    global _ALL_SCHEMES
    if _ALL_SCHEMES is None:
        r = requests.get(MFAPI, timeout=60)
        r.raise_for_status()
        _ALL_SCHEMES = [(int(s["schemeCode"]), s["schemeName"]) for s in r.json()]
    return _ALL_SCHEMES


def nav_history(code):
    r = requests.get(f"{MFAPI}/{code}", timeout=60)
    r.raise_for_status()
    js = r.json()
    hist = sorted((datetime.strptime(d["date"], "%d-%m-%Y").date(), float(d["nav"]))
                  for d in js["data"] if float(d["nav"]) > 0)
    return js["meta"], hist


def plan_matches(name, plan):
    low = name.lower()
    if plan.lower() == "direct":
        return bool(re.search(r"\bdir(ect)?\b", low))
    return not re.search(r"\bdir(ect)?\b", low)  # Regular plans often don't say "Regular"


def verified(hist, fund):
    """True if the scheme's NAV on each known purchase date matches the statement (within 1%)."""
    txns = fund.get("transactions") or []
    if not txns:
        return True
    for t in txns[:2]:
        got = nav_on(hist, date.fromisoformat(t["date"]))
        if not got or abs(got[1] / t["nav"] - 1) > 0.01:
            return False
    return True


def live(hist, snap):
    return bool(hist) and (date.today() - hist[-1][0]).days <= 20 and hist[0][0] <= snap


def find_scheme(fund, plan, snap):
    """Best-matching live scheme, verified against purchase NAVs when available.
    Returns (code, name, meta, history)."""
    if fund.get("scheme_code"):
        meta, hist = nav_history(int(fund["scheme_code"]))
        if not verified(hist, fund):
            raise RuntimeError(f"scheme_code {fund['scheme_code']} NAV doesn't match your purchase NAV for {fund['name']}")
        return int(fund["scheme_code"]), meta.get("scheme_name", fund["name"]), meta, hist
    scored = {}
    for query in fund.get("search", [fund["name"]]):
        want = tokens(query)
        for code, name in all_schemes():
            if not plan_matches(name, plan) or not re.search(r"\bgr(owth|\.)?\b", name.lower()) \
                    or EXCLUDE.search(name):
                continue
            have = tokens(name)
            if not want <= have or (CATEGORY_WORDS & have) - want:
                continue
            score = 1.0 - 0.1 * len(have - want)
            if score > scored.get(code, (-9, ""))[0]:
                scored[code] = (score, name)
    tried = set()
    for code, (score, name) in sorted(scored.items(), key=lambda kv: -kv[1][0])[:10]:
        tried.add(code)
        meta, hist = nav_history(code)
        if live(hist, snap) and verified(hist, fund):
            return code, name, meta, hist
    # Fallback for funds AMFI lists under an unexpected name: scan the fund house's
    # schemes for one whose NAV matches the purchase NAV exactly.
    if fund.get("transactions"):
        house = " ".join(fund.get("search", [fund["name"]])[0].lower().split()[:2])
        pool = [(c, n) for c, n in all_schemes()
                if n.lower().startswith(house.split()[0]) and house.split()[-1] in n.lower()
                and plan_matches(n, plan) and not EXCLUDE.search(n) and c not in tried]
        for code, name in pool[:150]:
            meta, hist = nav_history(code)
            if live(hist, snap) and verified(hist, fund):
                gh_annotate("notice", f"{fund['name']}: found by NAV match as '{name}'")
                return code, name, meta, hist
    raise RuntimeError(f"No live {plan} Growth scheme matching your purchase NAV for '{fund['name']}'. "
                       "Add its scheme_code in portfolio.json.")


def xirr(flows):
    """flows: [(date, amount)], investments negative. Annual rate in %, or None."""
    if len(flows) < 2 or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)

    def npv(r):
        return sum(a / (1 + r) ** ((d - t0).days / 365.0) for d, a in flows)
    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return mid * 100


def nav_on(hist, day):
    """NAV on the given day, or the last one before it."""
    best = None
    for d, v in hist:
        if d > day:
            break
        best = (d, v)
    return best


def cagr(hist, years):
    end_d, end_v = hist[-1]
    start = nav_on(hist, end_d - timedelta(days=round(365.25 * years)))
    if not start or (end_d - start[0]).days < 365.25 * years - 10:
        return None
    if years < 1:
        return (end_v / start[1] - 1) * 100
    return ((end_v / start[1]) ** (1 / years) - 1) * 100


def risk_stats(hist, years=3):
    end = hist[-1][0]
    pts = [(d, v) for d, v in hist if d >= end - timedelta(days=round(365.25 * years))]
    if len(pts) < 60:
        return None, None
    rets = [math.log(b[1] / a[1]) for a, b in zip(pts, pts[1:])]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(250) * 100
    peak, mdd = pts[0][1], 0.0
    for _, v in pts:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return vol, mdd * 100


def analyse(cfg, quiet=False):
    snap = date.fromisoformat(cfg["snapshot_date"])
    out, matches = [], []
    for f in cfg["funds"]:
        plan = f.get("plan", cfg.get("plan", "Direct"))
        code, matched, meta, hist = find_scheme(f, plan, snap)
        txns = f.get("transactions") or []
        if f.get("units"):
            units = float(f["units"])
        else:
            base = nav_on(hist, snap)
            if not base:
                raise RuntimeError(f"No NAV on or before {snap} for {f['name']}")
            units = f["value_on_snapshot"] / base[1]
        invested = sum(t["amount"] for t in txns) if txns else f["invested"]
        last_d, last_v = hist[-1]
        flows = [(date.fromisoformat(t["date"]), -t["amount"]) for t in txns]
        vol, mdd = risk_stats(hist)
        out.append({
            **f,
            "plan": plan,
            "invested": invested,
            "flows": flows,
            "xirr": xirr(flows + [(last_d, units * last_v)]) if flows else None,
            "code": code,
            "scheme": matched or meta.get("scheme_name", f["name"]),
            "units": units,
            "nav": last_v,
            "nav_date": last_d,
            "value": units * last_v,
            "r15d": (last_v / nav_on(hist, last_d - timedelta(days=15))[1] - 1) * 100,
            "r1y": cagr(hist, 1), "r3y": cagr(hist, 3), "r5y": cagr(hist, 5), "r10y": cagr(hist, 10),
            "vol": vol, "mdd": mdd,
            "inception": hist[0][0],
            "hist": hist,
        })
        matches.append(f"{f['name']} => {out[-1]['scheme']} [{code}] NAV {last_v:.2f} "
                       f"({last_d:%d %b}), value {inr(units * last_v)}, 3Y {pct(out[-1]['r3y'])}"
                       + (f", your XIRR {pct(out[-1]['xirr'])}" if out[-1]['xirr'] is not None else ""))
    for i in ([] if quiet else range(0, len(matches), 5)):  # GitHub shows max 10 notes per step
        gh_annotate("notice", "Matched funds: " + " | ".join(matches[i:i + 5]))
    return out


# ------------------------------------------------------------ projections --

def portfolio_rates(funds):
    total = sum(f["value"] for f in funds)
    return tuple(sum(f["value"] * CATEGORY_RATES[f["category"]][i] for f in funds) / total
                 for i in range(3))


def future_value(pv, rate, years, sip=0.0):
    r = rate / 100
    fv = pv * (1 + r) ** years
    if sip:
        m = (1 + r) ** (1 / 12) - 1
        fv += sip * (((1 + m) ** (years * 12) - 1) / m) * (1 + m)
    return fv


def projections(funds, cfg):
    total = sum(f["value"] for f in funds)
    invested = sum(f["invested"] for f in funds)
    sip = float(cfg.get("monthly_sip", 0) or 0)
    infl = float(cfg.get("inflation_pct", 6.0)) / 100
    rates = portfolio_rates(funds)
    rows = []
    for y in HORIZONS:
        cons, exp_, opt = (future_value(total, r, y, sip) for r in rates)
        put_in = invested + sip * 12 * y
        gain = exp_ - put_in
        tax = max(0.0, gain - LTCG_EXEMPT) * LTCG_RATE
        rows.append({"years": y, "cons": cons, "exp": exp_, "opt": opt,
                     "real": exp_ / (1 + infl) ** y, "after_tax": exp_ - tax, "tax": tax, "put_in": put_in})
    return rates, rows


# --------------------------------------------------------------- insights --

def portfolio_xirr(funds):
    known = [f for f in funds if f["flows"]]
    if not known:
        return None, 0.0
    end = max(f["nav_date"] for f in known)
    flows = [fl for f in known for fl in f["flows"]] + [(end, sum(f["value"] for f in known))]
    share = sum(f["value"] for f in known) / sum(f["value"] for f in funds)
    return xirr(flows), share


def rule_flags(funds):
    total = sum(f["value"] for f in funds)
    flags = []
    by_cat = {}
    for f in funds:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + f["value"]
    if len(funds) > 10:
        flags.append(f"{len(funds)} funds is more than most portfolios need (5–8 usually covers it). "
                     "Several funds in the same category mostly hold the same stocks.")
    small = by_cat.get("small", 0) / total * 100
    midsmall = small + by_cat.get("mid", 0) / total * 100
    if small > 25:
        flags.append(f"Small caps are {small:.0f}% of the portfolio. They can fall 30–50% in a bad year, "
                     "so this only suits a 7+ year horizon.")
    if midsmall > 45:
        flags.append(f"Mid + small caps together are {midsmall:.0f}%, an aggressive mix.")
    for cat in by_cat:
        same = [f for f in funds if f["category"] == cat]
        if len(same) >= 2 and cat not in ("sectoral",):
            names = ", ".join(f["name"] for f in same)
            flags.append(f"{len(same)} {CATEGORY_LABEL[cat].lower()} funds ({names}) likely overlap a lot.")
    regular = [f for f in funds if f["plan"].lower() == "regular"]
    if regular:
        names = ", ".join(f["name"] for f in regular)
        flags.append(f"{names} {'is a Regular plan' if len(regular) == 1 else 'are Regular plans'}: these pay "
                     "a distributor commission, typically 0.5–1% a year more than the Direct plan of the same fund. "
                     "Over 15–20 years that gap compounds into a noticeably smaller corpus.")
    tiny = [f["name"] for f in funds if f["value"] / total < 0.015]
    if tiny:
        flags.append(f"Very small holdings ({', '.join(tiny)}) barely move the total; consider merging them.")
    for f in funds:
        if f["category"] == "sectoral" and f["value"] / total > 0.03:
            flags.append(f"{f['name']} is a sector fund; these go through long dry spells, keep it a small slice.")
        if f["r3y"] is not None and f["r3y"] < 8:
            flags.append(f"{f['name']} has returned only {f['r3y']:.1f}% a year over 3 years, worth reviewing.")
    if all(f["category"] for f in funds) and not any(f["category"] in ("debt", "hybrid") for f in funds):
        flags.append("The portfolio is 100% equity with no debt or hybrid cushion for emergencies or near-term goals.")
    return flags


EXPERT_PROMPT = """You are an experienced Indian mutual fund analyst writing a fortnightly review for
an NRI investor (invests via NRO account, lives in Texas, long-term goals). Use ONLY the data below.

Write 6-8 short bullet points (max 45 words each), plain English, covering:
how the portfolio did recently, which funds are leading/lagging and why that matters,
risk level (volatility, drawdowns, category mix), overlap/over-diversification, and
2-3 concrete things worth considering (e.g. consolidating overlapping funds, adding a debt
or hybrid portion, SIP discipline, rebalancing). Frame suggestions as options to consider,
never as guaranteed outcomes or direct buy/sell orders. No intro or outro text.
Reply as JSON: {{"points": ["...", "..."]}}

DATA
{data}"""


def expert_review(funds, rates, rows, flags):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    total = sum(f["value"] for f in funds)
    data = {
        "total_value": round(total), "invested": round(sum(f["invested"] for f in funds)),
        "funds": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in {
            "name": f["name"], "plan": f["plan"], "your_xirr_pct": f["xirr"],
            "category": CATEGORY_LABEL[f["category"]], "weight_pct": f["value"] / total * 100,
            "value": f["value"], "invested": f["invested"], "ret_15d_pct": f["r15d"], "ret_1y_pct": f["r1y"],
            "cagr_3y_pct": f["r3y"], "cagr_5y_pct": f["r5y"], "volatility_pct": f["vol"],
            "max_drawdown_3y_pct": f["mdd"]}.items()} for f in funds],
        "assumed_return_pct": {"conservative": round(rates[0], 2), "expected": round(rates[1], 2),
                               "optimistic": round(rates[2], 2)},
        "projection_expected": {f"{r['years']}y": round(r["exp"]) for r in rows},
        "rule_based_flags": flags,
    }
    prompt = EXPERT_PROMPT.format(data=json.dumps(data, default=str))
    last = ""
    for model in gemini_models(key):
        try:
            r = requests.post(f"{GEMINI_API}/models/{model}:generateContent",
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                              json={"contents": [{"parts": [{"text": prompt}]}],
                                    "generationConfig": {"temperature": 0.3,
                                                         "responseMimeType": "application/json"}},
                              timeout=120)
            if r.status_code >= 400:
                last = f"{model} HTTP {r.status_code}: {r.text[:150]}"
                continue
            text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
            m = re.search(r"\{.*\}", text, re.S)
            points = [p for p in json.loads(m.group(0) if m else text)["points"] if isinstance(p, str)]
            if points:
                gh_annotate("notice", f"Expert review written by Gemini {model}")
                return points[:8]
        except Exception as exc:
            last = f"{model}: {type(exc).__name__}: {exc}"
    gh_annotate("warning", f"Gemini review failed, using rule-based notes only. {last}")
    return None


# ------------------------------------------------------------------ email --

TD = 'style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap"'
TDL = 'style="padding:6px 8px;border-bottom:1px solid #eee;text-align:left"'
TH = 'style="padding:6px 8px;border-bottom:2px solid #ddd;text-align:right;font-size:12px;color:#666"'
THL = 'style="padding:6px 8px;border-bottom:2px solid #ddd;text-align:left;font-size:12px;color:#666"'


def color(x):
    return "#16a34a" if x is not None and x >= 0 else "#dc2626"


def build_html(funds, cfg, rates, rows, flags, review, extra=None):
    total = sum(f["value"] for f in funds)
    invested = sum(f["invested"] for f in funds)
    gain = total - invested
    r15 = sum(f["value"] * f["r15d"] for f in funds) / total
    pxirr, covered = portfolio_xirr(funds)
    nav_date = max(f["nav_date"] for f in funds)
    sip = float(cfg.get("monthly_sip", 0) or 0)

    fund_rows = "".join(f"""<tr>
      <td {TDL}><b>{html.escape(f['name'])}</b><br><span style="font-size:11px;color:#888">{CATEGORY_LABEL[f['category']]} · {f['plan']} · {f['value'] / total * 100:.1f}%</span></td>
      <td {TD}>{short_inr(f['value'])}<br><span style="font-size:11px;color:{color(f['value'] - f['invested'])}">{pct((f['value'] / f['invested'] - 1) * 100)}</span></td>
      <td {TD}>{pct(f['xirr'])}</td>
      <td {TD} style="color:{color(f['r15d'])}">{pct(f['r15d'])}</td>
      <td {TD}>{pct(f['r1y'])}</td><td {TD}>{pct(f['r3y'])}</td><td {TD}>{pct(f['r5y'])}</td>
      <td {TD}>{'—' if f['vol'] is None else f"{f['vol']:.0f}%"}</td>
      <td {TD}>{'—' if f['mdd'] is None else f"{f['mdd']:.0f}%"}</td></tr>"""
        for f in sorted(funds, key=lambda f: -f["value"]))

    proj_rows = "".join(f"""<tr><td {TDL}><b>{r['years']} yrs</b></td>
      <td {TD}>{short_inr(r['cons'])}</td><td {TD}><b>{short_inr(r['exp'])}</b></td><td {TD}>{short_inr(r['opt'])}</td>
      <td {TD}>{short_inr(r['real'])}</td><td {TD}>{short_inr(r['after_tax'])}</td></tr>""" for r in rows)

    cat_tot = {}
    for f in funds:
        cat_tot[f["category"]] = cat_tot.get(f["category"], 0) + f["value"]
    mix = "".join(f"""<tr><td style="padding:3px 8px 3px 0;font-size:13px;white-space:nowrap">{CATEGORY_LABEL[c]}</td>
      <td style="width:100%;padding:3px 0"><div style="background:#eef2ff;border-radius:4px;height:10px"><div style="height:10px;border-radius:4px;background:#4f46e5;width:{v / total * 100:.1f}%"></div></div></td>
      <td style="padding:3px 0 3px 8px;font-size:13px;text-align:right">{v / total * 100:.0f}%</td></tr>"""
        for c, v in sorted(cat_tot.items(), key=lambda kv: -kv[1]))

    yearly = total * rates[1] / 100
    review_html = "".join(f"<li style='margin:6px 0'>{html.escape(p)}</li>" for p in (review or []))
    flags_html = "".join(f"<li style='margin:6px 0'>{html.escape(p)}</li>" for p in flags)
    sip_note = f" plus a {inr(sip)}/month SIP" if sip else " with no new money added (lump sum growing)"
    sec_top, sec_mid, sec_tax = rs.extra_sections(extra or {}, funds, rates, sip)

    return f"""<!doctype html><html><body style="margin:0;background:#f6f6f6">
<div style="max-width:720px;margin:0 auto;background:#fff;padding:22px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:14px;line-height:1.5;color:#1a1a1a">
  <h2 style="margin:0">📈 Portfolio Report</h2>
  <div style="color:#888;font-size:13px">{datetime.now(LOCAL_TZ):%B %d, %Y} · NAVs as of {nav_date:%d %b %Y}</div>

  <table style="width:100%;border-collapse:separate;margin:12px 0"><tr>
    <td style="width:25%;padding:4px;vertical-align:top"><div style="background:#f8fafc;border-radius:10px;padding:10px"><div style="font-size:12px;color:#666">Current value</div><div style="font-size:18px;font-weight:700">{inr(total)}</div></div></td>
    <td style="width:25%;padding:4px;vertical-align:top"><div style="background:#f8fafc;border-radius:10px;padding:10px"><div style="font-size:12px;color:#666">Invested</div><div style="font-size:18px;font-weight:700">{inr(invested)}</div></div></td>
    <td style="width:25%;padding:4px;vertical-align:top"><div style="background:#f8fafc;border-radius:10px;padding:10px"><div style="font-size:12px;color:#666">Total gain</div><div style="font-size:18px;font-weight:700;color:{color(gain)}">{inr(gain)}<br><span style="font-size:13px">{pct(gain / invested * 100)}</span></div></div></td>
    <td style="width:25%;padding:4px;vertical-align:top"><div style="background:#f8fafc;border-radius:10px;padding:10px"><div style="font-size:12px;color:#666">Last 15 days</div><div style="font-size:18px;font-weight:700;color:{color(r15)}">{pct(r15, 2)}</div></div></td>
  </tr></table>

  {sec_top}

  <h3 style="margin:22px 0 6px">🔮 Where it could be</h3>
  <p style="margin:0 0 8px;color:#444">Today's {short_inr(total)}{sip_note}. Assumed yearly returns, weighted by your fund mix:
  <b>{rates[0]:.1f}%</b> conservative, <b>{rates[1]:.1f}%</b> expected, <b>{rates[2]:.1f}%</b> optimistic.</p>
  <div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:13px">
    <tr><th {THL}>Horizon</th><th {TH}>Conservative</th><th {TH}>Expected</th><th {TH}>Optimistic</th><th {TH}>Expected, in today's money*</th><th {TH}>Expected, after LTCG tax**</th></tr>
    {proj_rows}</table></div>
  <p style="font-size:12px;color:#777;margin:6px 0">* Adjusted for {cfg.get('inflation_pct', 6)}% yearly inflation. ** If fully redeemed then: 12.5% on gains above ₹1.25 lakh, before surcharge/cess and NRI TDS rules.<br>
  Mutual funds don't pay fixed interest. At the expected rate, your portfolio would grow by roughly <b>{short_inr(yearly)}</b> in a typical year, but real years swing anywhere from about −30% to +40%.</p>

  {sec_mid}

  <h3 style="margin:22px 0 6px">🧭 Expert review</h3>
  {f"<ul style='padding-left:18px;margin:0'>{review_html}</ul>" if review_html else "<p style='color:#777'>AI review unavailable this time; see the checks below.</p>"}

  <h3 style="margin:22px 0 6px">⚠️ Checks</h3>
  <ul style="padding-left:18px;margin:0;color:#444">{flags_html}</ul>

  {sec_tax}

  <h3 style="margin:22px 0 6px">🧩 Category mix</h3>
  <table style="border-collapse:collapse;width:100%">{mix}</table>

  <h3 style="margin:22px 0 6px">📊 Fund by fund</h3>
  <div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:13px">
    <tr><th {THL}>Fund</th><th {TH}>Value / gain</th><th {TH}>Your XIRR</th><th {TH}>15 days</th><th {TH}>1 yr</th><th {TH}>3 yr/yr</th><th {TH}>5 yr/yr</th><th {TH}>Volatility</th><th {TH}>Worst fall (3y)</th></tr>
    {fund_rows}</table></div>
  <p style="font-size:12px;color:#777;margin:6px 0">"Your XIRR" is your personal yearly return from your actual purchase dates{'' if pxirr is None else f"; across funds with purchase history ({covered * 100:.0f}% of the portfolio) it is <b>{pxirr:.1f}%</b> a year"}. The other return columns are each fund's own NAV performance. 3 yr and 5 yr are yearly averages (CAGR). Volatility is yearly, over 3 years.</p>

  <p style="font-size:11px;color:#999;margin-top:22px;border-top:1px solid #eee;padding-top:10px">
  Data: AMFI NAVs via mfapi.in; holdings last updated {html.escape(cfg.get('updated', cfg['snapshot_date']))} (send a new statement after any buy or sell). Projections use fixed long-run assumptions, not forecasts; actual returns will differ, and past performance doesn't guarantee future results.
  This is an automated planning summary, not investment, tax or financial advice. Check tax rules with a tax professional before redeeming.</p>
</div></body></html>"""


def build_text(funds, rates, rows, flags, review):
    total = sum(f["value"] for f in funds)
    lines = [f"Portfolio value: {inr(total)}", "", "Projection (expected):"]
    lines += [f"  {r['years']} yrs: {short_inr(r['exp'])} (range {short_inr(r['cons'])} – {short_inr(r['opt'])})" for r in rows]
    lines += ["", "Review:"] + [f"- {p}" for p in (review or [])] + ["", "Checks:"] + [f"- {p}" for p in flags]
    return "\n".join(lines)


def build_extra(funds, cfg, rates, flags):
    """Benchmark check, Monte Carlo, tax calendar and portfolio-manager review. Each part is
    optional: if one fails, the report still goes out without it."""
    total = sum(f["value"] for f in funds)
    invested = sum(f["invested"] for f in funds)
    snap = date.fromisoformat(cfg["snapshot_date"])
    sip = float(cfg.get("monthly_sip", 0) or 0)
    extra, notes = {}, []
    for name, fn in [("bench", lambda: pa.benchmark_check(funds, snap)),
                     ("vol", lambda: pa.portfolio_volatility(funds)[0]),
                     ("tax", lambda: pa.tax_calendar(funds))]:
        try:
            extra[name] = fn()
        except Exception as exc:
            gh_annotate("warning", f"{name} section skipped: {type(exc).__name__}: {exc}")
    if "vol" in extra:
        extra["mc"] = pa.monte_carlo(total, invested, rates[1], extra["vol"], sip,
                                     seed=int(date.today().strftime("%Y%m%d")))
        notes.append(f"Vol {extra['vol'] * 100:.1f}%; MC median " + "; ".join(
            f"{r['years']}y {short_inr(r['p50'])} ({short_inr(r['p10'])}-{short_inr(r['p90'])})" for r in extra["mc"]))
    if "bench" in extra:
        notes.append("Bench: " + "; ".join(
            f"{k}: {v['verdict']}" + (f" {v['beat_pct']:.0f}%/{v['avg_excess']:+.1f}" if "beat_pct" in v else "")
            for k, v in extra["bench"].items()))
    if "tax" in extra:
        t = extra["tax"]
        notes.append(f"Tax: LT {inr(t['lt_gain'])}, ST {inr(t['st_gain'])}, switch tax {inr(t['switch_tax'])}, "
                     f"unknown {len(t['unknown'])}")
    try:
        extra["pm"] = pa.pm_review(funds, extra.get("bench", {}), extra.get("mc", []),
                                   extra.get("tax") or pa.tax_calendar(funds), flags, rates)
    except Exception as exc:
        gh_annotate("warning", f"PM review skipped: {type(exc).__name__}: {exc}")
    if notes:
        gh_annotate("notice", " || ".join(notes))
    return extra


# ------------------------------------------------------------------- main --

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config", default="portfolio.json")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    funds = analyse(cfg)
    rates, rows = projections(funds, cfg)
    flags = rule_flags(funds)
    review = expert_review(funds, rates, rows, flags)
    total = sum(f["value"] for f in funds)
    extra = build_extra(funds, cfg, rates, flags)
    gh_annotate("notice", f"Total {inr(total)}; expected rate {rates[1]:.1f}%; "
                + "; ".join(f"{r['years']}y {short_inr(r['exp'])}" for r in rows))

    html_body = build_html(funds, cfg, rates, rows, flags, review, extra)
    if args.dry_run:
        with open("portfolio_preview.html", "w", encoding="utf-8") as f:
            f.write(html_body)
        print("[info] Dry run: wrote portfolio_preview.html (not printed, logs may be visible)")
        return
    send_email(f"📈 Portfolio Report — {short_inr(total)} — {datetime.now(LOCAL_TZ):%b %d}",
               html_body, build_text(funds, rates, rows, flags, review))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        gh_annotate("error", f"{type(exc).__name__}: {exc}")
        sys.exit(1)
