"""
End-to-end test of the whole pipeline with fake news, a fake AI and silent audio.
No network, no email, no secrets. Runs on every code push (World Brief + Podcast workflow);
if any check fails, the push run turns red before a real email can go out broken.

  python tests/e2e.py            # morning, evening and weekly, in a temp copy of world_brief/

Checks for each edition: exactly one email, ▶️ play links for English + Telugu + Hindi,
chapter files, brief archive, podcast-script memory, seen list and story ledger saved.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

RUNNER = r'''
import json, os, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, os.getcwd())
import digest, markets, podcast, podfeed, languages, weather
weather.fetch = lambda lat, lon: {"current": {"temperature_2m": 28.4, "weather_code": 61},
    "daily": {"temperature_2m_max": [31, 32], "temperature_2m_min": [22, 23],
              "precipitation_probability_max": [60, 40], "weather_code": [63, 2]}}
STYLE = {"wx": 0, "style": 0, "tr": 0}

EDITION = os.environ["EDITION"]
digest.on_this_day = lambda today: [{"year": 1947, "text": "Test history event"}]
markets.numbers_box = lambda weekly=False: (
    "## 📊 Money & markets\n\n| Asset | Latest | Change | Status |\n|---|---:|---:|---|\n"
    "| 🇮🇳 Nifty 50 | 23,146 | ▲ +0.65% | PREVIOUS CLOSE |\n| 💵 USD/INR | ₹94.96 | ▼ -0.20% | DELAYED |\n"
    "| 🪙 Gold 24K India (₹/10 grams) | ₹1,46,430 | — | IBJA |\n", "Nifty 1")

def fake_items(feeds):
    cats = ["india", "usa", "world", "india_economy", "tech_ai"]
    out = []
    for n in range(15):
        link = f"https://news.example.com/{EDITION}/{n}"
        digest.KNOWN_LINKS.add(link)
        out.append({"category": cats[n % len(cats)], "source": "The Hindu",
                    "title": f"{EDITION} test headline: " + " ".join(
                        ["monsoon", "senate", "rupee", "chips", "robots", "tariff", "election",
                         "satellite", "vaccine", "housing", "jobs", "oil", "court", "visa",
                         "startup"][(n + k) % 15] for k in (0, 3, 7)),
                    "summary": "Something happened.", "published": "2026-10-13T06:00:00+00:00",
                    "link": link})
    return out
digest.fetch_items = fake_items

HOSTS = [("KARTIK", "PRIYA"), ("RAVI", "LAKSHMI"), ("ARJUN", "MEERA")]

def script(a, b):
    lines = []
    for ch in ["Top stories", "India", "USA", "World", "Money", "Wrap-up"]:
        lines.append(f"CHAPTER: {ch}")
        for i in range(3):
            lines.append(f"{a}: Line {i} about {ch}, explained simply for the listener today.")
            lines.append(f"{b}: Reply {i} about {ch}, with context and what happens next.")
    return "\n".join(lines)

BRIEF = """# World Brief
**Most important:** test

{{LISTEN}}

## 1. Top 5
- India story one [S1]
- USA story two [S2]

## 2. India
- Detail [S3]

{{MARKET_SNAPSHOT}}

<!--META
REPORT_TYPE: X
PODCAST_REQUIRED: TRUE
PODCAST_TOPICS: India story; USA story
TOP_STORY_COUNT: 5
URGENT_ALERT: FALSE
VERIFICATION_WARNINGS: NONE
-->
<!--STORIES [{"id": "test-story", "title": "Test story", "status": "DEVELOPING", "stage": "PROPOSED", "summary": "s", "milestone": "m"}] -->
<!--BELIEFS [{"belief": "b", "confidence": "MEDIUM"}] -->
"""

CALLS = []
def fake_gemini(system, user):
    CALLS.append(system[:80])
    if "LOCAL_HEADLINES" in system:
        n = len([l for l in user.splitlines() if l.strip()])
        return "\n".join([f"TE{i}: తెలుగు వార్త {i}" for i in range(1, n + 1)]
                         + [f"HI{i}: हिंदी खबर {i}" for i in range(1, n + 1)])
    if ("TRANSLATION MODE" in system and "ENGLISH_SCRIPT:" in user and "KARTIK:" in user
            and "CHAPTER:" in user):
        STYLE["tr"] += 1
    if "EXPLAINING STYLE" in system:
        STYLE["style"] += 1
        STYLE["wx"] += "WEATHER (local days): Hyderabad: now 28°C, light rain; today 22–31°C, rain, rain chance 60%" in user
    if user.startswith("SCRIPT:"):
        return user[len("SCRIPT:"):].strip()
    if "FINAL OUTPUT REMINDER" in system:              # the news brief itself
        return BRIEF
    for a, b in HOSTS:
        if os.environ.get("FAIL_HOST") == a and (f"`{a}:`" in system or f"with exactly `{a}:`" in system):
            return None
        if f"`{a}:`" in system or f"with exactly `{a}:`" in system or f"{a} and {b}" in system[:400]:
            return script(a, b)
    if "DRAFT:" in user:
        return user.split("DRAFT:", 1)[1].strip()
    return BRIEF
digest.call_gemini = fake_gemini
digest.call_github_models = lambda s, u: None
digest.call_mistral = lambda s, u: None                # backup brains "down": must not matter
digest.call_openrouter = lambda s, u: None
podcast.cloud_tts_podcast = lambda *a, **k: None
if os.environ.get("GEMINI_DOWN") == "1":              # drill: Gemini fully down
    digest.call_mistral = fake_gemini
    digest.call_gemini = lambda s, u: None

def silent(text, speaker, out, profile=None):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", "1", "-q:a", "9", str(out)], check=True)
podcast.speak_line = silent
podcast.gemini_podcast = lambda turns, work, profile, pref: None

podfeed.configured = lambda: ("Owner/world-brief-podcast", "token")
UPLOADS = []
def fake_upload(path, name):
    UPLOADS.append(name)
    ch = path.parent / f"chapters_{path.stem}.json"
    if ch.exists():
        UPLOADS.append("chapters:" + name)
    return f"https://github.com/Owner/world-brief-podcast/releases/download/episodes/{name}"
podfeed.upload_asset = fake_upload
podfeed.prune_extra = lambda *a, **k: None
FEEDS_PUB = []
podfeed.publish_language_episode = lambda lang, mp3, name, title, desc: FEEDS_PUB.append([lang, name])
TG = []
class _R:
    status_code = 200
TG_TEXT = []
digest.requests.post = lambda url, **kw: (TG.append(url.rsplit("/", 1)[-1]),
                                          TG_TEXT.append((kw.get("json") or {}).get("text", "")), _R())[2]
os.environ["TELEGRAM_BOT_TOKEN"] = "123:abc"
os.environ["TELEGRAM_CHANNEL_ID"] = "@family"
os.environ.pop("TELEGRAM_CHAT_ID", None)
SYNCED = []
podfeed.sync_site_audio = lambda mp3s: SYNCED.extend(n for _, n in mp3s) or True
podfeed.publish_episode = lambda ep: fake_upload(Path(ep["file"]),
                                                f"2026-10-13-{ep['edition']}.mp3")

EMAILS = []
digest.send_email = lambda subject, md, audio=None, to=None, audio_name=None, everyone=False: \
    EMAILS.append({"subject": subject, "md": md, "everyone": everyone,
                   "audio": [str(a) for a in (audio or [])]})
digest.send_telegram = lambda *a, **k: None
digest.token_reminder = lambda *a, **k: None
languages.send_whatsapp = lambda *a, **k: None

digest.main()
Path("result.json").write_text(json.dumps({"emails": EMAILS, "uploads": UPLOADS, "synced": SYNCED,
                                           "feeds": FEEDS_PUB, "telegram": TG, "style": STYLE, "tg_text": "".join(TG_TEXT)}))
'''


def check(cond: bool, msg: str, errors: list[str]) -> None:
    print(("  ok    " if cond else "  FAIL  ") + msg)
    if not cond:
        errors.append(msg)


def run_edition(work: Path, edition: str, errors: list[str]) -> None:
    print(f"\n=== {edition} ===")
    env = {**os.environ, "EDITION": edition, "PODCAST": "1", "PODCAST_REVIEW": "1",
           "GITHUB_EVENT_NAME": "push", "PODCAST_LANGUAGES": "te,hi",
           "LANG_EDITIONS": "morning,evening,weekly", "WB_ASSUME_PLAYER": "1"}
    for k in ("DRY_RUN", "NO_LLM", "GEMINI_API_KEY", "GITHUB_TOKEN", "MISTRAL_API_KEY",
              "OPENROUTER_API_KEY", "GOOGLE_TTS_API_KEY"):
        env.pop(k, None)
    (work / "runner.py").write_text(RUNNER)
    p = subprocess.run([sys.executable, "runner.py"], cwd=work, env=env,
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        print(p.stdout[-3000:], p.stderr[-3000:])
        check(False, f"{edition}: pipeline crashed", errors)
        return
    res = json.loads((work / "result.json").read_text())
    emails, uploads = res["emails"], res["uploads"]
    sfx = "" if edition == "morning" else f"-{edition}"
    check(len(emails) == 1, f"{edition}: exactly one email (got {len(emails)})", errors)
    if not emails:
        return
    md = emails[0]["md"]
    check(emails[0]["everyone"], f"{edition}: email goes to all readers (BCC)", errors)
    check("{{LISTEN}}" not in md and "{{MARKET_SNAPSHOT}}" not in md,
          f"{edition}: no leftover placeholders", errors)
    for name in (f"telugu{sfx}", f"hindi{sfx}"):
        check(f"play.html?f=2026-10-13-{name}.mp3" in md or f"-{name}.mp3" in md,
              f"{edition}: ▶️ play link for {name}", errors)
    check(md.count("play.html?f=") >= 3, f"{edition}: English + Telugu + Hindi play links "
                                            f"({md.count('play.html?f=')} found)", errors)
    check(not emails[0]["audio"], f"{edition}: no big attachments when links work", errors)
    check("తెలుగులో ముఖ్యాంశాలు" not in md and "हिंदी में मुख्य खबरें" not in md,
          f"{edition}: email text is English only", errors)
    check("Markets now:** " in md and "Nifty 50 23,146 ▲0.65%" in md and "Gold 10g ₹1,46,430" in md,
          f"{edition}: one-line market strip", errors)
    check("India story one" in emails[0]["subject"], f"{edition}: subject shows the top stories "
          f"({emails[0]['subject'][:80]})", errors)
    import markdown as _mdlib, email_style
    page = email_style.page(_mdlib.markdown(md, extensions=["extra"]))
    check(page.count("border-radius:24px") >= 3 and "border-left:4px solid" in page
          and "#fff7e6" in page and 'color:#16a34a">▲' in page,
          f"{edition}: email has play buttons, story cards, Top 5 box, green/red arrows", errors)
    st = res.get("style", {})
    check(st.get("style", 0) >= 3 and st.get("wx", 0) == st.get("style"),
          f"{edition}: every podcast gets the explaining style + Hyderabad/Dallas weather {st}", errors)
    if edition != "weekly":
        tail = md.split("More headlines (not covered above)")[-1]
        check("More headlines (not covered above)" in md and "](http" in tail,
              f"{edition}: '📰 More headlines' lists every other story with links", errors)
        check("More headlines" not in "".join(
            p.read_text() for p in (work / "briefs" / "podcasts").glob("*.txt")) if
            (work / "briefs" / "podcasts").exists() else True,
            f"{edition}: podcasts don't read out the extra headline list", errors)
    langs_fed = sorted({f[0] for f in res.get("feeds", [])})
    check(langs_fed == ["hi", "te"], f"{edition}: Telugu + Hindi podcast-app feeds updated ({langs_fed})", errors)
    tg = res.get("telegram", [])
    check(tg.count("sendMessage") == 1 and tg.count("sendAudio") >= 3,
          f"{edition}: Telegram family channel gets the message + every podcast ({tg.count('sendAudio')} audios)", errors)
    check(st.get("tr") == 2, f"{edition}: Telugu + Hindi made from the same English episode "
          f"(same stories and details; {st.get('tr')} conversions)", errors)
    tgt = res.get("tg_text", "")
    check(tgt.count('▶️ <a href="https://') >= 3 and ">https://" not in tgt,
          f"{edition}: Telegram shows named play links (no bare URLs)", errors)
    check(len(res.get("synced", [])) >= 3, f"{edition}: audio copied to the player site "
                                            f"({len(res.get('synced', []))} files)", errors)
    check(sum(u.startswith("chapters:") for u in uploads) >= 3,
          f"{edition}: chapter files published", errors)
    import datetime
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).strftime("%Y-%m-%d")
    arch = work / "briefs" / f"{today}{'' if edition == 'morning' else '-' + edition}.md"
    check(arch.exists(), f"{edition}: brief archived for tomorrow's memory", errors)
    for lang in ("telugu", "hindi"):
        check(any((work / "briefs" / lang).glob("*-podcast.txt")),
              f"{edition}: {lang} script kept as memory", errors)
    check(any((work / "briefs" / "podcasts").glob("*.txt")),
          f"{edition}: English scripts kept as memory", errors)
    if edition != "weekly":
        seen = json.loads((work / "state" / "seen.json").read_text())
        check(any(f"{edition} test headline" in k or True for k in seen) and len(seen) > 0,
              f"{edition}: seen list saved (no repeats tomorrow)", errors)
    stories = json.loads((work / "state" / "stories.json").read_text())
    check("test-story" in json.dumps(stories), f"{edition}: story ledger saved", errors)


def main() -> int:
    if not shutil.which("ffmpeg"):
        print("ffmpeg missing; install it first")
        return 2
    errors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "wb"
        shutil.copytree(SRC, work, ignore=shutil.ignore_patterns(
            "briefs", "state", "logs", "out", "tests", "__pycache__"))
        # Topic coverage: every requested section, feed group and money row is wired in.
        print("\n=== topic coverage ===")
        bp = (SRC / "prompts" / "brief_prompt.md").read_text()
        for sec in ["What actually matters today", "Canada", "AI & robotics (highest priority)",
                    "Musk & frontier companies", "Chips & computing", "Major world changes",
                    "Government & politics", "Personal finance", "Housing & cost of living",
                    "Business, jobs & the future of work", "Energy revolution", "Future technology",
                    "Space", "Truth check", "Future signal", "Today's lesson", "Why markets moved",
                    "Hyderabad & Telangana", "Around the world (region by region)"]:
            check(f"## " in bp and sec in bp, f"brief has section: {sec}", errors)
        import yaml
        groups = yaml.safe_load((SRC / "feeds.yaml").read_text())
        for g in ["canada", "business_jobs", "personal_finance_housing", "fact_check",
                  "ai_robotics", "frontier_companies", "chips_energy_future", "world_regions"]:
            check(bool(groups.get(g)), f"feeds: {g}", errors)
        mk = (SRC / "markets.py").read_text()
        check("USD/CAD" in mk and "Ethereum" in mk, "money table: USD/CAD and Ethereum", errors)
        te_p = (SRC / "prompts" / "telugu_podcast_prompt.md").read_text()
        hi_p = (SRC / "prompts" / "hindi_podcast_prompt.md").read_text()
        check("Tenglish" in te_p and "Telangana yaasa" in te_p and "numbers" in te_p.lower() or "number, price" in te_p,
              "Telugu: Telangana Telugu + numbers/hard words in simple English", errors)
        check("Hinglish" in hi_p and "number, price" in hi_p and "Telugu" not in hi_p,
              "Hindi: Hinglish + numbers/hard words in simple English, no Telugu leftovers", errors)
        for name in ("evening_prompt.md", "weekly_prompt.md"):
            check("EXTRA TOPICS" in (SRC / "prompts" / name).read_text(), f"{name}: extra topics", errors)

        for edition in ("morning", "evening", "weekly"):
            run_edition(work, edition, errors)
        # Failure drill: Telugu script can't be written -> email still goes out, says so.
        print("\n=== morning, Telugu AI down ===")
        env = {**os.environ, "EDITION": "morning", "FAIL_HOST": "RAVI",
               "PODCAST_LANGUAGES": "te,hi", "LANG_EDITIONS": "morning", "GITHUB_EVENT_NAME": "push",
               "WB_ASSUME_PLAYER": "1"}
        for k in ("DRY_RUN", "NO_LLM", "GEMINI_API_KEY", "GITHUB_TOKEN", "MISTRAL_API_KEY",
              "OPENROUTER_API_KEY", "GOOGLE_TTS_API_KEY"):
            env.pop(k, None)
        import time as _t
        p = subprocess.run([sys.executable, "-c",
                            "import time; time.sleep=lambda s: None; exec(open('runner.py').read())"],
                           cwd=work, env=env, capture_output=True, text=True, timeout=600)
        res = json.loads((work / "result.json").read_text()) if p.returncode == 0 else {"emails": []}
        md = res["emails"][0]["md"] if res["emails"] else ""
        check(len(res["emails"]) == 1, "Telugu down: email still sent", errors)
        check("could not be made" in md and "Telugu" in md, "Telugu down: email says Telugu audio is missing", errors)
        check("hindi.mp3" in md, "Telugu down: Hindi still delivered", errors)

        # Drill 2: Gemini completely down -> free backup brain writes everything.
        print("\n=== morning, Gemini down (Mistral backup) ===")
        (work / "state" / "seen.json").unlink(missing_ok=True)   # fresh news for this drill
        env2 = {**env, "GEMINI_DOWN": "1"}
        env2.pop("FAIL_HOST", None)
        p = subprocess.run([sys.executable, "-c",
                            "import time; time.sleep=lambda s: None; exec(open('runner.py').read())"],
                           cwd=work, env=env2, capture_output=True, text=True, timeout=600)
        res = json.loads((work / "result.json").read_text()) if p.returncode == 0 else {"emails": []}
        md = res["emails"][0]["md"] if res["emails"] else ""
        if not res["emails"]:
            print(p.stdout[-1500:], p.stderr[-1500:])
        check(len(res["emails"]) == 1 and "LLM unavailable" not in md,
              "Gemini down: full brief still written by the backup brain", errors)
        check(md.count("play.html?f=") >= 3, "Gemini down: podcasts still made", errors)

        # Drill: player-site audio sync with a local git repo (fresh single commit, prune old).
        print("\n=== player site audio sync ===")
        bare = Path(tmp) / "site.git"
        seed = Path(tmp) / "seed"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "MASTER", str(bare)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "MASTER", str(seed)], check=True)
        (seed / "audio").mkdir()
        (seed / "play.html").write_text("World Brief")
        (seed / "audio" / "2020-01-01-old.mp3").write_bytes(b"old")
        g = ["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(g + ["add", "-A"], check=True)
        subprocess.run(g + ["commit", "-qm", "c1"], check=True)
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "c2"], check=True)
        subprocess.run(g + ["push", "-q", str(bare), "MASTER"], check=True)
        mp3 = Path(tmp) / "x.mp3"
        mp3.write_bytes(b"ID3audio")
        drill2 = f"""
import os, sys, json
sys.path.insert(0, os.getcwd())
import podfeed
podfeed.configured = lambda: ("Owner/site", "tok")
class J:
    def json(self): return {{"default_branch": "MASTER"}}
podfeed.gh = lambda *a, **k: J()
os.environ["PODCAST_GIT_URL"] = {str(bare)!r}
from pathlib import Path
ok = podfeed.sync_site_audio([(Path({str(mp3)!r}), "2099-01-01-telugu.mp3")])
print(json.dumps({{"ok": ok}}))
"""
        p = subprocess.run([sys.executable, "-c", drill2], cwd=work, capture_output=True, text=True, timeout=120)
        files = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "MASTER"],
                               capture_output=True, text=True).stdout.split()
        commits = subprocess.run(["git", "-C", str(bare), "rev-list", "--count", "MASTER"],
                                 capture_output=True, text=True).stdout.strip()
        check('"ok": true' in p.stdout, "site sync: push succeeded", errors)
        check("audio/2099-01-01-telugu.mp3" in files and "play.html" in files,
              "site sync: new audio added, player page kept", errors)
        check("audio/2020-01-01-old.mp3" not in files, "site sync: old audio removed", errors)
        check(commits == "1", f"site sync: history stays one commit (got {commits})", errors)

        # Feed XML: all three languages, enclosures on the site, old episodes left out.
        print("\n=== podcast feeds ===")
        feed_py = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
import podfeed
from datetime import datetime, timezone, timedelta
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d")
eps = [{"id": today + "-telugu.mp3", "title": "T & <x>", "description": "d", "bytes": 10, "pubDate": "x", "duration": 60},
       {"id": old + "-telugu.mp3", "title": "old", "description": "d", "bytes": 10, "pubDate": "x", "duration": 60}]
out = {l: podfeed.build_feed("Owner/world-brief-podcast", eps, l) for l in ("en", "te", "hi")}
print(json.dumps({"te_has_today": "audio/" + today + "-telugu.mp3" in out["te"], "old_dropped": old not in out["te"],
                  "escaped": "&amp;" in out["te"] and "<x>" not in out["te"], "te_lang": "<language>te</language>" in out["te"],
                  "hi_title": "Hindi" in out["hi"], "en_title": "<title>World Brief</title>" in out["en"]}))
"""
        p = subprocess.run([sys.executable, "-c", feed_py], cwd=work, capture_output=True, text=True, timeout=60)
        try:
            r = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            print(p.stdout[-800:], p.stderr[-800:])
            r = {}
        check(bool(r) and all(r.values()), f"feeds: three language feeds valid, site audio links, old items dropped {r}", errors)

        # Telegram setup: finds the channel from the bot's updates, saves it, digest uses it.
        print("\n=== Telegram auto-setup ===")
        tg_py = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["TELEGRAM_BOT_TOKEN"] = "1:x"; os.environ.pop("TELEGRAM_CHANNEL_ID", None)
import tgsetup, digest
class G:
    status_code = 200
    def json(self): return {"result": [{"my_chat_member": {"chat": {"id": -1009876, "type": "channel", "title": "Family News"}}}]}
posts = []
tgsetup.requests.get = lambda *a, **k: G()
tgsetup.requests.post = lambda url, **k: (posts.append(k["json"]["chat_id"]), G())[1]
mails = []
digest.send_email = lambda s, m, *a, **k: mails.append(m)
tgsetup.main()
saved = json.loads(tgsetup.STATE.read_text())
sent = []
digest.requests.post = lambda url, **k: (sent.append((url.rsplit("/",1)[-1], (k.get("json") or k.get("data") or {}).get("chat_id"))), G())[1]
open("e.mp3","wb").write(b"x")
digest.send_telegram_channel("T", "## 1. Top 5\n- a\n", [{"file": "e.mp3", "url": None, "label": "L", "hosts": "H"}])
print(json.dumps({"saved": saved["id"], "test_post": posts, "mail_ok": "Found" in mails[0], "used": sent}))
"""
        p = subprocess.run([sys.executable, "-c", tg_py], cwd=work, capture_output=True, text=True, timeout=60)
        try:
            r = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            print(p.stdout[-800:], p.stderr[-800:])
            r = {}
        check(r.get("saved") == -1009876 and r.get("test_post") == [-1009876] and r.get("mail_ok"),
              "Telegram setup: channel found, saved, test posted, email sent", errors)
        check(r.get("used") and all(c in (-1009876, "-1009876") for _, c in r["used"]),
              "Telegram setup: editions use the saved channel automatically", errors)

        # Drill 3: free Google Cloud voices + monthly cap (no network: fake API).
        print("\n=== Google Cloud voices + free-tier cap ===")
        drill = r"""
import base64, json, os, subprocess, sys, types
sys.path.insert(0, os.getcwd())
import podcast
from pathlib import Path
os.environ["GOOGLE_TTS_API_KEY"] = "k"
silent = Path("s.mp3")
subprocess.run(["ffmpeg","-y","-loglevel","error","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t","1","-q:a","9",str(silent)],check=True)
class R:
    status_code = 200
    def json(self): return {"audioContent": base64.b64encode(silent.read_bytes()).decode()}
calls = []
podcast.requests.post = lambda url, **kw: (calls.append(kw["json"]["voice"]["name"]), R())[1]
work = Path("cw"); work.mkdir(exist_ok=True)
turns = [("RAVI", "నమస్తే " * 50), ("LAKSHMI", "ఇది పరీక్ష. " * 900)]
parts = podcast.cloud_tts_podcast(turns, work, podcast.PROFILES["te"], "te")
used = json.loads(podcast.CLOUD_TTS_USAGE.read_text())
os.environ["GOOGLE_TTS_MONTHLY_CHARS"] = "10"
blocked = podcast.cloud_tts_podcast(turns, work, podcast.PROFILES["te"], "te")
print(json.dumps({"parts": len(parts or []), "voices": sorted(set(calls)), "used": sum(used.values()),
                  "blocked": blocked is None, "split": len(calls)}))
"""
        p = subprocess.run([sys.executable, "-c", drill], cwd=work, capture_output=True, text=True, timeout=120)
        try:
            r = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            print(p.stdout[-1500:], p.stderr[-1500:])
            r = {}
        check(r.get("parts") == 2, "Cloud voices: one audio file per line", errors)
        check(r.get("voices") == ["te-IN-Chirp3-HD-Charon", "te-IN-Chirp3-HD-Kore"],
              "Cloud voices: Telugu male + female Chirp 3 HD voices", errors)
        check((r.get("split") or 0) > 2, "Cloud voices: long lines split under the size limit", errors)
        check((r.get("used") or 0) > 0 and r.get("blocked") is True,
              "Cloud voices: usage counted, stops before the free limit (never charges)", errors)

        # Drill 4: lite model writes too short -> retry, then expand the draft; the editor pass
        # may not cut a script that is already short.
        print("\n=== short scripts get expanded, never cut ===")
        drill = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["WEATHER"] = "0"
import podcast
from pathlib import Path
def lines(n):
    return "\n".join(("KARTIK: " if i % 2 == 0 else "PRIYA: ") + "word " * 20 for i in range(n))
asked = []
def llm(system, user):
    if user.startswith("SCRIPT:"):
        asked.append("review")
        return lines(30)                       # editor tries to cut 60 lines -> 30
    if "YOUR DRAFT" in user:
        asked.append("expand")
        return lines(80)                       # 1600 words
    asked.append("write")
    return lines(30)                           # 600 words, far under target
got = []
class Stop(Exception): pass
def tts(turns, *a, **k):
    got.append(sum(len(x.split()) for _, x in turns)); raise Stop()
podcast.gemini_podcast = tts
try:
    podcast.make_podcast("brief", "d", Path("sx"), llm, length="About 1,500 to 2,300 words.")
except Stop:
    pass
print(json.dumps({"asked": asked, "words": got}))
"""
        p = subprocess.run([sys.executable, "-c", drill], cwd=work, capture_output=True, text=True, timeout=120)
        try:
            r = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            print(p.stdout[-1500:], p.stderr[-1500:])
            r = {}
        check(r.get("asked") == ["write", "write", "expand", "review"],
              f"short script: retried, then draft expanded ({r.get('asked')})", errors)
        check(r.get("words") == [1600], f"short script: editor pass did not cut it ({r.get('words')})", errors)
    print("\nRESULT:", "ALL CHECKS PASSED" if not errors else f"{len(errors)} FAILED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
