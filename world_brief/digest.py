"""
World Brief: daily global news digest.

Pipeline:
  1. Fetch RSS feeds (feeds.yaml), keep items from the last LOOKBACK_HOURS.
  2. Drop items already sent (state/seen.json) and near-duplicate headlines.
  3. Cap items per category so the LLM prompt stays small (free-tier friendly).
  4. Add "on this day" history from Wikipedia.
  5. Ask an LLM (Gemini free tier, GitHub Models free backup) to write the brief using prompts/brief_prompt.md.
     The model must cite item ids like [S12]; we turn those into real links afterwards,
     which keeps it grounded and lets you verify every claim.
  6. Deliver by email (Gmail SMTP) and/or Telegram. Archive markdown in briefs/.

Env vars (GitHub Secrets):
  GEMINI_API_KEY        free Google AI Studio key (GEMINI_MODEL optional, auto-picked)
  GITHUB_TOKEN          built-in in Actions; GitHub Models = free backup brain
  GMAIL_USER, GMAIL_APP_PASSWORD, TO_EMAIL     (for email)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID         (optional)
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
            source = clean(parsed.feed.get("title", "")) or url.split("/")[2]
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
# All providers here are free: Gemini free tier (your key) and GitHub Models
# (built-in GITHUB_TOKEN, no extra key). If both fail you still get headlines.
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"


def build_user(items: list[dict], history: list[dict], date_str: str,
               compact: bool = False) -> str:
    if compact:
        # GitHub Models' free tier caps input at about 8k tokens per request,
        # so send titles only, India first, most-covered stories first.
        order = list(CATEGORY_CAPS) + sorted({i["category"] for i in items} - set(CATEGORY_CAPS))
        ranked = sorted(items, key=lambda i: (order.index(i["category"]), -i["coverage"]))
        per_cat: dict[str, int] = {}
        slim = []
        for it in ranked:
            if per_cat.get(it["category"], 0) >= 8:
                continue
            per_cat[it["category"]] = per_cat.get(it["category"], 0) + 1
            slim.append({k: it[k] for k in ("id", "category", "source", "coverage", "title")})
        payload, hist = slim, history[:6]
    else:
        payload = [{k: it[k] for k in ("id", "category", "source", "coverage", "title",
                                        "summary", "published")} for it in items]
        hist = history
    return (f"DATE: {date_str}\n\nNEWS_ITEMS:\n{json.dumps(payload, ensure_ascii=False)}"
            f"\n\nON_THIS_DAY:\n{json.dumps(hist, ensure_ascii=False)}")


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


def write_brief(system: str, items: list[dict], history: list[dict],
                date_str: str) -> str | None:
    full = build_user(items, history, date_str)
    print(f"  brief prompt ~{(len(system) + len(full)) // 4:,} tokens")
    return (call_gemini(system, full)
            or call_github_models(system, build_user(items, history, date_str, compact=True)))


def write_script(system: str, user: str) -> str | None:
    """Generic free LLM call (used by the podcast script writer)."""
    return call_gemini(system, user) or call_github_models(system, user)


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
    by_id = {it["id"]: it for it in items}

    def repl(m):
        it = by_id.get(m.group(1))
        return f" [[{it['source']}]]({it['link']})" if it else ""
    return re.sub(r"\[(S\d+)\]", repl, md)


# ---------------------------------------------------------------- deliver
def send_email(subject: str, md: str, audio: Path | None = None) -> None:
    user, pwd, to = (os.getenv(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "TO_EMAIL"))
    if not (user and pwd and to):
        print("  (email skipped: GMAIL_USER / GMAIL_APP_PASSWORD / TO_EMAIL not set)")
        return
    body_html = markdown.markdown(md, extensions=["extra"])
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
def main() -> None:
    dry = os.getenv("DRY_RUN") == "1"
    now = datetime.now(TZ)
    date_str = now.strftime("%A, %d %B %Y")

    feeds = yaml.safe_load((ROOT / "feeds.yaml").read_text())
    print("Fetching feeds...")
    raw = fetch_items(feeds)
    seen = load_seen()
    items = cap_per_category(dedupe(raw, seen))
    history = on_this_day(now)
    print(f"{len(raw)} raw -> {len(items)} items after dedupe/cap; {len(history)} history events")
    if not items:
        print("Nothing new. Exiting.")
        return

    system = (ROOT / "prompts" / "brief_prompt.md").read_text().replace("{DATE}", date_str)
    if os.getenv("NO_LLM") == "1":
        brief = fallback_brief(items, date_str)
    else:
        brief = write_brief(system, items, history, date_str) or fallback_brief(items, date_str)
    final = link_citations(brief, items)

    # Audio podcast version of the same brief (free: Gemini/GitHub Models script + edge-tts voices).
    audio_path = None
    if os.getenv("PODCAST", "1") == "1" and os.getenv("NO_LLM") != "1":
        try:
            from podcast import make_podcast
            audio_path = make_podcast(brief, date_str, ROOT / "out", write_script)
        except Exception as exc:
            print(f"  ! podcast failed, email still goes out: {exc}", file=sys.stderr)

    if dry:
        print("\n" + final)
        if audio_path:
            print(f"\n[podcast saved at {audio_path}]")
        return

    subject = f"World Brief — {date_str}" + (" 🎧" if audio_path else "")
    send_email(subject, final, audio_path)
    send_telegram(final, audio_path)

    ARCHIVE_DIR.mkdir(exist_ok=True)
    (ARCHIVE_DIR / f"{now:%Y-%m-%d}.md").write_text(final)
    stamp = datetime.now(timezone.utc).isoformat()
    for it in items:
        seen[key_of(it)] = stamp
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen, indent=0))


if __name__ == "__main__":
    main()
