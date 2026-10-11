"""
World Brief: India-first daily news digest with an audio podcast. All free.

Editions (EDITION env):
  morning  full brief, 13 sections, ~9 min read + ~10 min podcast
  evening  what changed since morning, ~3 min read + ~4 min podcast
  weekly   Sunday deep dive built from the week's briefs + ~14 min podcast

Pipeline (morning/evening):
  1. Fetch RSS feeds (feeds.yaml) from the last LOOKBACK_HOURS.
  2. Drop items already sent (state/seen.json); merge near-duplicate headlines and
     count how many outlets ran each story ("coverage" = importance signal).
  3. Cap items per category (India gets the most room).
  4. Exact numbers box: markets + weather from free data APIs (markets.py), never from AI.
  5. Story memory: the last few archived briefs go in as context, so continuing stories
     become short "Update:" bullets instead of repeats.
  6. Gemini (free) writes the brief; every bullet cites ids like [S12].
  7. Fact-check pass removes or softens anything the cited sources don't support.
  8. Ids become real links; podcast.py turns the brief into a two-host MP3.
  9. Email (Gmail) + optional Telegram; archive in briefs/; episode.json for podfeed.py.

Env vars:
  GEMINI_API_KEY        free Google AI Studio key (GEMINI_MODEL optional, auto-picked)
  GITHUB_TOKEN          built-in in Actions; GitHub Models = free second brain
  GMAIL_USER, GMAIL_APP_PASSWORD, TO_EMAIL     email delivery
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID         optional
  EDITION               morning (default) | evening | weekly
  PODCAST=1 (default)   also make an MP3 podcast and attach it
  DRY_RUN=1             build everything, send nothing, save nothing
  NO_LLM=1              skip AI (test runs, so free quota isn't spent)
"""
from __future__ import annotations

import calendar
import hashlib
import html
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.mime.audio import MIMEAudio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import markdown
import requests
import yaml

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state" / "seen.json"
ARCHIVE_DIR = ROOT / "briefs"
TZ = ZoneInfo(os.getenv("BRIEF_TZ", "America/Chicago"))

LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "26"))
MAX_PER_CATEGORY = int(os.getenv("MAX_PER_CATEGORY", "25"))
# India gets more room than the default; everything else uses MAX_PER_CATEGORY.
CATEGORY_CAPS = {"india": 40, "india_economy": 25, "telangana_hyderabad": 12,
                 "usa": 30, "india_us_visa_trade": 25, "world": 30}
SUMMARY_CHARS = 280
SEEN_TTL_DAYS = 7
UA = {"User-Agent": "world-brief/1.0 (personal news digest)"}


# ---------------------------------------------------------------- fetch
def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


HOST_NAMES = {
    "indianexpress": "Indian Express", "hindustantimes": "Hindustan Times",
    "timesofindia": "Times of India", "livemint": "Mint", "economictimes": "Economic Times",
    "thehindu": "The Hindu", "bbci": "BBC", "aljazeera": "Al Jazeera", "theguardian": "The Guardian",
    "dw.com": "DW", "france24": "France 24", "npr.org": "NPR", "nhk.or.jp": "NHK World",
    "arstechnica": "Ars Technica", "technologyreview": "MIT Tech Review", "hnrss": "Hacker News",
    "nature.com": "Nature", "sciencedaily": "ScienceDaily", "freightwaves": "FreightWaves",
}


def short_source(name: str) -> str:
    """'World news | The Guardian' -> 'The Guardian'; 'Al Jazeera – Breaking News...' -> 'Al Jazeera'."""
    name = name.strip()
    for sep in (" | ", " -- "):
        if sep in name:
            name = name.split(sep)[-1].strip()
    for sep in (" – ", " - ", ": "):
        if sep in name:
            name = name.split(sep)[0].strip()
    return {"World": "DW", "mint": "Mint"}.get(name, name)[:30]


def entry_time(e) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def fetch_items(feeds: dict[str, list[str]]) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    items = []
    for category, urls in feeds.items():
        for url in urls:
            try:
                resp = requests.get(url, headers=UA, timeout=20)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
            except Exception as exc:  # one bad feed must not kill the run
                print(f"  ! feed failed: {url} ({exc})", file=sys.stderr)
                continue
            host = url.split("/")[2]
            source = next((n for h, n in HOST_NAMES.items() if h in host), None) \
                or short_source(clean(parsed.feed.get("title", "")) or host)
            kept = 0
            for e in parsed.entries:
                ts = entry_time(e)
                if ts and ts < cutoff:
                    continue
                title = clean(e.get("title", ""))
                link = e.get("link", "")
                if not title or not link:
                    continue
                # Google News titles end with " - Outlet"; use that as the real source.
                src = source
                if "news.google.com" in url and " - " in title:
                    title, src = title.rsplit(" - ", 1)
                    src = short_source(src)
                items.append({
                    "category": category,
                    "source": src[:60],
                    "title": title[:200],
                    "summary": clean(e.get("summary", ""))[:SUMMARY_CHARS],
                    "published": ts.isoformat() if ts else "",
                    "link": link,
                })
                kept += 1
            print(f"  {kept:3d} items  {category:28s} {source[:40]}")
    return items


# ---------------------------------------------------------------- dedupe
def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower())


def key_of(item: dict) -> str:
    return hashlib.sha1(norm_title(item["title"]).encode()).hexdigest()[:16]


def load_seen() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    seen = json.loads(STATE_FILE.read_text())
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_TTL_DAYS)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


def dedupe(items: list[dict], seen: dict[str, str]) -> list[dict]:
    # Newest first so the freshest version of a story survives.
    items.sort(key=lambda i: i["published"], reverse=True)
    # Near-duplicate headlines are merged, but we remember how many outlets ran the
    # story ("coverage"). Wide coverage is a strong importance signal for the LLM.
    out, titles = [], []
    for it in items:
        if key_of(it) in seen:
            continue
        nt = norm_title(it["title"])
        match = next((k for k, t in enumerate(titles)
                      if SequenceMatcher(None, nt, t).ratio() > 0.8), None)
        if match is not None:
            kept = out[match]
            if it["source"] not in kept["also"]:
                kept["also"].append(it["source"])
            continue
        it["also"] = []
        titles.append(nt)
        out.append(it)
    for it in out:
        it["coverage"] = 1 + len(it["also"])
    return out


def cap_per_category(items: list[dict]) -> list[dict]:
    by_cat: dict[str, list[dict]] = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)
    capped = []
    for cat, cat_items in by_cat.items():
        limit = CATEGORY_CAPS.get(cat, MAX_PER_CATEGORY)
        # Round-robin across sources so one outlet cannot fill a category.
        by_src: dict[str, list[dict]] = {}
        for it in cat_items:
            by_src.setdefault(it["source"], []).append(it)
        queues = list(by_src.values())
        picked = []
        while queues and len(picked) < limit:
            for q in list(queues):
                if q:
                    picked.append(q.pop(0))
                if not q:
                    queues.remove(q)
                if len(picked) >= limit:
                    break
        capped.extend(picked)
    for n, it in enumerate(capped, 1):
        it["id"] = f"S{n}"
    return capped


# ---------------------------------------------------------------- history
def on_this_day(today: datetime) -> list[dict]:
    url = (f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/selected/"
           f"{today.month:02d}/{today.day:02d}")
    try:
        data = requests.get(url, headers=UA, timeout=20).json()
        return [{"year": e.get("year"), "text": e.get("text", "")[:250]}
                for e in data.get("selected", [])[:15]]
    except Exception as exc:
        print(f"  ! on-this-day failed ({exc})", file=sys.stderr)
        return []


# ---------------------------------------------------------------- LLM
# All providers are free: Gemini free tier (your key) and GitHub Models (built-in
# GITHUB_TOKEN). Quota plan: Gemini (big context) writes the main brief; GitHub Models
# (small context, separate quota) does fact-check and podcast script when they fit.
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
GH_MODELS_MAX_TOKENS = 7000      # free tier caps input near 8k tokens


def est_tokens(*texts: str) -> int:
    return sum(len(t) for t in texts) // 4


def ask(system: str, user: str, prefer: str = "gemini") -> str | None:
    """Free LLM call. prefer='github' saves Gemini quota for small jobs."""
    fits_gh = est_tokens(system, user) < GH_MODELS_MAX_TOKENS
    order = [call_gemini, call_github_models]
    if prefer == "github" and fits_gh:
        order.reverse()
    for fn in order:
        if fn is call_github_models and not fits_gh:
            continue
        text = fn(system, user)
        if text:
            return text
    return None


def strip_links(md: str) -> str:
    return re.sub(r"\s?\[\[([^\]]+)\]\]\([^)]+\)", "", md)


def previous_briefs(days: int, max_chars: int, keep_links: bool = False) -> str:
    """Story memory: the daily briefs this reader already got (oldest first)."""
    if not ARCHIVE_DIR.exists():
        return ""
    cutoff = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    files = sorted(p for p in ARCHIVE_DIR.glob("*.md")
                   if p.name[:10] >= cutoff and "weekly" not in p.name)
    chunks = []
    for p in files:
        text = p.read_text()
        if not keep_links:
            text = strip_links(text)
        chunks.append(f"=== {p.stem} ===\n{text.strip()}")
    joined = "\n\n".join(chunks)
    return joined[-max_chars:]          # keep the newest if too long


def build_user(items: list[dict], history: list[dict], date_str: str,
               markets: str, memory: str, compact: bool = False) -> str:
    if compact:
        # GitHub Models' free tier caps input near 8k tokens: titles only, India first,
        # most-covered first, and only the latest previous brief.
        order = list(CATEGORY_CAPS) + sorted({i["category"] for i in items} - set(CATEGORY_CAPS))
        ranked = sorted(items, key=lambda i: (order.index(i["category"]), -i["coverage"]))
        per_cat: dict[str, int] = {}
        payload = []
        for it in ranked:
            if per_cat.get(it["category"], 0) >= 7:
                continue
            per_cat[it["category"]] = per_cat.get(it["category"], 0) + 1
            payload.append({k: it[k] for k in ("id", "category", "source", "coverage", "title")})
        history, memory = history[:5], memory[-1500:]
    else:
        payload = [{k: it[k] for k in ("id", "category", "source", "coverage", "title",
                                        "summary", "published")} for it in items]
    return (f"DATE: {date_str}\n\nMARKETS: {markets or '(none)'}\n\n"
            f"NEWS_ITEMS:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            f"ON_THIS_DAY:\n{json.dumps(history, ensure_ascii=False)}\n\n"
            f"PREVIOUS_BRIEFS:\n{memory or '(none)'}")


def write_brief(system: str, items, history, date_str, markets, memory) -> str | None:
    full = build_user(items, history, date_str, markets, memory)
    print(f"  brief prompt ~{est_tokens(system, full):,} tokens")
    return (call_gemini(system, full)
            or call_github_models(system, build_user(items, history, date_str, markets,
                                                     memory, compact=True)))


def fact_check(draft: str, items: list[dict]) -> str:
    """Second pass: drop or soften anything the cited sources don't support."""
    cited = set(re.findall(r"\[(S\d+)\]", draft))
    if not cited:
        return draft
    src = [{"id": i["id"], "title": i["title"], "summary": i["summary"][:200]}
           for i in items if i["id"] in cited]
    system = (ROOT / "prompts" / "factcheck_prompt.md").read_text()
    user = f"SOURCE_ITEMS:\n{json.dumps(src, ensure_ascii=False)}\n\nDRAFT:\n{draft}"
    checked = ask(system, user, prefer="github")
    if not checked:
        print("  ! fact-check unavailable; sending the unchecked draft")
        return draft
    checked = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", checked.strip())
    # Guardrails: a checker that wipes out most of the brief or its headings is wrong.
    heads = lambda t: len(re.findall(r"^## ", t, re.M))
    if len(checked) < 0.6 * len(draft) or heads(checked) < heads(draft) - 1:
        print("  ! fact-check removed too much; keeping the draft")
        return draft
    removed = len(re.findall(r"^\s*- ", draft, re.M)) - len(re.findall(r"^\s*- ", checked, re.M))
    print(f"  fact-check done ({max(removed, 0)} bullet(s) removed)")
    return checked


def write_script(system: str, user: str) -> str | None:
    """Podcast script writer (GitHub Models first, to save Gemini quota)."""
    return ask(system, user, prefer="github")


def gemini_models(key: str) -> list[str]:
    """Free Flash text models, newest first (names change often, so ask the API)."""
    preferred = os.getenv("GEMINI_MODEL", "").strip()
    names = []
    try:
        r = requests.get(f"{GEMINI_API}/models", headers={"x-goog-api-key": key},
                         params={"pageSize": 200}, timeout=20)
        r.raise_for_status()
        for m in r.json().get("models", []):
            name = m["name"].split("/", 1)[-1]
            if ("generateContent" in m.get("supportedGenerationMethods", [])
                    and "flash" in name
                    and not re.search(r"image|tts|audio|live|thinking|exp|transcribe", name)):
                names.append(name)
    except Exception as exc:
        print(f"  ! couldn't list Gemini models: {exc}", file=sys.stderr)

    def version(n):
        nums = re.findall(r"\d+(?:\.\d+)?", n)
        return float(nums[0]) if nums else 0.0
    names.sort(key=lambda n: (-version(n), "lite" in n, "preview" in n, n))
    order = ([preferred] if preferred else []) + names + ["gemini-flash-latest"]
    return list(dict.fromkeys(order))[:4]


def call_gemini(system: str, user: str) -> str | None:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    for model in gemini_models(key):
        for _ in range(2):
            try:
                r = requests.post(
                    f"{GEMINI_API}/models/{model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json={"system_instruction": {"parts": [{"text": system}]},
                          "contents": [{"role": "user", "parts": [{"text": user}]}],
                          "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192}},
                    timeout=180)
                if r.status_code == 429:          # free-tier rate limit: wait, retry once
                    time.sleep(30)
                    continue
                if r.status_code >= 400:
                    print(f"  ! Gemini {model} HTTP {r.status_code}: {r.text[:150]}",
                          file=sys.stderr)
                    break
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts)
                if text.strip():
                    print(f"  written by Gemini {model}")
                    return text
            except Exception as exc:
                print(f"  ! Gemini {model}: {exc}", file=sys.stderr)
                break
    return None


def call_github_models(system: str, user: str) -> str | None:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        return None
    for model in ("openai/gpt-4.1-mini", "openai/gpt-4o-mini"):
        try:
            r = requests.post(
                "https://models.github.ai/inference/chat/completions",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                json={"model": model, "temperature": 0.4, "max_tokens": 4000,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=180)
            if r.status_code >= 400:
                print(f"  ! GitHub Models {model} HTTP {r.status_code}: {r.text[:150]}",
                      file=sys.stderr)
                continue
            text = r.json()["choices"][0]["message"]["content"]
            if text and text.strip():
                print(f"  written by GitHub Models {model}")
                return text
        except Exception as exc:
            print(f"  ! GitHub Models {model}: {exc}", file=sys.stderr)
    return None



def fallback_brief(items: list[dict], date_str: str) -> str:
    """Used when every LLM fails: plain headlines, so you still get something."""
    lines = [f"# World Brief — {date_str}", "", "_LLM unavailable today; headlines only._", ""]
    cat = None
    for it in items:
        if it["category"] != cat:
            cat = it["category"]
            lines += ["", f"## {cat.replace('_', ' ').title()}"]
        lines.append(f"- {it['title']} ({it['source']}) [{it['id']}]")
    return "\n".join(lines)


def link_citations(md: str, items: list[dict]) -> str:
    """[S12] -> [[Source]](link); one link per outlet per line, max 3 per line."""
    by_id = {it["id"]: it for it in items}
    out = []
    for line in md.split("\n"):
        used: list[str] = []

        def repl(m):
            it = by_id.get(m.group(1))
            if not it or it["source"] in used or len(used) >= 3:
                return ""
            used.append(it["source"])
            return f" [[{it['source']}]]({it['link']})"
        line = re.sub(r"\s*\[(S\d+)\]", repl, line)
        out.append(re.sub(r"  +(\[\[)", r" \1", line))
    return "\n".join(out)


# ---------------------------------------------------------------- deliver
def send_email(subject: str, md: str, audio: Path | None = None) -> None:
    user, pwd, to = (os.getenv(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "TO_EMAIL"))
    if not (user and pwd and to):
        print("  (email skipped: GMAIL_USER / GMAIL_APP_PASSWORD / TO_EMAIL not set)")
        return
    body_html = markdown.markdown(md, extensions=["extra"])
    body_html = (body_html
                 .replace("<table>", '<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px">')
                 .replace("<td", '<td style="border-bottom:1px solid #eee;padding:4px 10px"')
                 .replace("<th", '<th style="border-bottom:2px solid #ccc;padding:4px 10px"'))
    page = f"""<html><body style="font-family:Georgia,serif;max-width:680px;margin:auto;
line-height:1.55;color:#222;padding:12px">{body_html}</body></html>"""
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(md, "plain", "utf-8"))
    body.attach(MIMEText(page, "html", "utf-8"))
    msg = MIMEMultipart("mixed")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(body)
    if audio and audio.exists() and audio.stat().st_size < 20_000_000:  # Gmail cap is 25 MB
        part = MIMEAudio(audio.read_bytes(), "mpeg")
        part.add_header("Content-Disposition", "attachment",
                        filename=f"World-Brief-{datetime.now(TZ):%Y-%m-%d}.mp3")
        msg.attach(part)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pwd)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    print(f"  email sent to {to}")


def send_telegram(md: str, audio: Path | None = None) -> None:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    # Plain text is safest; markdown links become "text (url)" style.
    text = re.sub(r"\[\[([^\]]+)\]\]\(([^)]+)\)", r"(\1)", md)
    text = re.sub(r"[#*_]", "", text)
    chunks, buf = [], ""
    for para in text.split("\n"):
        if len(buf) + len(para) > 3800:
            chunks.append(buf)
            buf = ""
        buf += para + "\n"
    chunks.append(buf)
    for c in chunks:
        if c.strip():
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": c,
                                "disable_web_page_preview": True}, timeout=30)
    if audio and audio.exists():
        with audio.open("rb") as f:
            requests.post(f"https://api.telegram.org/bot{token}/sendAudio",
                          data={"chat_id": chat, "title": "World Brief podcast",
                                "performer": "Arjun & Meera"},
                          files={"audio": (audio.name, f, "audio/mpeg")}, timeout=120)
    print(f"  telegram sent ({len(chunks)} message(s){' + audio' if audio else ''})")


# ---------------------------------------------------------------- main
EDITIONS = {
    "morning": {"title": "World Brief", "prompt": "brief_prompt.md", "lookback": 26,
                "memory_days": 3, "memory_chars": 14000, "fact_check": True,
                "podcast_length": "1,800 to 2,200 words (about 12 to 15 minutes spoken)",
                "show": "The Morning Brief", "file_suffix": ""},
    "evening": {"title": "Evening Update", "prompt": "evening_prompt.md", "lookback": 12,
                "memory_days": 1, "memory_chars": 9000, "fact_check": True,
                "podcast_length": "700 to 900 words (about 5 to 6 minutes spoken)",
                "show": "The Evening Update", "file_suffix": "-evening"},
    "weekly": {"title": "Weekly Deep Dive", "prompt": "weekly_prompt.md", "lookback": 0,
               "memory_days": 7, "memory_chars": 120000, "fact_check": False,
               "podcast_length": "1,800 to 2,200 words (about 13 to 15 minutes spoken)",
               "show": "The Weekly Deep Dive", "file_suffix": "-weekly"},
}


def episode_notes(brief: str) -> str:
    """Short, clean show notes for podcast apps: the one-liner + Top 5 headlines."""
    one = re.search(r"\*\*(?:In one line|Since this morning|The week in one line):\*\*\s*(.+)", brief)
    heads = re.findall(r"^- \*\*(.+?)\*\*", brief, re.M)[:5]
    notes = (one.group(1).strip() + "\n\n" if one else "") + "\n".join(f"• {h}" for h in heads)
    return re.sub(r"\s*\[S\d+\]", "", notes)[:1500]


def insert_box(md: str, box: str) -> str:
    """Put the exact-numbers box right after the one-line summary."""
    if not box:
        return md
    i = md.find("\n## ")
    return md + "\n\n" + box if i < 0 else md[:i] + "\n\n" + box + md[i:]


def main() -> None:
    global LOOKBACK_HOURS
    edition = os.getenv("EDITION", "morning").strip() or "morning"
    cfg = EDITIONS[edition]
    dry = os.getenv("DRY_RUN") == "1"
    no_llm = os.getenv("NO_LLM") == "1"
    now = datetime.now(TZ)
    date_str = now.strftime("%A, %d %B %Y")
    print(f"Edition: {edition}")

    from markets import numbers_box
    try:
        box, markets_plain = numbers_box(weekly=(edition == "weekly"))
    except Exception as exc:
        print(f"  ! numbers box failed: {exc}", file=sys.stderr)
        box, markets_plain = "", ""
    memory = previous_briefs(cfg["memory_days"], cfg["memory_chars"],
                             keep_links=(edition == "weekly"))
    system = (ROOT / "prompts" / cfg["prompt"]).read_text().replace("{DATE}", date_str)

    items: list[dict] = []
    seen = load_seen()
    if edition == "weekly":
        if not memory:
            print("No briefs from this week yet. Exiting.")
            return
        user = f"DATE: {date_str}\n\nMARKETS: {markets_plain or '(none)'}\n\nPREVIOUS_BRIEFS:\n{memory}"
        brief = None if no_llm else call_gemini(system, user)
        if not brief:
            print("Weekly needs the AI and it was unavailable. Exiting.")
            return
    else:
        LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", cfg["lookback"]))
        feeds = yaml.safe_load((ROOT / "feeds.yaml").read_text())
        print("Fetching feeds...")
        raw = fetch_items(feeds)
        items = cap_per_category(dedupe(raw, seen))
        history = on_this_day(now) if edition == "morning" else []
        print(f"{len(raw)} raw -> {len(items)} items after dedupe/cap; "
              f"{len(history)} history events; memory {len(memory):,} chars")
        if not items:
            print("Nothing new. Exiting.")
            return
        brief = None if no_llm else write_brief(system, items, history, date_str,
                                                markets_plain, memory)
        if brief and cfg["fact_check"]:
            brief = fact_check(brief, items)
        brief = brief or fallback_brief(items, date_str)

    brief = insert_box(brief, box)
    final = link_citations(brief, items)

    # Audio podcast of the same brief (free: GitHub Models/Gemini script + edge-tts voices).
    audio_path = None
    if os.getenv("PODCAST", "1") == "1" and not no_llm:
        try:
            from podcast import make_podcast
            audio_path = make_podcast(strip_links(final), date_str, ROOT / "out", write_script,
                                      length=cfg["podcast_length"], show=cfg["show"])
        except Exception as exc:
            print(f"  ! podcast failed, email still goes out: {exc}", file=sys.stderr)

    title = f"{cfg['title']} — {date_str}"
    if audio_path:
        # Metadata for the podcast-app feed publisher (podfeed.py).
        plain = re.sub(r"[#*_|>]", "", strip_links(final))
        (ROOT / "out" / "episode.json").write_text(json.dumps({
            "edition": edition, "title": title, "date": now.isoformat(),
            "file": str(audio_path), "description": episode_notes(brief)}, ensure_ascii=False))

    if dry:
        print("\n" + final)
        if audio_path:
            print(f"\n[podcast saved at {audio_path}]")
        return

    send_email(title + (" 🎧" if audio_path else ""), final, audio_path)
    send_telegram(final, audio_path)

    ARCHIVE_DIR.mkdir(exist_ok=True)
    (ARCHIVE_DIR / f"{now:%Y-%m-%d}{cfg['file_suffix']}.md").write_text(final)
    if items:
        stamp = datetime.now(timezone.utc).isoformat()
        for it in items:
            seen[key_of(it)] = stamp
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(seen, indent=0))


if __name__ == "__main__":
    main()
