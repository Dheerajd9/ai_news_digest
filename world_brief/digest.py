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
import subprocess
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
STORIES_FILE = ROOT / "state" / "stories.json"   # story ledger: memory across days
ARCHIVE_DIR = ROOT / "briefs"
PODCAST_ARCHIVE = ARCHIVE_DIR / "podcasts"   # subfolder: not part of the brief memory
TZ = ZoneInfo(os.getenv("BRIEF_TZ", "America/Chicago"))

LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "26"))
MAX_PER_CATEGORY = int(os.getenv("MAX_PER_CATEGORY", "25"))
# India gets more room than the default; everything else uses MAX_PER_CATEGORY.
CATEGORY_CAPS = {"india": 40, "india_economy": 30, "telangana_hyderabad": 12,
                 "usa": 30, "india_us_visa_trade": 25, "world": 30, "official": 15,
                 "jobs_tax_housing": 20}
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
    "rbi.org.in": "RBI (official)", "sebi.gov.in": "SEBI (official)", "pib.gov.in": "PIB (official)",
    "federalreserve.gov": "Federal Reserve (official)", "bls.gov": "BLS (official)", "cnbc.com": "CNBC",
}


OFFICIAL_SOURCES = re.compile(
    r"\(official\)|\.gov\b|gov\.in|nic\.in|press information bureau|\bPIB\b|reserve bank of india|"
    r"\bRBI\b|\bSEBI\b|ministry of|\bMEA\b|white ?house|federal reserve|treasury|department of|"
    r"\bUSCIS\b|\bDHS\b|\bBLS\b|bureau of labor|\bSEC\b|\bUSTR\b|\bIRCC\b|supreme court of|"
    r"\bNSE\b|\bBSE\b|govt\.|government of", re.I)
MAJOR_SOURCES = {
    "reuters", "associated press", "ap news", "ap", "bloomberg", "financial times", "bbc", "bbc news",
    "cnbc", "the hindu", "indian express", "the indian express", "hindustan times", "times of india",
    "the times of india", "mint", "livemint", "economic times", "the economic times", "business standard",
    "ndtv", "ndtv profit", "moneycontrol", "moneycontrol.com", "pti", "ani", "ani news", "press trust of india",
    "npr", "the guardian", "al jazeera", "dw", "deutsche welle", "nhk world", "france 24", "cnn",
    "the new york times", "new york times", "the washington post", "washington post",
    "the wall street journal", "wsj", "politico", "axios", "the economist", "nature", "ars technica",
    "mit tech review", "live law", "bar and bench", "the tribune", "deccan herald", "the new indian express",
    "telangana today", "the print", "theprint", "scroll", "fortune", "forbes", "usa today", "abc news",
    "cbs news", "nbc news", "fox news", "the verge", "techcrunch", "wired", "kyiv independent",
    "nikkei asia", "south china morning post", "the japan times", "cbc", "global news", "businessline",
    "the hindu businessline", "india today", "news on air", "all india radio", "doordarshan",
}


def source_tier(source: str) -> str:
    """official / major / other: lets the AI apply the extraordinary-claim rule."""
    if OFFICIAL_SOURCES.search(source):
        return "official"
    return "major" if source.lower().strip() in MAJOR_SOURCES else "other"


def redact(text: str) -> str:
    """Remove anything that looks like a secret (bot tokens, api keys) before logging."""
    text = re.sub(r"bot\d+:[\w-]+", "bot***", text)
    text = re.sub(r"(apikey|key|token)=[^&\s]+", r"\1=***", text, flags=re.I)
    return re.sub(r"github_pat_\w+|gh[pousr]_\w+|AIza[\w-]{20,}", "***", text)


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
                print(f"  ! feed failed: {url} ({redact(str(exc))})", file=sys.stderr)
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
    tier_bonus = {"official": 3, "major": 1, "other": 0}
    for cat, cat_items in by_cat.items():
        limit = CATEGORY_CAPS.get(cat, MAX_PER_CATEGORY)
        # Simple Python scoring first (cheap): trusted and widely covered stories go first.
        cat_items.sort(key=lambda i: (tier_bonus[source_tier(i["source"])] + 2 * i.get("coverage", 1)),
                       reverse=True)
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
        print(f"  ! on-this-day failed ({redact(str(exc))})", file=sys.stderr)
        return []


# ---------------------------------------------------------------- story ledger
def load_stories(today: str) -> dict[str, dict]:
    """Tracked stories. Resolved/archived ones fade out after 3 days, anything unseen for 14."""
    try:
        stories = json.loads(STORIES_FILE.read_text())
    except Exception:
        return {}
    keep = {}
    for sid, st in stories.items():
        age = (datetime.fromisoformat(today) - datetime.fromisoformat(st.get("last_seen", today))).days
        if age > 14 or (st.get("status") in ("RESOLVED", "ARCHIVED") and age > 3):
            continue
        keep[sid] = st
    return keep


def story_memory(stories: dict[str, dict], limit: int = 40) -> str:
    active = sorted(stories.items(), key=lambda kv: kv[1].get("last_seen", ""), reverse=True)[:limit]
    return json.dumps([{"id": sid, **{k: st.get(k) for k in
                       ("title", "status", "stage", "summary", "open_questions",
                        "first_seen", "last_seen")},
                        "timeline": st.get("timeline", [])[-5:],
                        "expectations": st.get("expectations", [])[-3:]}
                       for sid, st in active], ensure_ascii=False)


def extract_stories(md: str) -> tuple[str, list[dict]]:
    m = re.search(r"<!--\s*STORIES(.*?)-->", md, re.S)
    if not m:
        return md, []
    md = md[:m.start()].rstrip() + "\n" + md[m.end():].lstrip()
    raw = m.group(1).strip()
    j = re.search(r"\[.*\]", raw, re.S)
    try:
        data = json.loads(j.group(0)) if j else []
        return md, [d for d in data if isinstance(d, dict)]
    except json.JSONDecodeError:
        print("  ! story ledger block was not valid JSON; skipped")
        return md, []


def compact_timeline(timeline: list[dict], keep: int = 10) -> list[dict]:
    """Keep timelines small: merge the oldest milestones into one summary line."""
    if len(timeline) <= keep:
        return timeline
    old, recent = timeline[:len(timeline) - keep + 1], timeline[len(timeline) - keep + 1:]
    merged = {"date": f"{old[0]['date'].split('..')[0]}..{old[-1]['date'].split('..')[-1]}",
              "event": ("; ".join(e["event"] for e in old))[:220]}
    return [merged] + recent


def update_stories(stories: dict[str, dict], new: list[dict], today: str) -> dict[str, dict]:
    statuses = {"NEW", "DEVELOPING", "MAJOR CHANGE", "RESOLVED", "ARCHIVED"}
    for d in new[:60]:
        sid = re.sub(r"[^a-z0-9-]", "", str(d.get("id", "")).lower().replace(" ", "-")
                     .replace("_", "-"))[:60]
        if len(sid) < 3:
            continue
        st = stories.setdefault(sid, {"first_seen": today, "expectations": [], "timeline": []})
        if "known" in st and "summary" not in st:          # older ledger format
            st["summary"] = st.pop("known")
        st["title"] = str(d.get("title") or st.get("title", ""))[:150]
        status = str(d.get("status", "")).upper()
        st["status"] = status if status in statuses else st.get("status", "DEVELOPING")
        if d.get("stage"):
            st["stage"] = str(d["stage"]).upper()[:40]
        summary = d.get("summary") or d.get("known")
        if summary:
            st["summary"] = str(summary)[:300]
        answered = {str(q).strip().lower() for q in (d.get("answered") or [])}
        questions = d.get("open_questions")
        if isinstance(questions, list):
            st["open_questions"] = [str(q)[:150] for q in questions
                                    if str(q).strip() and str(q).strip().lower() not in answered][:5]
        elif answered:
            st["open_questions"] = [q for q in st.get("open_questions", [])
                                    if q.strip().lower() not in answered]
        milestone = str(d.get("milestone") or "").strip()
        tl = st.setdefault("timeline", [])
        if milestone and not (tl and tl[-1]["event"].lower() == milestone.lower()):
            tl.append({"date": today, "event": milestone[:120]})
            st["timeline"] = compact_timeline(tl)
        st["last_seen"] = today
        exp = str(d.get("expectation") or "").strip()
        if exp and all(e["text"] != exp for e in st.get("expectations", [])):
            st.setdefault("expectations", []).append({"date": today, "text": exp[:250]})
            st["expectations"] = st["expectations"][-5:]
    return stories


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
               markets: str, memory: str, compact: bool = False, ledger: str = "") -> str:
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
            payload.append({**{k: it[k] for k in ("id", "category", "source", "coverage", "title")},
                            "tier": source_tier(it["source"])})
        history, memory, ledger = history[:5], memory[-1500:], ledger[:1500]
    else:
        payload = [{**{k: it[k] for k in ("id", "category", "source", "coverage", "title",
                                           "summary", "published")},
                    "tier": source_tier(it["source"]),
                    "also": [f"{a} ({source_tier(a)})" for a in it.get("also", [])][:6]}
                   for it in items]
    return (f"DATE: {date_str}\n\nMARKETS: {markets or '(none)'}\n\n"
            f"NEWS_ITEMS:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            f"ON_THIS_DAY:\n{json.dumps(history, ensure_ascii=False)}\n\n"
            f"PREVIOUS_BRIEFS:\n{memory or '(none)'}\n\n"
            f"STORY_MEMORY:\n{ledger or '[]'}")


def write_brief(system: str, items, history, date_str, markets, memory,
                ledger: str = "") -> str | None:
    full = build_user(items, history, date_str, markets, memory, ledger=ledger)
    print(f"  brief prompt ~{est_tokens(system, full):,} tokens")
    return (call_gemini(system, full)
            or call_github_models(system, build_user(items, history, date_str, markets,
                                                     memory, compact=True, ledger=ledger)))


def fact_check(draft: str, items: list[dict]) -> str:
    """Second pass: drop or soften anything the cited sources don't support."""
    cited = set(re.findall(r"\[(S\d+)\]", draft))
    if not cited:
        return draft
    src = [{"id": i["id"], "source": i["source"], "tier": source_tier(i["source"]),
            "published": i["published"][:10], "title": i["title"], "summary": i["summary"][:200]}
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
        print(f"  ! couldn't list Gemini models: {redact(str(exc))}", file=sys.stderr)

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
                if r.status_code == 429:          # free-tier limit
                    if "PerDay" in r.text or "per day" in r.text.lower():
                        print(f"  ! Gemini {model}: daily free quota used up", file=sys.stderr)
                        break
                    time.sleep(20)                # per-minute limit: wait, retry once
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
                print(f"  ! Gemini {model}: {redact(str(exc))}", file=sys.stderr)
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
            print(f"  ! GitHub Models {model}: {redact(str(exc))}", file=sys.stderr)
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
            if (not it or it["source"] in used or len(used) >= 3
                    or not re.match(r"https?://", it["link"])):
                return ""
            used.append(it["source"])
            return f" [[{it['source']}]]({it['link']})"
        line = re.sub(r"\s*\[(S\d+)\]", repl, line)
        out.append(re.sub(r"  +(\[\[)", r" \1", line))
    return "\n".join(out)


# ---------------------------------------------------------------- deliver
def send_email(subject: str, md: str, audio=None, to: str | None = None,
               audio_name: str | None = None) -> None:
    user, pwd = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    to = to or os.getenv("TO_EMAIL")
    if not (user and pwd and to):
        print("  (email skipped: GMAIL_USER / GMAIL_APP_PASSWORD / recipient not set)")
        return
    # Escape raw HTML coming from feeds/AI so only Markdown formatting reaches the email.
    safe_md = re.sub(r"\]\((?!https?://)[^)]*\)", "]", md)      # only http(s) links survive
    body_html = markdown.markdown(safe_md.replace("&", "&amp;").replace("<", "&lt;"),
                                  extensions=["extra"])
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
    files = [audio] if isinstance(audio, Path) else list(audio or [])
    total = 0
    for f in files:
        if not (f and f.exists()):
            continue
        size = f.stat().st_size
        if total + size > 20_000_000:                    # Gmail caps a message at 25 MB
            print(f"  (skipped attaching {f.name}: email would be too large; use the link)")
            continue
        total += size
        part = MIMEAudio(f.read_bytes(), "mpeg")
        name = audio_name if (audio_name and len(files) == 1) else \
            f"World-Brief-{datetime.now(TZ):%Y-%m-%d}-{f.stem.split('_')[-1]}.mp3"
        part.add_header("Content-Disposition", "attachment", filename=name)
        msg.attach(part)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pwd)
        recipients = [a.strip() for a in to.split(",") if a.strip()]
        s.sendmail(user, recipients, msg.as_string())
    print(f"  email sent ({len(recipients)} recipient(s))")


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
    try:
        for c in chunks:
            if c.strip():
                requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat, "text": c,
                                    "disable_web_page_preview": True}, timeout=30)
        if audio and audio.exists():
            with audio.open("rb") as f:
                requests.post(f"https://api.telegram.org/bot{token}/sendAudio",
                              data={"chat_id": chat, "title": "World Brief podcast",
                                    "performer": "Kartik & Priya"},
                              files={"audio": (audio.name, f, "audio/mpeg")}, timeout=120)
    except Exception as exc:   # the URL holds the bot token: print only the error type
        print(f"  ! telegram failed: {type(exc).__name__}")
        return
    print(f"  telegram sent ({len(chunks)} message(s){' + audio' if audio else ''})")


# ---------------------------------------------------------------- main
QUICK = ("podcast_quick_prompt.md", "quick")
FULL = ("podcast_prompt.md", "full")
EDITIONS = {
    "morning": {"title": "World Brief", "prompt": "brief_prompt.md", "lookback": 26,
                "memory_days": 3, "memory_chars": 14000, "fact_check": True, "file_suffix": "",
                "top5_intro": ("Frame this as 🌅 what happened while I was sleeping: India, overnight "
                               "world events, the previous US market close, Asian markets."),
                "podcasts": [
                    (*QUICK, "⚡ Quick", "The Morning Brief: Quick",
                     "At most about 1,000 words (5 to 7 minutes MAXIMUM). A 4-minute episode is completely fine."),
                    (*FULL, "📻 Full", "The Morning Brief: Full",
                     "About 1,500 to 2,300 words (10 to 15 minutes): shorter on a quiet day, "
                     "longer on a big news day."),
                ]},
    "evening": {"title": "Evening Update", "prompt": "evening_prompt.md", "lookback": 12,
                "memory_days": 1, "memory_chars": 9000, "fact_check": True,
                "file_suffix": "-evening", "top5_intro": "",
                "podcasts": [
                    (*QUICK, "⚡ Quick", "The Evening Update",
                     "At most about 600 words (3 to 4 minutes MAXIMUM). Shorter if little changed."),
                ]},
    "weekly": {"title": "Weekly Deep Dive", "prompt": "weekly_prompt.md", "lookback": 0,
               "memory_days": 7, "memory_chars": 120000, "fact_check": False,
               "file_suffix": "-weekly", "top5_intro": "",
               "podcasts": [
                   (*FULL, "📻 Weekly", "The Weekly Deep Dive",
                    "At most about 2,300 words (12 to 15 minutes MAXIMUM). Shorter is fine."),
               ]},
}
GENERIC_TOPICS = {"update", "updates", "latest", "news", "breaking", "story", "new", "previously"}


def extract_meta(md: str) -> tuple[str, dict]:
    """Pull the AI's <!--META ...--> block out of the brief (it drives the automation)."""
    meta = {}
    m = re.search(r"<!--\s*META(.*?)-->", md, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().upper()] = v.strip()
        md = md[:m.start()].rstrip() + "\n" + md[m.end():].lstrip()
    return md.strip() + "\n", meta


def episode_notes(brief: str, meta: dict) -> str:
    """Short, clean show notes for podcast apps: the one-liner + topics/top headlines."""
    one = re.search(r"\*\*(?:In one line|Since this morning|The week in one line):\*\*\s*(.+)", brief)
    topics = [t.strip(" •-") for t in meta.get("PODCAST_TOPICS", "").split(";")
              if t.strip(" •-") and t.strip(" •-").lower() not in GENERIC_TOPICS]
    if len(topics) < 2:
        sec = re.search(r"^## (?:1\.|🚨|🌆).*?(?=^## )", brief, re.S | re.M)
        topics = []
        for line in re.findall(r"^- .+$", sec.group(0) if sec else brief, re.M):
            bolds = [b for b in re.findall(r"\*\*(.+?)\*\*", line)
                     if b.strip(" :").lower() not in GENERIC_TOPICS and len(b) > 5]
            if bolds:
                topics.append(bolds[0].strip(" :"))
        topics = topics[:5]
    notes = (one.group(1).strip() + "\n\n" if one else "") + "\n".join(f"• {t}" for t in topics)
    return re.sub(r"\s*\[S\d+\]", "", notes)[:1500]


def insert_box(md: str, box: str) -> str:
    """Put the market snapshot where the AI left {{MARKET_SNAPSHOT}}, else after the summary."""
    if "{{MARKET_SNAPSHOT}}" in md:
        return md.replace("{{MARKET_SNAPSHOT}}", box)
    if not box:
        return md
    i = md.find("\n## ")
    return md + "\n\n" + box if i < 0 else md[:i] + "\n\n" + box + md[i:]


def audio_minutes(audio: Path) -> int | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(audio)], capture_output=True, text=True)
        return max(1, round(float(out.stdout.strip()) / 60))
    except Exception:
        return None


def listen_section(episodes: list[dict], meta: dict, brief: str) -> str:
    """🎧 block near the top of the email: one line per podcast + real topic titles."""
    if not episodes:
        return ""
    lines = []
    for ep in episodes:
        mins = audio_minutes(ep["file"])
        when = f" (about {mins} min)" if mins else ""
        play = f"[Play or download]({ep['url']})" if ep.get("url") else "attached to this email"
        lines.append(f"**{ep['label']}{when}:** {play}")
    topics = [t.lstrip("• ").strip() for t in episode_notes(brief, meta).split("\n\n")[-1].split("\n")]
    topics = [t for t in topics if t]
    out = "## 🎧 Listen to Kartik & Priya\n\n" + "  \n".join(lines) + "\n"
    if topics:
        out += "\n**Today's topics:**\n" + "\n".join(f"- {t}" for t in topics[:7]) + "\n"
    return out


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
        print(f"  ! numbers box failed: {redact(str(exc))}", file=sys.stderr)
        box, markets_plain = "", ""
    memory = previous_briefs(cfg["memory_days"], cfg["memory_chars"],
                             keep_links=(edition == "weekly"))
    system = ((ROOT / "prompts" / cfg["prompt"]).read_text()
              .replace("{DATE}", date_str).replace("{EDITION}", edition.upper())
              .replace("{TITLE}", cfg["title"]).replace("{TOP5_INTRO}", cfg["top5_intro"])
              + "\n\n" + (ROOT / "prompts" / "intelligence_layer.md").read_text()
              + "\n\n" + (ROOT / "prompts" / "advanced_tracking.md").read_text())
    today_iso = now.strftime("%Y-%m-%d")
    stories = load_stories(today_iso)
    ledger = story_memory(stories)

    items: list[dict] = []
    meta: dict = {}
    seen = load_seen()
    if edition == "weekly":
        if not memory:
            print("No briefs from this week yet. Exiting.")
            return
        user = (f"DATE: {date_str}\n\nMARKETS: {markets_plain or '(none)'}\n\n"
                f"STORY_MEMORY:\n{ledger}\n\nPREVIOUS_BRIEFS:\n{memory}")
        brief = None if no_llm else call_gemini(system, user)
        if not brief:
            print("Weekly needs the AI and it was unavailable. Exiting.")
            return
        brief, meta = extract_meta(brief)
        brief, new_stories = extract_stories(brief)
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
                                                markets_plain, memory, ledger)
        new_stories: list[dict] = []
        if brief:
            brief, meta = extract_meta(brief)
            brief, new_stories = extract_stories(brief)
            if cfg["fact_check"]:
                brief = fact_check(brief, items)
        brief = brief or fallback_brief(items, date_str)
    print(f"  meta: {meta}")

    brief = insert_box(brief, box)
    final = link_citations(brief, items)

    title = f"{cfg['title']} — {date_str}"
    warnings = [w.strip() for w in meta.get("VERIFICATION_WARNINGS", "").split(";")
                if w.strip() and w.strip().upper() not in ("NONE", "N/A", "-")]
    if warnings:
        final += ("\n\n## ⚠️ Verification notes\n"
                  + "\n".join(f"- {w}" for w in warnings[:5])
                  + "\n\n_These claims could not be fully verified from strong sources yet._\n")

    # Podcasts (Kartik & Priya): Quick and/or Full, unless the AI says nothing is worth it.
    episodes: list[dict] = []
    wants_podcast = meta.get("PODCAST_REQUIRED", "TRUE").upper().startswith("T")
    if os.getenv("PODCAST", "1") == "1" and not no_llm and wants_podcast:
        from podcast import make_podcast, latest_previous_script
        import podfeed
        source_text = strip_links(final.replace("{{LISTEN}}", ""))
        for prompt_file, kind, label, show, length in cfg["podcasts"]:
            try:
                mp3 = make_podcast(source_text, date_str, ROOT / "out", write_script,
                                   length=length, show=show, prompt_file=prompt_file,
                                   out_name=f"world_brief_{kind}.mp3",
                                   report_type=edition.upper(),
                                   previous_script=latest_previous_script(PODCAST_ARCHIVE, kind))
            except Exception as exc:
                print(f"  ! {kind} podcast failed: {redact(str(exc))[:200]}", file=sys.stderr)
                continue
            if not mp3:
                continue
            tag = edition if kind == "full" else f"{edition}-{kind}"
            ep = {"edition": tag, "title": f"{title} ({label.split(' ', 1)[-1]})",
                  "date": now.isoformat(), "file": str(mp3), "label": label,
                  "description": episode_notes(brief, meta), "url": None}
            # Publish BEFORE emailing so the links in the email already work.
            if podfeed.configured() and not dry:
                try:
                    ep["url"] = podfeed.publish_episode(ep)
                except Exception as exc:
                    print(f"  ! podcast feed failed: {type(exc).__name__}: {redact(str(exc))[:150]}")
            ep["kind"] = kind
            episodes.append(ep)
        if episodes:
            (ROOT / "out" / "episodes.json").write_text(
                json.dumps(episodes, ensure_ascii=False, default=str))
    elif not wants_podcast:
        print("  podcast skipped: AI says nothing meaningful new")

    listen = listen_section(episodes, meta, brief)
    if "{{LISTEN}}" in final:
        final = final.replace("{{LISTEN}}", listen)
    elif listen:
        final = insert_box(final, listen)
    final = re.sub(r"\n{3,}", "\n\n", final)
    audio_files = [Path(e["file"]) for e in episodes]

    urgent = meta.get("URGENT_ALERT", "").upper().startswith("T")
    subject = ("🚨 " if urgent else "") + title + (" 🎧" if episodes else "")
    telugu_on = (edition in os.getenv("TELUGU_EDITIONS", "morning").split(",")
                 and not no_llm and items)
    if dry:
        print(f"\nSUBJECT: {subject}\n\n" + final)
        for f in audio_files:
            print(f"\n[podcast saved at {f}]")
        if telugu_on and os.getenv("TELUGU_DRY_RUN") == "1":
            import telugu
            telugu.run(final, markets_plain, date_str, now, ROOT / "out", dry=True)
        return

    send_email(subject, final, audio_files)
    send_telegram(final, audio_files[0] if audio_files else None)

    # Telugu edition for parents (only if PARENTS_EMAIL / PARENTS_WHATSAPP is set).
    if telugu_on:
        import telugu
        if telugu.parents_configured():
            telugu.run(final, markets_plain, date_str, now, ROOT / "out", dry=False)

    ARCHIVE_DIR.mkdir(exist_ok=True)
    (ARCHIVE_DIR / f"{now:%Y-%m-%d}{cfg['file_suffix']}.md").write_text(final)
    # Keep podcast scripts as memory for tomorrow's episode ("Yesterday we talked about...").
    PODCAST_ARCHIVE.mkdir(parents=True, exist_ok=True)
    for ep in episodes:
        script = ROOT / "out" / f"script_world_brief_{ep['kind']}.txt"
        if script.exists():
            (PODCAST_ARCHIVE / f"{now:%Y-%m-%d}-{edition}-{ep['kind']}.txt").write_text(
                script.read_text())
    if new_stories:
        STORIES_FILE.parent.mkdir(exist_ok=True)
        STORIES_FILE.write_text(json.dumps(update_stories(stories, new_stories, today_iso),
                                           indent=1, ensure_ascii=False))
        print(f"  story ledger: {len(new_stories)} stories updated")
    if items:
        stamp = datetime.now(timezone.utc).isoformat()
        for it in items:
            seen[key_of(it)] = stamp
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(seen, indent=0))


if __name__ == "__main__":
    main()
