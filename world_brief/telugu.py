"""
Telugu edition for parents: the same fact-checked morning news, retold in simple
spoken Telugu, as a short letter + a Ravi & Lakshmi conversation podcast.

Delivery (all optional, all free):
  PARENTS_EMAIL      one or more addresses, comma-separated -> Telugu email + MP3 attached
  PARENTS_WHATSAPP   "phone:apikey" pairs, comma-separated -> WhatsApp text with the play
                     link, via CallMeBot (free; each phone activates it once, see README).
                     Needs the public podcast repo (PODCAST_REPO/PODCAST_TOKEN) for the link.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

import digest
import podfeed


def _redact(text: str) -> str:
    text = re.sub(r"bot\d+:[\w-]+", "bot***", text)
    text = re.sub(r"(apikey|key|token)=[^&\s]+", r"\1=***", text, flags=re.I)
    return re.sub(r"github_pat_\w+|gh[pousr]_\w+|AIza[\w-]{20,}", "***", text)

ROOT = Path(__file__).parent
TELUGU_DIR = ROOT / "briefs" / "telugu"     # kept out of story memory on purpose


def parents_configured() -> bool:
    return bool(os.getenv("PARENTS_EMAIL", "").strip() or os.getenv("PARENTS_WHATSAPP", "").strip())


def write_telugu_letter(english_plain: str, markets_plain: str, date_str: str) -> str | None:
    system = (ROOT / "prompts" / "telugu_brief_prompt.md").read_text()
    user = (f"DATE: {date_str}\n\nMARKETS: {markets_plain or '(none)'}\n\n"
            f"ENGLISH_BRIEF:\n{english_plain}")
    text = digest.ask(system, user, prefer="gemini")
    if not text or len(re.findall(r"[ఀ-౿]", text)) < 200:   # must really be Telugu
        print("  ! Telugu letter missing or not in Telugu")
        return None
    return re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text.strip()) + "\n"


def headlines(md: str, n: int = 3) -> list[str]:
    sec = re.search(r"^## 1\..*?(?=^## )", md, re.S | re.M)
    lines = re.findall(r"^- (.+)$", sec.group(0) if sec else md, re.M)[:n]
    return [re.sub(r"[*_#`]", "", l).strip()[:140] for l in lines]


def listen_block(url: str | None, minutes: int | None) -> str:
    if not (url or minutes):
        return ""
    parts = ["## 🎧 వార్తలు వినండి", ""]
    if url:
        parts.append(f"**▶️ [ఇక్కడ నొక్కి వినండి]({url})** · ఈ ఈమెయిల్‌తో MP3 కూడా జత చేశాం")
    else:
        parts.append("**ఈ ఈమెయిల్‌తో MP3 జత చేశాం — దాన్ని నొక్కి వినండి**")
    if minutes:
        parts.append(f"సుమారు **{minutes} నిమిషాలు**")
    return "  \n".join(parts[:1]) + "\n\n" + "  \n".join(parts[2:]) + "\n"


def send_whatsapp(text: str) -> None:
    pairs = [p.strip() for p in os.getenv("PARENTS_WHATSAPP", "").split(",") if p.strip()]
    for n, pair in enumerate(pairs, 1):
        m = re.fullmatch(r"\+?(\d{8,15}):(\w{4,40})", pair)
        if not m:
            print(f"  ! WhatsApp entry {n} is not in phone:apikey format; skipped")
            continue
        try:
            r = requests.get("https://api.callmebot.com/whatsapp.php",
                             params={"phone": "+" + m.group(1), "text": text, "apikey": m.group(2)},
                             timeout=30)
            ok = r.status_code == 200 and "error" not in r.text.lower()
            print(f"  WhatsApp {n}: {'sent' if ok else f'failed (HTTP {r.status_code})'}")
        except Exception as exc:          # never print the URL: it contains the apikey
            print(f"  ! WhatsApp {n} failed: {type(exc).__name__}")


def run(english_final: str, markets_plain: str, date_str: str, now: datetime,
        out_dir: Path, dry: bool) -> None:
    """Build and deliver the Telugu edition. Never raises: English edition must not suffer."""
    try:
        english_plain = digest.strip_links(english_final)
        letter = write_telugu_letter(english_plain, markets_plain, date_str)
        if not letter:
            return
        from podcast import make_podcast
        mp3 = None
        if os.getenv("PODCAST", "1") == "1":
            mp3 = make_podcast(letter.replace("{{LISTEN}}", ""), date_str, out_dir,
                               lambda s, u: digest.ask(s, u, prefer="gemini"),
                               length="About 900 to 1,400 Telugu words (8 to 12 minutes spoken).",
                               show="ఈ రోజు వార్తలు", lang="te")
        url, minutes = None, None
        if mp3:
            minutes = digest.audio_minutes(mp3)
            if podfeed.configured() and not dry:
                try:
                    url = podfeed.upload_asset(mp3, f"{now:%Y-%m-%d}-telugu.mp3")
                    podfeed.prune_extra("-telugu.mp3", keep=21)
                except Exception as exc:
                    print(f"  ! Telugu audio upload failed: {type(exc).__name__}")
        letter = letter.replace("{{LISTEN}}", listen_block(url, minutes))
        letter = re.sub(r"\n{3,}", "\n\n", letter)

        title = f"🌏 ఈ రోజు వార్తలు — {now:%d-%m-%Y}" + (" 🎧" if mp3 else "")
        if dry:
            print("\n----- TELUGU EDITION -----\n" + letter)
            return
        to = os.getenv("PARENTS_EMAIL", "").strip()
        if to:
            digest.send_email(title, letter, mp3, to=to,
                              audio_name=f"Telugu-News-{now:%Y-%m-%d}.mp3")
        if os.getenv("PARENTS_WHATSAPP", "").strip():
            if url:
                msg = ("🙏 నమస్కారం! ఈ రోజు వార్తలు వినండి 🎧\n" + url + "\n\n"
                       + "\n".join(f"• {h}" for h in headlines(letter)))
                send_whatsapp(msg[:1500])
            else:
                print("  WhatsApp skipped: needs the public podcast link (PODCAST_REPO/PODCAST_TOKEN)")
        TELUGU_DIR.mkdir(parents=True, exist_ok=True)
        (TELUGU_DIR / f"{now:%Y-%m-%d}.md").write_text(letter)
    except Exception as exc:
        print(f"  ! Telugu edition failed: {type(exc).__name__}: {_redact(str(exc))[:200]}", file=sys.stderr)
