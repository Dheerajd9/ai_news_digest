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
    # Google News catches companies without their own RSS (Anthropic, Apple, Meta)
    "Google News": (
        "https://news.google.com/rss/search?q=Anthropic+OR+%22Apple+Intelligence%22"
        "+OR+%22Meta+AI%22+OR+OpenAI+OR+Gemini+when:1d&hl=en-US&gl=US&ceid=US:en"
    ),
}
OFFICIAL = {"OpenAI", "Google AI", "Google DeepMind", "Microsoft AI", "Hugging Face"}
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
            # Google News titles end with " - Outlet"; keep the outlet as the source
            outlet = source
            if source == "Google News" and " - " in title:
                title, outlet = title.rsplit(" - ", 1)
            items.append({
                "title": title,
                "summary": clean(e.get("summary") or e.get("description")),
                "link": e.get("link", ""),
                "source": outlet,
                "official": source in OFFICIAL,
                "published": published,
            })
            count += 1
        report.append(f"{source}={count}/{len(feed.entries)}")
    gh_annotate("notice", "Feeds (recent/total): " + ", ".join(report))
    return dedupe(items)


def norm(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) > 3}


def dedupe(items):
    """Drop near-duplicate headlines (same story from several outlets)."""
    items.sort(key=lambda i: (not i["official"], -i["published"].timestamp()))
    kept = []
    for it in items:
        words = norm(it["title"])
        dup = any(len(words & norm(k["title"])) >= max(3, 0.6 * min(len(words), len(norm(k["title"]))))
                  for k in kept)
        if not dup:
            kept.append(it)
    return kept


def rank(items):
    """Simple fallback ranking: official blogs and big company names first."""
    def score(it):
        text = (it["title"] + " " + it["summary"]).lower()
        s = 3 if it["official"] else 0
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
outlets), minor announcements, opinion pieces, deals/coupons, and how-to articles.

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


def pick_with_ai(items):
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("[info] No GITHUB_TOKEN, skipping AI step", file=sys.stderr)
        return None
    listing = "\n".join(
        f"[{i}] ({it['source']}) {it['title']} — {it['summary'][:180]}"
        for i, it in enumerate(items)
    )
    prompt = PROMPT.format(n=TOP_N, items=listing)
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

def build_html(stories, edition, ai_used, total):
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


# ------------------------------------------------------------------- main --

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="write preview.html instead of emailing")
    args = parser.parse_args()

    edition = "Morning" if datetime.now(LOCAL_TZ).hour < 15 else "Evening"
    items = rank(fetch_items())[:MAX_CANDIDATES]
    stories = pick_with_ai(items) if items else None
    ai_used = stories is not None
    if not ai_used:
        stories = fallback_stories(items)
    gh_annotate("notice", f"{len(items)} candidate stories, {len(stories)} picked, AI summary used: {ai_used}")

    subject = f"🤖 AI News {edition} Digest — {datetime.now(LOCAL_TZ):%b %d}"
    html_body = build_html(stories, edition, ai_used, len(items))
    text_body = build_text(stories, edition)

    if args.dry_run:
        with open("preview.html", "w", encoding="utf-8") as f:
            f.write(html_body)
        print(text_body)
        print("[info] Dry run: wrote preview.html", file=sys.stderr)
    else:
        send_email(subject, html_body, text_body)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        gh_annotate("error", f"{type(exc).__name__}: {exc}")
        sys.exit(1)
