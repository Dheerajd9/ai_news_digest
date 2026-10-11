"""
Parents' edition: the same fact-checked morning news as a short, simple ENGLISH email,
plus a Ravi & Lakshmi podcast in Telugu (Telangana yaasa, straight to the news).

Delivery (all optional, all free):
  PARENTS_EMAIL      one or more addresses, comma-separated -> English email + Telugu MP3
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


def write_parents_letter(english_plain: str, markets_plain: str, date_str: str) -> str | None:
    """Simple English email for the parents (the podcast is in Telugu)."""
    system = (ROOT / "prompts" / "parents_brief_prompt.md").read_text().replace("{DATE}", date_str)
    user = (f"DATE: {date_str}\n\nMARKETS: {markets_plain or '(none)'}\n\n"
            f"ENGLISH_BRIEF:\n{english_plain}")
    text = digest.ask(system, user, prefer="gemini")
    if not text or len(text.split()) < 120:
        print("  ! parents' letter missing or too short")
        return None
    telugu_chars = len(re.findall("[\u0C00-\u0C7F]", text))
    if telugu_chars > 0.2 * len(text):                     # must be English
        print("  ! parents' letter came back in Telugu; skipped")
        return None
    return re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text.strip()) + "\n"


def headlines(md: str, n: int = 3) -> list[str]:
    sec = re.search(r"^## 1\..*?(?=^## )", md, re.S | re.M)
    lines = re.findall(r"^- (.+)$", sec.group(0) if sec else md, re.M)[:n]
    return [re.sub(r"[*_#`]", "", l).strip()[:140] for l in lines]


def listen_block(url: str | None, minutes: int | None) -> str:
    if not (url or minutes):
        return ""
    parts = ["## 🎧 Listen in Telugu", ""]
    if url:
        parts.append(f"**▶️ [Tap here to listen]({url})** · the MP3 is also attached to this email")
    else:
        parts.append("**The Telugu news audio (MP3) is attached to this email: tap it to listen**")
    if minutes:
        parts.append(f"About **{minutes} minutes**")
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
        letter = write_parents_letter(english_plain, markets_plain, date_str)
        if not letter:
            return
        from podcast import make_podcast
        mp3 = None
        if os.getenv("PODCAST", "1") == "1":
            from podcast import latest_previous_script
            mp3 = make_podcast(letter.replace("{{LISTEN}}", ""), date_str, out_dir,
                               lambda s, u: digest.ask(s, u, prefer="gemini"),
                               length="About 700 to 1,000 Telugu words (5 to 8 minutes spoken).",
                               show="ఈ రోజు వార్తలు", lang="te", report_type="MORNING",
                               previous_script=latest_previous_script(TELUGU_DIR, "podcast", 3000))
        url, minutes = None, None
        if mp3:
            minutes = digest.audio_minutes(mp3)
            if podfeed.configured() and not dry:
                for attempt in (1, 2):
                    try:
                        url = podfeed.upload_asset(mp3, f"{now:%Y-%m-%d}-telugu.mp3")
                        print(f"  Telugu audio uploaded ({mp3.stat().st_size / 1e6:.1f} MB)")
                        break
                    except Exception as exc:
                        print(f"  ! Telugu audio upload attempt {attempt} failed: "
                              f"{type(exc).__name__}: {_redact(str(exc))[:200]}")
                if url:
                    try:
                        podfeed.prune_extra("-telugu.mp3", keep=21)
                    except Exception as exc:
                        print(f"  ! Telugu prune failed: {type(exc).__name__}")
            elif not podfeed.configured():
                print("  Telugu audio: no public podcast repo configured, email attachment only")
        letter = letter.replace("{{LISTEN}}", listen_block(url, minutes))
        letter = re.sub(r"\n{3,}", "\n\n", letter)

        title = f"🌏 Today's News — {now:%d %b %Y}" + (" 🎧 Telugu audio" if mp3 else "")
        if dry:
            print("\n----- PARENTS' EDITION -----\n" + letter)
            return
        to = os.getenv("PARENTS_EMAIL", "").strip()
        if to:
            digest.send_email(title, letter, mp3, to=to,
                              audio_name=f"Telugu-News-{now:%Y-%m-%d}.mp3")
        if os.getenv("PARENTS_WHATSAPP", "").strip():
            if url:
                msg = ("🎧 Today's news in Telugu: " + url + "\n\n"
                       + "\n".join(f"• {h}" for h in headlines(letter)))
            else:   # no public link today: still send the headlines
                msg = ("Today's top news:\n\n"
                       + "\n".join(f"• {h}" for h in headlines(letter))
                       + ("\n\n🎧 Telugu audio is in the email." if to else ""))
                print("  WhatsApp: sending headlines without audio link")
            send_whatsapp(msg[:1500])
        TELUGU_DIR.mkdir(parents=True, exist_ok=True)
        (TELUGU_DIR / f"{now:%Y-%m-%d}.md").write_text(letter)
        script = out_dir / "script_telugu_podcast.txt"
        if script.exists():
            (TELUGU_DIR / f"{now:%Y-%m-%d}-podcast.txt").write_text(script.read_text())
    except Exception as exc:
        print(f"  ! Telugu edition failed: {type(exc).__name__}: {_redact(str(exc))[:200]}", file=sys.stderr)
