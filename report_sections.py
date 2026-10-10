"""HTML sections for the portfolio report email (portfolio manager view, benchmarks,
Monte Carlo chances, tax calendar). Kept separate so portfolio_report.py stays readable."""

import html

TD = 'style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap"'
TDL = 'style="padding:6px 8px;border-bottom:1px solid #eee;text-align:left"'
TH = 'style="padding:6px 8px;border-bottom:2px solid #ddd;text-align:right;font-size:12px;color:#666"'
THL = 'style="padding:6px 8px;border-bottom:2px solid #ddd;text-align:left;font-size:12px;color:#666"'
TONE = {"Keep": "#16a34a", "Keep, stop adding": "#0891b2", "Watch": "#d97706",
        "Consider consolidating": "#7c3aed", "Consider switching to Direct": "#2563eb",
        "Consider exiting": "#dc2626"}


def _inr(x):
    from portfolio_report import inr
    return inr(x)


def _short(x):
    from portfolio_report import short_inr
    return short_inr(x)


def _color(x):
    return "#16a34a" if x is not None and x >= 0 else "#dc2626"


def esc(x):
    return html.escape(str(x if x is not None else ""))


def h3(title):
    return f'<h3 style="margin:22px 0 6px">{title}</h3>'


def section_pm(pm):
    if not pm:
        return ("<p style='color:#777'>The portfolio-manager review couldn't be generated this time "
                "(AI service unavailable). The data sections below are still complete.</p>")
    frows = "".join(
        f"<tr><td {TDL}><b>{esc(x['name'])}</b></td>"
        f"<td {TDL}><span style=\"color:{TONE.get(x.get('action', ''), '#444')};font-weight:600\">{esc(x.get('action'))}</span></td>"
        f"<td {TDL}>{esc(x.get('why'))}</td></tr>"
        for x in pm["funds"])
    parts = [f"<p style='margin:0 0 10px;color:#333'>{esc(pm.get('summary'))}</p>",
             "<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%;font-size:13px'>",
             f"<tr><th {THL}>Fund</th><th {THL}>Suggestion</th><th {THL}>Why</th></tr>{frows}</table></div>"]
    target = pm.get("target") or []
    if target:
        trows = "".join(f"<tr><td {TDL}>{esc(t.get('bucket'))}</td><td {TD}><b>{esc(t.get('pct'))}%</b></td>"
                        f"<td {TDL}>{esc(t.get('funds'))}</td></tr>" for t in target)
        parts.append("<h4 style='margin:14px 0 4px'>Suggested simpler structure</h4>"
                     "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
                     f"<tr><th {THL}>Bucket</th><th {TH}>Target</th><th {THL}>Funds</th></tr>{trows}</table>")
    steps = "".join(f"<li style='margin:5px 0'>{esc(x)}</li>" for x in pm.get("steps") or [])
    if steps:
        parts.append(f"<h4 style='margin:14px 0 4px'>How to get there (tax-aware)</h4><ol style='padding-left:20px;margin:0'>{steps}</ol>")
    watch = "".join(f"<li style='margin:5px 0'>{esc(x)}</li>" for x in pm.get("watch") or [])
    if watch:
        parts.append(f"<h4 style='margin:14px 0 4px'>Watch before the next review</h4><ul style='padding-left:18px;margin:0'>{watch}</ul>")
    srcs = " · ".join(f"<a href='{esc(x['uri'])}' style='color:#2563eb'>{esc(x['title'])}</a>" for x in pm.get("sources") or [])
    if srcs:
        parts.append(f"<p style='font-size:11px;color:#888;margin:8px 0 0'>Research sources ({esc(pm.get('model'))}): {srcs}</p>")
    parts.append("<p style='font-size:11px;color:#888;margin:6px 0 0'>AI-generated suggestions to discuss and verify, "
                 "not personal investment advice. Check costs (exit load, tax in India and the US) before acting.</p>")
    return "".join(parts)


def section_bench(funds, bench):
    rows = []
    for f in sorted(funds, key=lambda f: -f["value"]):
        b = bench.get(f["name"])
        if not b:
            continue
        if "beat_pct" in b:
            col = "#16a34a" if b["beat_pct"] >= 50 else "#dc2626"
            rows.append(f"<tr><td {TDL}>{esc(f['name'])}</td><td {TDL}>{esc(b['index'])}</td>"
                        f"<td {TD}><span style='color:{_color(b['latest_excess'])}'>{b['latest_excess']:+.1f} pts</span></td>"
                        f"<td {TD}><b style='color:{col}'>{b['beat_pct']:.0f}%</b></td><td {TD}>{b['avg_excess']:+.1f} pts</td>"
                        f"<td {TDL}>{esc(b['verdict'])}</td></tr>")
        else:
            rows.append(f"<tr><td {TDL}>{esc(f['name'])}</td><td {TDL}>{esc(b.get('index', '—'))}</td>"
                        f"<td colspan='3' {TD}>—</td><td {TDL}>{esc(b['verdict'])}</td></tr>")
    return ("<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%;font-size:13px'>"
            f"<tr><th {THL}>Fund</th><th {THL}>Compared with</th><th {TH}>Latest 3 yr gap (per yr)</th>"
            f"<th {TH}>Beat index in</th><th {TH}>Avg gap</th><th {THL}>Verdict</th></tr>{''.join(rows)}</table></div>"
            "<p style='font-size:12px;color:#777;margin:6px 0'>\"Beat index in\" = share of all 3-year periods (checked weekly "
            "over up to 5 years) where the fund's yearly return beat the index. The index is measured with a low-cost index fund. "
            "A fund trailing for years is the signal worth acting on, not one bad quarter.</p>")


def section_mc(mc, vol, rate, sip):
    rows = []
    for r in mc:
        chances = " · ".join(f"{_short(m).replace('.00', '')}: {p:.0f}%"
                             for m, p in r["milestones"].items() if 3 < p < 99.5) or "—"
        rows.append(f"<tr><td {TDL}><b>{r['years']} yrs</b></td><td {TD}>{_short(r['p10'])}</td>"
                    f"<td {TD}><b>{_short(r['p50'])}</b></td><td {TD}>{_short(r['p90'])}</td><td {TD}>{chances}</td></tr>")
    sip_txt = f", with your {_inr(sip)}/month SIP" if sip else ""
    return (f"<p style='margin:0 0 8px;color:#444'>5,000 simulated futures using your portfolio's real ups and downs "
            f"(volatility <b>{vol * 100:.1f}%</b> a year over the last 3 years) around the {rate:.1f}% expected return{sip_txt}.</p>"
            "<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%;font-size:13px'>"
            f"<tr><th {THL}>Horizon</th><th {TH}>Bad case (1 in 10)</th><th {TH}>Middle</th><th {TH}>Good case (1 in 10)</th>"
            f"<th {TH}>Chance of reaching</th></tr>{''.join(rows)}</table></div>"
            f"<p style='font-size:12px;color:#777;margin:6px 0'>Chance of ending below the money you put in: "
            f"{mc[0]['loss_chance']:.0f}% at {mc[0]['years']} years, {mc[1]['loss_chance']:.0f}% at {mc[1]['years']} years. "
            "Real markets have fatter tails than this model, so treat the bad case as \"bad but not worst\".</p>")


def section_tax(tc):
    parts = []
    if tc["season"]:
        parts.append(f"<div style='background:#fef3c7;border-radius:8px;padding:10px;margin-bottom:8px'>⏰ "
                     f"<b>{tc['days_left']} days left in this financial year</b> (ends {tc['fy_end']:%d %b %Y}). "
                     "If you plan to rebalance or switch Regular plans to Direct, long-term gains up to ₹1.25 lakh "
                     "booked before then are tax-free.</div>")
    items = [f"<li>Unrealized <b>long-term</b> gains (held 12+ months): <b>{_inr(tc['lt_gain'])}</b>. Up to "
             f"<b>{_inr(tc['harvestable'])}</b> of this could be booked tax-free this financial year "
             "(₹1.25 lakh limit, assuming no other long-term gains this year).</li>",
             f"<li>Unrealized <b>short-term</b> gains (under 12 months): {_inr(tc['st_gain'])}. "
             "Selling these now would be taxed at about 20%.</li>"]
    if tc["soon"]:
        soon = "".join(f"<li>{esc(l['fund'])}: becomes long-term on {l['lt_on']:%d %b %Y} (gain now {_inr(l['gain'])})</li>"
                       for l in tc["soon"])
        items.append(f"<li>Turning long-term within 60 days:<ul>{soon}</ul></li>")
    if tc["regular_funds"]:
        items.append(f"<li><b>Regular → Direct switch</b> ({esc(', '.join(tc['regular_funds']))}): total gain "
                     f"{_inr(tc['regular_gain'])}; estimated Indian tax if switched this financial year with no other "
                     f"long-term gains booked: <b>{_inr(tc['switch_tax'])}</b>. Exit load is usually nil after 12 months; "
                     "TDS may still be deducted for NRIs and claimed back in your return.</li>")
    if tc["unknown"]:
        items.append(f"<li>Purchase dates unknown for {esc(', '.join(tc['unknown']))}, so they're not included here. "
                     "Send their transaction report to add them.</li>")
    parts.append(f"<ul style='padding-left:18px;margin:0;color:#333'>{''.join(items)}</ul>")
    parts.append("<p style='font-size:12px;color:#777;margin:6px 0'>India tax only, before surcharge/cess. As a US resident, "
                 "US tax on Indian funds (PFIC rules) may also apply; check with a cross-border tax professional.</p>")
    return "".join(parts)


def extra_sections(extra, funds, rates, sip):
    """Returns (top_html, middle_html, tax_html) to slot into the main report."""
    top = h3("🧑‍💼 Portfolio manager view") + section_pm(extra.get("pm"))
    middle = ""
    if extra.get("mc"):
        middle += h3("🎲 Chances, not just one number") + section_mc(extra["mc"], extra["vol"], rates[1], sip)
    if extra.get("bench"):
        middle += h3("🎯 Is each fund beating its index?") + section_bench(funds, extra["bench"])
    tax = h3("🧾 Tax calendar") + section_tax(extra["tax"]) if extra.get("tax") else ""
    return top, middle, tax
