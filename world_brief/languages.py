"""
Telugu and Hindi podcasts of the same full, fact-checked brief, so ONE email can offer
the news in English, Telugu and Hindi (everyone picks their language and taps play).

  PODCAST_LANGUAGES   which extra languages, comma-separated (default "te,hi")
  LANG_EDITIONS       editions that get them (default "morning,evening,weekly")
  PARENTS_WHATSAPP    optional "phone:apikey" pairs: WhatsApp message with the play links
                      via CallMeBot (free; each phone activates it once, see README)
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

import digest
import podfeed

ROOT = Path(__file__).parent

LANGS = {
    "te": {"name": "telugu", "label": "🟠 తెలుగు (Telugu)", "show": "ఈ రోజు వార్తలు",
           "hosts": "Ravi & Lakshmi", "archive": ROOT / "briefs" / "telugu"},
    "hi": {"name": "hindi", "label": "🟢 हिंदी (Hindi)", "show": "आज की खबरें",
           "hosts": "Arjun & Meera", "archive": ROOT / "briefs" / "hindi"},
}

LENGTHS = {
    "MORNING": ("About 1,900 to 2,600 words (15 to 20 minutes spoken): the same full coverage as "
                "the English Full podcast. Cover every story in the brief that matters, with real "
                "explanation; shorter only on a genuinely quiet news day. Do not stop early."),
    "EVENING": ("About 500 to 900 words (4 to 7 minutes spoken): only what changed since the "
                "morning, biggest change first. Shorter if little changed."),
    "WEEKLY": ("About 1,900 to 2,600 words (15 to 20 minutes spoken): the week's big patterns, "
               "same coverage as the English weekly podcast. Do not stop early."),
}


def _redact(text: str) -> str:
    return digest.redact(text)


def wanted(edition: str) -> list[str]:
    if edition not in os.getenv("LANG_EDITIONS", "morning,evening,weekly").split(","):
        return []
    return [c.strip() for c in os.getenv("PODCAST_LANGUAGES", "te,hi").split(",")
            if c.strip() in LANGS]


def make_episodes(final_md: str, date_str: str, now: datetime, out_dir: Path,
                  report_type: str, langs: list[str], dry: bool) -> list[dict]:
    """Build (and upload) one podcast per language. Never raises."""
    from podcast import make_podcast, latest_previous_script
    news = digest.strip_links(final_md.replace("{{LISTEN}}", ""))
    news = re.sub(r"(?ms)^##\s*🎧.*?(?=^## |\Z)", "", news)      # the Listen block is not news
    episodes = []
    for code in langs:
        info = LANGS[code]
        sfx = "" if report_type == "MORNING" else f"-{report_type.lower()}"
        mp3 = None
        for attempt in (1, 2):           # AI busy? wait a bit and try once more
            try:
                mp3 = make_podcast(news, date_str, out_dir,
                                   lambda s, u: digest.ask(s, u, prefer="gemini"),
                                   length=LENGTHS.get(report_type, LENGTHS["MORNING"]),
                                   show=info["show"], lang=code,
                                   report_type=report_type,
                                   out_name=f"{info['name']}_podcast.mp3",
                                   previous_script=latest_previous_script(info["archive"], "podcast", 3000))
            except Exception as exc:
                print(f"  ! {info['name']} podcast failed: {type(exc).__name__}: "
                      f"{_redact(str(exc))[:200]}", file=sys.stderr)
            if mp3:
                break
            if attempt == 1:
                print(f"  {info['name']} script failed; retrying in 90s")
                time.sleep(90)
        if not mp3:
            continue
        url = None
        if podfeed.configured() and not dry:
            for attempt in (1, 2):
                try:
                    url = podfeed.upload_asset(mp3, f"{now:%Y-%m-%d}-{info['name']}{sfx}.mp3")
                    print(f"  {info['name']} audio uploaded ({mp3.stat().st_size / 1e6:.1f} MB)")
                    break
                except Exception as exc:
                    print(f"  ! {info['name']} upload attempt {attempt} failed: "
                          f"{type(exc).__name__}: {_redact(str(exc))[:200]}")
            if url:
                try:                         # its own podcast-app feed (feed-te.xml / feed-hi.xml)
                    podfeed.publish_language_episode(
                        code, mp3, url.rsplit("/", 1)[-1],
                        f"{info['show']} · {date_str}", f"{info['hosts']} · {report_type.title()}")
                except Exception as exc:
                    print(f"  ! {info['name']} feed not updated: {type(exc).__name__}")
                try:
                    podfeed.prune_extra(f"-{info['name']}{sfx}.mp3", keep=21)
                except Exception as exc:
                    print(f"  ! {info['name']} prune failed: {type(exc).__name__}")
        episodes.append({"kind": code, "label": info["label"], "file": str(mp3), "url": url,
                         "hosts": info["hosts"]})
        if not dry:                                  # previous-podcast memory for tomorrow
            info["archive"].mkdir(parents=True, exist_ok=True)
            script = out_dir / f"script_{info['name']}_podcast.txt"
            if script.exists():
                (info["archive"] / f"{now:%Y-%m-%d}{sfx}-podcast.txt").write_text(script.read_text())
    return episodes


def headlines(md: str, n: int = 3) -> list[str]:
    sec = re.search(r"^##[^\n]*Top[^\n]*\n(.*?)(?=^## )", md, re.S | re.M)
    lines = re.findall(r"^\s*(?:[-*]|\d+\.)\s+(.+)$", sec.group(1) if sec else md, re.M)[:n]
    return [re.sub(r"\[\[[^\]]+\]\]\([^)]+\)|[*_#`]", "", l).strip()[:140] for l in lines]


def send_whatsapp(final_md: str, episodes: list[dict]) -> None:
    pairs = [p.strip() for p in os.getenv("PARENTS_WHATSAPP", "").split(",") if p.strip()]
    if not pairs:
        return
    links = [f"{e['label']}: {digest.play_link(e['url'])}" for e in episodes if e.get("url")]
    text = ("🎧 Today's news\n" + "\n".join(links) + "\n\n" if links else "Today's top news:\n\n") \
        + "\n".join(f"• {h}" for h in headlines(final_md))
    for n, pair in enumerate(pairs, 1):
        m = re.fullmatch(r"\+?(\d{8,15}):(\w{4,40})", pair)
        if not m:
            print(f"  ! WhatsApp entry {n} is not in phone:apikey format; skipped")
            continue
        try:
            r = requests.get("https://api.callmebot.com/whatsapp.php",
                             params={"phone": "+" + m.group(1), "text": text[:1500],
                                     "apikey": m.group(2)}, timeout=30)
            ok = r.status_code == 200 and "error" not in r.text.lower()
            print(f"  WhatsApp {n}: {'sent' if ok else f'failed (HTTP {r.status_code})'}")
        except Exception as exc:          # never print the URL: it contains the apikey
            print(f"  ! WhatsApp {n} failed: {type(exc).__name__}")
