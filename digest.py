"""
AI News Digest — collects the latest AI news from free RSS feeds, picks the
top 5 stories with a free AI model (GitHub Models), and emails them to you.

Runs on GitHub Actions twice a day. Everything used here is free:
  - RSS feeds (no API keys)
  - GitHub Models (free tier, uses the built-in GITHUB_TOKEN)
  - Gmail SMTP with an App Password

If the AI step fails for any reason, the email still goes out with the top
headlines picked by a simple ranking, so you never miss a digest.

Local test (no email sent, writes preview.html):
    python digest.py --dry-run
"""

import argparse
import html
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import feedparser
import requests

# ---------------------------------------------------------------- settings --

LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "14"))  # a little over 12h so cron delays don't leave gaps
TOP_N = int(os.getenv("TOP_N", "5"))
MODEL = os.getenv("MODEL", "openai/gpt-4o-mini")
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "America/Chicago"))
MAX_CANDIDATES = 40  # keeps the prompt inside the free tier's input limit

# Source name -> feed URL. Official company blogs get a priority boost.
FEEDS = {
    # Official company blogs
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google AI": "https://blog.google/technology/ai/rss/",
    "Google DeepMind": "https://deepmind.google/blog/rss.xml",
    "Microsoft AI": "https://blogs.microsoft.com/ai/feed/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    # Tech press
    "TechCrunch": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "Ars Technica": "https://arstechnica.com/ai/feed/",
    "VentureBeat": "https://venturebeat.com/category/ai/feed/",
    "MIT Tech Review": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "The Decoder": "https://the-decoder.com/feed/",
    "Wired": "https://www.wired.com/feed/tag/ai/latest/rss",
    # General tech/business feeds, filtered down to AI stories
    "CNBC Tech": "https://www.cnbc.com/id/19854910/device/rss/rss.html",
    "Axios": "https://api.axios.com/feed/",
    "Engadget": "https://www.engadget.com/rss.xml",
    # Hacker News posts about AI with 100+ points (links go to the original article)
    "Hacker News": ("https://hnrss.org/newest?points=100&q=AI+OR+LLM+OR+OpenAI+OR+Anthropic"
                    "+OR+Gemini+OR+Claude+OR+GPT"),
    # Google News catches companies without their own RSS (Anthropic, Apple, Meta)
    "Google News": (
        "https://news.google.com/rss/search?q=Anthropic+OR+%22Apple+Intelligence%22"
        "+OR+%22Meta+AI%22+OR+OpenAI+OR+Gemini+when:1d&hl=en-US&gl=US&ceid=US:en"
    ),
}
OFFICIAL = {"OpenAI", "Google AI", "Google DeepMind", "Microsoft AI", "Hugging Face"}
NEEDS_AI_FILTER = {"CNBC Tech", "Axios", "Engadget"}
AI_WORDS = re.compile(r"\b(ai|a\.i\.|artificial intelligence|llms?|openai|anthropic|claude|gemini|"
                      r"chatgpt|gpt-?\d*|copilot|deepmind|machine learning|chatbots?|nvidia|agents?)\b", re.I)
STATE_FILE = os.getenv("STATE_FILE", "sent.json")
STATE_DAYS = 5  # remember sent stories this long so they aren't repeated
BIG_NAMES = ["openai", "anthropic", "claude", "google", "gemini", "deepmind",
             "apple", "meta", "microsoft", "copilot", "nvidia", "gpt"]
API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "Content-Type": "application/json"}
HEADERS = {"User-Agent": "Mozilla/5.0 (ai-news-digest; +https://github.com)"}


# ------------------------------------------------------------- collecting --

def clean(text, limit=300):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


def entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def fetch_items():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    items = []
    report = []
    for source, url in FEEDS.items():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:  # one bad feed shouldn't break the digest
            print(f"[warn] {source}: {exc}", file=sys.stderr)
            report.append(f"{source}=ERR({type(exc).__name__})")
            continue
        count = 0
        for e in feed.entries:
            published = entry_time(e)
            if not published or published < cutoff:
                continue
            title = clean(e.get("title"), 200)
            summary = clean(e.get("summary") or e.get("description"))
            if source in NEEDS_AI_FILTER and not AI_WORDS.search(title + " " + summary):
                continue
            link = e.get("link", "")
            # Google News titles end with " - Outlet"; keep the outlet as the source
            outlet = source
            if source == "Google News" and " - " in title:
                title, outlet = title.rsplit(" - ", 1)
            if source == "Hacker News":
                outlet = re.sub(r"^www\.", "", link.split("/")[2]) if "//" in link else source
            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "source": outlet,
                "official": source in OFFICIAL,
                # 0 = company blog, 1 = publisher's own feed, 2 = Google News redirect link
                "tier": 0 if source in OFFICIAL else (2 if source == "Google News" else 1),
                "published": published,
            })
            count += 1
        report.append(f"{source}={count}/{len(feed.entries)}")
    gh_annotate("notice", "Feeds (recent/total): " + ", ".join(report))
    return dedupe(items)


def norm(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) > 3}


def similar(a, b):
    wa, wb = norm(a), norm(b)
    return len(wa & wb) >= max(3, 0.6 * min(len(wa), len(wb)))


def dedupe(items):
    """Drop near-duplicate headlines (same story from several outlets).
    Company blogs win, then the publisher's own feed, so links point to the original source."""
    items.sort(key=lambda i: (i["tier"], -i["published"].timestamp()))
    kept = []
    for it in items:
        dup = any(similar(it["title"], k["title"]) for k in kept)
        if not dup:
            kept.append(it)
    return kept


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            sent = json.load(f).get("sent", [])
    except (FileNotFoundError, ValueError):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=STATE_DAYS)
    return [s for s in sent if datetime.fromisoformat(s["at"]) >= cutoff]


def drop_already_sent(items, sent):
    links = {s["link"] for s in sent}
    titles = [s["title"] for s in sent]
    fresh = [it for it in items
             if it["link"] not in links and not any(similar(it["title"], t) for t in titles)]
    gh_annotate("notice", f"Skipped {len(items) - len(fresh)} stories already sent in the last {STATE_DAYS} days")
    return fresh


def save_state(sent, stories, items):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_link = {it["link"]: it["title"] for it in items}
    for st in stories:
        sent.append({"link": st["link"], "title": by_link.get(st["link"], st["headline"]), "at": now})
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"sent": sent}, f, indent=1)


def rank(items):
    """Simple fallback ranking: official blogs and big company names first."""
    def score(it):
        text = (it["title"] + " " + it["summary"]).lower()
        s = 3 if it["official"] else 0
        s -= 1 if it["tier"] == 2 else 0  # prefer stories with a direct publisher link
        s += sum(1 for n in BIG_NAMES if n in text)
        if re.search(r"launch|release|introduc|announc|unveil|model", text):
            s += 2
        return s
    return sorted(items, key=lambda it: (-score(it), -it["published"].timestamp()))


# ------------------------------------------------------------ AI picking --

PROMPT = """You are an AI news editor. From the numbered news items below, pick the {n} most
important artificial intelligence stories. Prioritize major AI product launches, new AI
models, important research, and significant announcements from companies like OpenAI,
Anthropic, Google, Apple, Meta, and Microsoft. Skip duplicates (same story from different
outlets; if two items are the same story, use the one that is NOT from Google News), minor announcements, opinion pieces, deals/coupons, and how-to articles.

For each story return:
- "id": the item number you used
- "headline": a short headline (max 12 words)
- "summary": what happened, in your own words (max 45 words)
- "why": why it matters (max 35 words)
Keep summary + why under 100 words total. Use only facts present in the items.

Reply with JSON only, in this exact shape:
{{"stories": [{{"id": 0, "headline": "", "summary": "", "why": ""}}]}}

News items:
{items}"""


# GitHub Models has served chat completions from two addresses; the working one is
# detected at runtime by find_endpoint().
ENDPOINTS = [
    "https://models.github.ai/inference/chat/completions",
    "https://models.inference.ai.azure.com/chat/completions",
]
ENDPOINT = ENDPOINTS[0]


def find_endpoint(token):
    """Return the first endpoint that answers a tiny request with real JSON."""
    global ENDPOINT
    notes = []
    for url in ENDPOINTS:
        for model in ("openai/gpt-4o-mini", "gpt-4o-mini", "openai/gpt-4.1-mini"):
            try:
                r = requests.post(url, headers={**API_HEADERS, "Authorization": f"Bearer {token}"},
                                  json={"model": model, "max_tokens": 5,
                                        "messages": [{"role": "user", "content": "Say hi"}]},
                                  timeout=30)
                r.json()["choices"]
                ENDPOINT = url
                gh_annotate("notice", f"AI endpoint OK: {url} ({model})")
                return model
            except Exception:
                notes.append(f"{url.split('/')[2]} {model}: HTTP {getattr(r, 'status_code', '?')} "
                             f"{(getattr(r, 'text', '') or '')[:60]!r}")
    gh_annotate("warning", "No AI endpoint answered: " + " | ".join(notes))
    return None


PREFERRED_MODELS = ["openai/gpt-4.1-mini", "openai/gpt-4o-mini", "openai/gpt-5-mini",
                    "openai/gpt-4.1", "openai/gpt-4o"]


def available_models(token):
    """Model IDs currently offered by GitHub Models (empty list if the catalog can't be read)."""
    try:
        resp = requests.get("https://models.github.ai/catalog/models",
                            headers={**API_HEADERS, "Authorization": f"Bearer {token}"}, timeout=20)
        resp.raise_for_status()
        try:
            return [m["id"] for m in resp.json()]
        except ValueError:
            gh_annotate("warning", f"Catalog HTTP {resp.status_code}, non-JSON "
                        f"({resp.headers.get('content-type')}): {resp.text[:150]!r}")
            return []
    except Exception as exc:
        gh_annotate("warning", f"Couldn't read GitHub Models catalog: {exc}")
        return []


def model_candidates(token):
    catalog = available_models(token)
    order = [MODEL] + [m for m in PREFERRED_MODELS if m != MODEL]
    if not catalog:
        return order
    picks = [m for m in order if m in catalog]
    # Anything else from OpenAI in the catalog, smallest first, as a last resort
    extras = sorted((m for m in catalog if m.startswith("openai/") and m not in picks),
                    key=lambda m: ("mini" not in m and "nano" not in m, m))
    return picks + extras[:3]


def parse_stories(content, items):
    """Turn the model's JSON reply into stories; links always come from the feeds."""
    match = re.search(r"\{.*\}", content, re.S)  # tolerate ```json fences or extra text
    stories = []
    for s in json.loads(match.group(0) if match else content)["stories"][:TOP_N]:
        idx = int(s["id"])
        if 0 <= idx < len(items):
            stories.append({**s, "link": items[idx]["link"], "source": items[idx]["source"]})
    return stories


GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"


def gemini_models(key):
    """Free 'flash' models that can generate text, newest first."""
    preferred = os.getenv("GEMINI_MODEL", "").strip()
    names = []
    try:
        r = requests.get(f"{GEMINI_API}/models", headers={"x-goog-api-key": key},
                         params={"pageSize": 200}, timeout=20)
        r.raise_for_status()
        for m in r.json().get("models", []):
            name = m["name"].split("/", 1)[-1]
            if ("generateContent" in m.get("supportedGenerationMethods", [])
                    and "flash" in name and not re.search(r"image|tts|audio|live|thinking|exp", name)):
                names.append(name)
    except Exception as exc:
        gh_annotate("warning", f"Couldn't list Gemini models: {type(exc).__name__}: {str(exc)[:150]}")

    def version(n):
        nums = re.findall(r"\d+(?:\.\d+)?", n)
        return float(nums[0]) if nums else 0.0
    names.sort(key=lambda n: (-version(n), "lite" in n, "preview" in n, n))
    fallback = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]
    order = ([preferred] if preferred else []) + names + fallback
    return list(dict.fromkeys(order))[:4]


def pick_with_gemini(prompt, items):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    last_error = ""
    for model in gemini_models(key):
        r = None
        try:
            r = requests.post(
                f"{GEMINI_API}/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.2,
                                           "responseMimeType": "application/json"}},
                timeout=90,
            )
            if r.status_code >= 400:
                last_error = f"{model} HTTP {r.status_code}: {r.text[:200]}"
                continue
            parts = r.json()["candidates"][0]["content"]["parts"]
            stories = parse_stories("".join(p.get("text", "") for p in parts), items)
            if stories:
                gh_annotate("notice", f"Stories picked by Gemini {model}")
                return stories
            last_error = f"{model}: no usable stories in reply"
        except Exception as exc:
            body = (r.text[:150] if r is not None else "")
            last_error = f"{model}: {type(exc).__name__}: {exc} {body!r}"
    gh_annotate("warning", f"Gemini failed. Last error: {last_error}")
    return None


def pick_with_ai(items):
    listing = "\n".join(
        f"[{i}] ({it['source']}{' via Google News' if it['tier'] == 2 else ''}) "
        f"{it['title']} — {it['summary'][:180]}"
        for i, it in enumerate(items)
    )
    prompt = PROMPT.format(n=TOP_N, items=listing)
    stories = pick_with_gemini(prompt, items)
    if stories:
        return stories
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("[info] No AI key available, skipping AI step", file=sys.stderr)
        return None
    if not os.getenv("GEMINI_API_KEY"):
        gh_annotate("notice", "No GEMINI_API_KEY secret yet, trying GitHub Models")
    last_error = ""
    working = find_endpoint(token)
    if not working:
        return None
    candidates = model_candidates(token)
    if "azure.com" in ENDPOINT:  # this endpoint uses bare model names
        candidates = [m.split("/", 1)[-1] for m in candidates]
    candidates = [working] + [m for m in candidates if m != working]
    for model in candidates[:4]:
        stories, last_error = call_model(token, model, prompt, items)
        if stories:
            gh_annotate("notice", f"Stories picked by {model}")
            return stories
    gh_annotate("warning", f"AI step failed, sent plain headlines instead. Last error: {last_error}")
    return None


def call_model(token, model, prompt, items):
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }
    error = ""
    for attempt in range(2):
        try:
            resp = requests.post(
                ENDPOINT,
                headers={**API_HEADERS, "Authorization": f"Bearer {token}"},
                json=body, timeout=90,
            )
            if resp.status_code >= 400:
                error = f"{model} HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code in (400, 401, 403, 404):  # not worth retrying this model
                    return None, error
                raise RuntimeError(error)
            try:
                content = resp.json()["choices"][0]["message"]["content"]
            except ValueError:
                return None, (f"{model} HTTP {resp.status_code}, non-JSON reply "
                              f"({resp.headers.get('content-type')}): {resp.text[:150]!r}")
            match = re.search(r"\{.*\}", content, re.S)  # tolerate ```json fences or extra text
            stories = []
            for s in json.loads(match.group(0) if match else content)["stories"][:TOP_N]:
                idx = int(s["id"])
                if 0 <= idx < len(items):  # links always come from the feeds, never the model
                    stories.append({**s, "link": items[idx]["link"], "source": items[idx]["source"]})
            if stories:
                return stories, ""
            error = f"{model}: no usable stories in reply"
        except Exception as exc:
            error = error or f"{model}: {type(exc).__name__}: {exc}"
            print(f"[warn] AI attempt {attempt + 1} failed: {exc}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    return None, error


def fallback_stories(items):
    return [{
        "headline": it["title"],
        "summary": it["summary"][:280] or "Open the link for details.",
        "why": "",
        "link": it["link"],
        "source": it["source"],
    } for it in items[:TOP_N]]


# --------------------------------------------------------------- emailing --

def build_html(stories, edition, ai_used, total, portfolio_line=""):
    now = datetime.now(LOCAL_TZ)
    rows = []
    for n, s in enumerate(stories, 1):
        why = (f'<p style="margin:6px 0 0;color:#444"><b>Why it matters:</b> '
               f'{html.escape(s["why"])}</p>') if s.get("why") else ""
        rows.append(f"""
        <div style="padding:16px 0;border-bottom:1px solid #eee">
          <div style="font-size:12px;color:#888">{n} · {html.escape(s['source'])}</div>
          <h3 style="margin:4px 0 6px;font-size:17px;line-height:1.3">
            <a href="{html.escape(s['link'])}" style="color:#1a1a1a;text-decoration:none">{html.escape(s['headline'])}</a>
          </h3>
          <p style="margin:0;color:#333">{html.escape(s['summary'])}</p>
          {why}
          <p style="margin:8px 0 0"><a href="{html.escape(s['link'])}" style="color:#2563eb">Read the source →</a></p>
        </div>""")
    note = "" if ai_used else (
        '<p style="color:#b45309;font-size:13px">AI summary unavailable this run, '
        'so these are the top headlines by ranking.</p>')
    return f"""<!doctype html><html><body style="margin:0;background:#f6f6f6">
    <div style="max-width:640px;margin:0 auto;background:#fff;padding:24px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;line-height:1.5">
      <h2 style="margin:0">🤖 AI News — {edition} Edition</h2>
      <div style="color:#888;font-size:13px">{now:%A, %B %d, %Y · %I:%M %p %Z} · picked from {total} stories in the last {LOOKBACK_HOURS}h</div>
      {portfolio_line}
      {note}
      {''.join(rows) if rows else '<p>No major AI stories in this window.</p>'}
      <p style="color:#aaa;font-size:12px;margin-top:20px">Sent by your GitHub Actions AI News Digest.</p>
    </div></body></html>"""


def build_text(stories, edition):
    lines = [f"AI News — {edition} Edition", ""]
    for n, s in enumerate(stories, 1):
        lines += [f"{n}. {s['headline']} ({s['source']})", s["summary"]]
        if s.get("why"):
            lines.append(f"Why it matters: {s['why']}")
        lines += [s["link"], ""]
    return "\n".join(lines)


def gh_annotate(level, message):
    """Show a message on the GitHub Actions run summary page (never include secret values)."""
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::{level}::{message}")
    print(f"[{level}] {message}", file=sys.stderr)


def send_email(subject, html_body, text_body):
    user = os.getenv("GMAIL_USER", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    to = os.getenv("TO_EMAIL", "").strip() or user
    missing = [name for name, val in (("GMAIL_USER", user), ("GMAIL_APP_PASSWORD", password)) if not val]
    if missing:
        raise RuntimeError(f"Missing repository secret(s): {', '.join(missing)}. "
                           "Check names under Settings > Secrets and variables > Actions.")
    if "@" not in user:
        raise RuntimeError("GMAIL_USER doesn't look like an email address.")
    if len(password) != 16:
        gh_annotate("warning", f"GMAIL_APP_PASSWORD has {len(password)} characters; a Gmail app password has 16.")
    recipients = [a.strip() for a in to.split(",") if a.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.sendmail(user, recipients, msg.as_string())
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError("Gmail rejected the login. Check GMAIL_USER is the same Gmail that "
                           "created the app password, and GMAIL_APP_PASSWORD is the 16-letter app "
                           "password (not your normal Gmail password).") from None
    gh_annotate("notice", f"Email sent to {len(recipients)} recipient(s)")


# -------------------------------------------------------- portfolio extras --

ALERT_STATE = "alerts.json"


def portfolio_morning(dry_run):
    """Daily portfolio line for the morning email, plus smart alerts. Never breaks the digest."""
    if not os.path.exists("portfolio.json"):
        return ""
    try:
        import portfolio_analytics as pa
        import portfolio_report as pr
        with open("portfolio.json", encoding="utf-8") as f:
            cfg = json.load(f)
        funds = pr.analyse(cfg, quiet=True)
        snap = pa.daily_snapshot(funds)
        c = lambda x: "#16a34a" if x >= 0 else "#dc2626"
        line = (f'<div style="margin:12px 0 4px;padding:10px 12px;background:#f8fafc;border-radius:10px;font-size:14px">'
                f'💼 <b>Portfolio {pr.inr(snap["total"])}</b> · '
                f'<span style="color:{c(snap["day_pct"])}">{snap["day_pct"]:+.2f}% ({pr.inr(snap["day_abs"])}) last day</span> · '
                f'<span style="color:{c(snap["mtd_pct"])}">{snap["mtd_pct"]:+.1f}% this month</span>'
                f'<span style="color:#999;font-size:12px"> · NAVs of {snap["nav_date"]:%d %b}</span></div>')
        gh_annotate("notice", f"Portfolio {pr.inr(snap['total'])}, day {snap['day_pct']:+.2f}%, "
                    f"week {snap['week_pct']:+.1f}%, month {snap['mtd_pct']:+.1f}%")
        try:
            with open(ALERT_STATE, encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, ValueError):
            state = {}
        fresh, new_state = pa.alerts(funds, state)
        if fresh:
            body = "".join(f"<p style='margin:10px 0'>⚠️ {html.escape(m)}</p>" for _, m in fresh)
            html_alert = (f'<!doctype html><html><body style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
                          f'font-size:15px;line-height:1.5;max-width:640px;margin:0 auto;padding:20px">'
                          f'<h2 style="margin:0 0 6px">⚠️ Portfolio alert</h2>{body}'
                          f'<p style="color:#888;font-size:12px">Automated alert from your portfolio tracker. '
                          f'Not investment advice. Big falls are usually a reason to check, not to rush.</p></body></html>')
            gh_annotate("warning", f"{len(fresh)} portfolio alert(s): " + " | ".join(k for k, _ in fresh))
            if not dry_run:
                send_email(f"⚠️ Portfolio alert — {datetime.now(LOCAL_TZ):%b %d}", html_alert,
                           "\n\n".join(m for _, m in fresh))
                with open(ALERT_STATE, "w", encoding="utf-8") as f:
                    json.dump(new_state, f, indent=1)
        return line
    except Exception as exc:
        gh_annotate("warning", f"Portfolio line skipped: {type(exc).__name__}: {exc}")
        return ""


# ------------------------------------------------------------------- main --

TARGET_HOURS = {7: "Morning", 18: "Evening"}  # local Texas time


def scheduled_local_time():
    """Local time a scheduled run was *meant* for (GitHub can start runs late)."""
    cron = os.getenv("SCHEDULE", "").split()
    if os.getenv("GITHUB_EVENT_NAME") != "schedule" or len(cron) < 2:
        return None
    now = datetime.now(timezone.utc)
    planned = now.replace(hour=int(cron[1]), minute=int(cron[0]), second=0, microsecond=0)
    if planned > now:  # run started after midnight UTC for a slot planned the day before
        planned -= timedelta(days=1)
    return planned.astimezone(LOCAL_TZ)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="write preview.html instead of emailing")
    args = parser.parse_args()

    planned = scheduled_local_time()
    if planned is not None:
        if planned.hour not in TARGET_HOURS:
            # Two cron slots exist per edition (summer/winter UTC offset); only one matches today.
            print(f"[info] Slot is {planned:%H:%M %Z}, not a digest time today. Skipping.", file=sys.stderr)
            return
        edition = TARGET_HOURS[planned.hour]
    else:
        edition = "Morning" if datetime.now(LOCAL_TZ).hour < 15 else "Evening"
    sent = load_state()
    items = rank(drop_already_sent(fetch_items(), sent))[:MAX_CANDIDATES]
    stories = pick_with_ai(items) if items else None
    ai_used = stories is not None
    if not ai_used:
        stories = fallback_stories(items)
    gh_annotate("notice", f"{len(items)} candidate stories, {len(stories)} picked, AI summary used: {ai_used}")

    subject = f"🤖 AI News {edition} Digest — {datetime.now(LOCAL_TZ):%b %d}"
    portfolio_line = portfolio_morning(args.dry_run) if edition == "Morning" or args.dry_run else ""
    html_body = build_html(stories, edition, ai_used, len(items), portfolio_line)
    text_body = build_text(stories, edition)

    if args.dry_run:
        with open("preview.html", "w", encoding="utf-8") as f:
            f.write(html_body)
        print(text_body)
        print("[info] Dry run: wrote preview.html", file=sys.stderr)
    else:
        send_email(subject, html_body, text_body)
        save_state(sent, stories, items)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        gh_annotate("error", f"{type(exc).__name__}: {exc}")
        sys.exit(1)
