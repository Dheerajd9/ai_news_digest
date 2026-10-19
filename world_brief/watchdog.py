"""Watchdog: keeps the whole system healthy without anyone looking (runs every afternoon).

1. Missing-email alarm: at about 2 PM US Eastern, if today's World Brief morning email (or the
   Sunday weekly, or the AI News Digest) was never sent, alert Dji by email + Telegram.
   Catches the silent case where nothing ran at all (GitHub timer and cron-job.org both missed).
2. Every Sunday: tests every news feed (feedcheck.py). A feed that is dead or empty two Sundays
   in a row is removed from feeds.yaml automatically; a short report is emailed when something
   was removed or too few feeds still work.

Sends nothing on a normal day. Never prints secrets.
"""

from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
REPO = ROOT.parent
TZ = ZoneInfo("America/New_York")
HEALTH = ROOT / "state" / "feed_health.json"
BAD_WEEKS = 2            # dead/empty this many Sundays in a row -> removed
MIN_OK = 150             # fewer working feeds than this -> warning email


def alert(subject: str, body: str) -> None:
    """Email + Telegram (personal chat) to Dji. Never raises."""
    user, pwd, to = (os.getenv(k, "").strip() for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "TO_EMAIL"))
    if user and pwd and to:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, user, to
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
                s.login(user, pwd)
                s.sendmail(user, [a.strip() for a in to.split(",") if a.strip()], msg.as_string())
            print("alert emailed")
        except Exception as exc:
            print(f"could not email alert: {type(exc).__name__}")
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if token and chat:
        try:
            import requests
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                              json={"chat_id": chat, "text": f"{subject}\n\n{body}",
                                    "disable_web_page_preview": True})
            print(f"Telegram alert: HTTP {r.status_code}")
        except Exception as exc:      # the URL holds the bot token: print only the error type
            print(f"could not send Telegram alert: {type(exc).__name__}")


def missing(now: datetime) -> list[str]:
    """What should have been sent by now today but wasn't."""
    d = now.strftime("%Y-%m-%d")
    done = ROOT / "state" / "done"
    out = []
    if not (done / f"{d}-morning").exists():
        out.append("World Brief morning email")
    if now.isoweekday() == 7 and not (done / f"{d}-weekly").exists():
        out.append("World Brief Sunday weekly")
    try:
        ai_done = json.loads((REPO / "digest_done.json").read_text()).get("Morning")
    except Exception:
        ai_done = None
    if ai_done != d:
        out.append("AI News Digest morning email")
    return out


def check_sent(now: datetime) -> list[str]:
    miss = missing(now)
    if miss:
        alert(f"⏰ Not sent today: {', '.join(miss)}",
              "These were not sent by 2 PM US Eastern today:\n- " + "\n- ".join(miss)
              + "\n\nUsually GitHub's timer and the cron-job.org timer both missed, or a run failed."
              "\nTo send now: add a line 'run: morning' to world_brief/run_request.txt "
              "(or ask Claude). Check also that the cron-job.org token has not expired."
              + (f"\n\nDetails: {os.getenv('RUN_URL')}" if os.getenv("RUN_URL") else ""))
    else:
        print("all of today's emails were sent")
    return miss


def prune_feeds(rows: list[dict], health: dict, feeds_text: str) -> tuple[dict, str, list[str]]:
    """Count bad Sundays per feed; drop feeds bad BAD_WEEKS Sundays in a row.
    If over a quarter of all feeds look bad, it's a network problem: count nothing."""
    bad = {r["url"] for r in rows if r["status"] in ("dead", "empty")}
    if len(bad) > len(rows) / 4:
        print(f"{len(bad)} of {len(rows)} feeds bad: looks like a network problem, nothing counted")
        return health, feeds_text, []
    new = {u: health.get(u, 0) + 1 for u in bad}
    removed = [u for u, n in new.items() if n >= BAD_WEEKS]
    for u in removed:
        feeds_text = feeds_text.replace(f"  - {u}\n", "")
        new.pop(u)
    return new, feeds_text, removed


def check_feeds() -> None:
    import feedcheck
    import yaml
    path = ROOT / "feeds.yaml"
    text = path.read_text()
    rows = feedcheck.check(yaml.safe_load(text))
    try:
        health = json.loads(HEALTH.read_text())
    except Exception:
        health = {}
    health, text, removed = prune_feeds(rows, health, text)
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "feedcheck.txt").write_text(feedcheck.report(rows) + "\n")
    HEALTH.parent.mkdir(exist_ok=True)
    HEALTH.write_text(json.dumps(health, indent=1))
    if removed:
        path.write_text(text)
    ok = sum(r["status"] == "ok" for r in rows)
    print(f"feeds: {len(rows)}, ok {ok}, bad this week {len(health)}, removed {len(removed)}")
    if removed or ok < MIN_OK:
        alert(f"📰 Weekly feed check: {ok} of {len(rows)} news feeds working",
              (("Removed (dead or empty two Sundays in a row):\n- " + "\n- ".join(removed) + "\n\n")
               if removed else "")
              + (f"Only {ok} feeds work now (aim: {MIN_OK}+). Ask Claude to add new sources.\n"
                 if ok < MIN_OK else "")
              + "Full report: world_brief/logs/feedcheck.txt")


def main() -> None:
    now = datetime.now(TZ)
    hour = int(os.getenv("START_H") or now.hour)   # hour the slot started (before any waiting)
    force = os.getenv("WATCHDOG_FORCE") == "1"
    if hour == 14 or force:            # two UTC slots; only the one at 2 PM Eastern acts
        check_sent(now)
    else:
        print(f"{hour}h Eastern: not the 2 PM slot, skipping the sent check")
    if (now.isoweekday() == 7 and hour == 14) or os.getenv("FEEDCHECK") == "1":
        check_feeds()


if __name__ == "__main__":
    main()
