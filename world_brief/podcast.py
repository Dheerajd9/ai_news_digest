"""
Turn the written brief into a two-host audio podcast, free.

Voices, best first:
  A. Gemini multi-speaker TTS (free tier, your GEMINI_API_KEY): both anchors in one
     natural-sounding conversation, directed to speak with Indian English accents.
  B. edge-tts fallback (Microsoft Indian English neural voices), line by line.
Set PODCAST_TTS=edge (English) or TELUGU_TTS=edge (Telugu) to always use B.

1. A free LLM (same Gemini -> GitHub Models chain as the brief) rewrites the brief
   as a natural conversation between KARTIK and PRIYA (prompts/podcast_prompt.md).
2. edge-tts (Microsoft Edge's neural voices, no API key) speaks each line with
   Indian English voices. Change voices with PODCAST_VOICE_A / PODCAST_VOICE_B
   (list all: `edge-tts --list-voices | grep en-IN`).
3. ffmpeg joins the lines with short natural pauses into one small MP3.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

# Voice profiles share one engine: English (Kartik & Priya), Telugu (Ravi & Lakshmi),
# Hindi (Arjun & Meera).
PROFILES = {
    "en": {
        "hosts": ("KARTIK", "PRIYA"),
        "prompt": "podcast_prompt.md",
        "file": "world_brief_podcast.mp3",
        "edge": {"KARTIK": os.getenv("PODCAST_VOICE_A", "en-IN-PrabhatNeural"),
                 "PRIYA": os.getenv("PODCAST_VOICE_B", "en-IN-NeerjaExpressiveNeural")},
        "edge_fallback": {"PRIYA": "en-IN-NeerjaNeural"},
        "edge_rate": os.getenv("PODCAST_RATE", "+4%"),
        "gemini": {"KARTIK": "Charon", "PRIYA": "Sulafat"},
        "gemini_pinned": {"KARTIK": os.getenv("GEMINI_VOICE_A", ""),
                          "PRIYA": os.getenv("GEMINI_VOICE_B", "")},
        "lang_code": "en-IN",
        "turn_style": "Warm, friendly, natural conversation, relaxed pace",
        "style": ("Read this aloud as a lively, friendly Indian English news discussion show. "
                  "Kartik and Priya are two Indian friends from Hyderabad hosting the show. Both "
                  "speak with a natural Indian English accent, warm and expressive, at a relaxed "
                  "conversational pace, reacting to each other like real people, never robotic:"),
    },
    "te": {
        "hosts": ("RAVI", "LAKSHMI"),
        "prompt": "telugu_podcast_prompt.md",
        "file": "telugu_podcast.mp3",
        "edge": {"RAVI": os.getenv("TELUGU_VOICE_A", "te-IN-MohanNeural"),
                 "LAKSHMI": os.getenv("TELUGU_VOICE_B", "te-IN-ShrutiNeural")},
        "edge_fallback": {},
        "edge_rate": os.getenv("TELUGU_RATE", "-4%"),     # a little slower for elders
        "gemini": {"RAVI": "Charon", "LAKSHMI": "Sulafat"},
        "gemini_pinned": {"RAVI": os.getenv("GEMINI_TELUGU_VOICE_A", ""),
                          "LAKSHMI": os.getenv("GEMINI_TELUGU_VOICE_B", "")},
        "lang_code": "te-IN",
        "tts_budget": int(os.getenv("GEMINI_TELUGU_TTS_BUDGET_SEC", "1000")),   # full-length episode
        "turn_style": "Natural spoken Telugu as people talk in Hyderabad, warm and clear, calm conversational pace",
        "style": ("Read this aloud in natural, warm spoken Telugu, like a friendly Telugu news "
                  "discussion on a Hyderabad TV channel. Ravi and Lakshmi are two Telugu news hosts "
                  "from Hyderabad. Speak clearly at a calm, unhurried pace with "
                  "a natural Telangana (Hyderabad) accent and Telangana yaasa expressions, warm and "
                  "homely like family talking, never robotic or bookish:"),
    },
    "hi": {
        "hosts": ("ARJUN", "MEERA"),
        "prompt": "hindi_podcast_prompt.md",
        "file": "hindi_podcast.mp3",
        "edge": {"ARJUN": os.getenv("HINDI_VOICE_A", "hi-IN-MadhurNeural"),
                 "MEERA": os.getenv("HINDI_VOICE_B", "hi-IN-SwaraNeural")},
        "edge_fallback": {},
        "edge_rate": os.getenv("HINDI_RATE", "+0%"),
        "gemini": {"ARJUN": "Charon", "MEERA": "Sulafat"},
        "gemini_pinned": {"ARJUN": os.getenv("GEMINI_HINDI_VOICE_A", ""),
                          "MEERA": os.getenv("GEMINI_HINDI_VOICE_B", "")},
        "lang_code": "hi-IN",
        "tts_budget": int(os.getenv("GEMINI_HINDI_TTS_BUDGET_SEC", "1000")),
        "turn_style": "Natural everyday spoken Hindi, warm and clear, relaxed conversational pace",
        "style": ("Read this aloud in natural, warm everyday spoken Hindi, like a friendly Hindi "
                  "news discussion. Arjun and Meera are two Indian news hosts. Speak clearly at a "
                  "relaxed pace, warm and expressive, never robotic or bookish:"),
    },
}


def _redact(text: str) -> str:
    text = re.sub(r"bot\d+:[\w-]+", "bot***", text)
    text = re.sub(r"(apikey|key|token)=[^&\s]+", r"\1=***", text, flags=re.I)
    return re.sub(r"github_pat_\w+|gh[pousr]_\w+|AIza[\w-]{20,}", "***", text)

ROOT = Path(__file__).parent


def clean_brief_for_script(brief_md: str) -> str:
    text = re.sub(r"\[S\d+\]", "", brief_md)           # source ids
    text = re.sub(r"\[\[([^\]]+)\]\]\([^)]+\)", "", text)  # linked sources
    return re.sub(r"[ \t]+\n", "\n", text)


def parse_script(script: str, hosts: tuple[str, str] = ("KARTIK", "PRIYA")) -> list[tuple[str, str]]:
    turns = []
    names = "|".join(hosts)
    for line in script.splitlines():
        m = re.match(r"\s*\**\s*(" + names + r")\s*\**\s*:\s*(.+)", line, re.I)
        if m:
            speaker, said = m.group(1).upper(), m.group(2).strip()
            said = re.sub(r"\[S\d+\]", "", said)            # source ids
            said = re.sub(r"[*_#`\[\]]", "", said)       # stray markdown
            said = re.sub(r"\([^)]*\)", "", said).strip()  # stage directions
            if said:
                turns.append((speaker, said))
    return turns


CHAPTER_NAMES = ["Top stories", "India", "USA", "World", "Money", "Wrap-up"]
CHAPTER_RULES = """

## CHAPTERS (for the listener's skip buttons)
Arrange the conversation into these chapters, in this order, skipping any chapter with nothing
meaningful today: Top stories (the 1-3 most important stories of the day, any country), India,
USA (includes India-USA, visas and immigration), World, Money (markets, economy, gold, rupee,
jobs, tax), Wrap-up (what to watch, the three takeaways, the ending). Before the first line of
each chapter write ONE marker line exactly like `CHAPTER: India` (marker names always in English,
never translated). Each story appears once, in its best chapter. Keep natural spoken transitions
between chapters; never say the word "chapter" aloud.
"""


def _chapter_name(raw: str) -> str | None:
    r = raw.strip().strip("*`#[]: ").lower()
    for key, name in (("top", "Top stories"), ("most", "Top stories"), ("headline", "Top stories"),
                      ("india", "India"), ("usa", "USA"), ("america", "USA"), ("u.s", "USA"),
                      ("world", "World"), ("global", "World"), ("money", "Money"),
                      ("market", "Money"), ("econom", "Money"), ("wrap", "Wrap-up"),
                      ("takeaway", "Wrap-up"), ("watch", "Wrap-up")):
        if key in r:
            return name
    return None


def parse_chapters(script: str, hosts: tuple[str, str]) -> list[tuple[int, str]]:
    """(index of the first turn, chapter name) for each CHAPTER: marker line."""
    out, n = [], 0
    names = "|".join(hosts)
    for line in script.splitlines():
        m = re.match(r"\s*[*#`\[]*\s*CHAPTER\s*[:\-]\s*(.+)", line, re.I)
        if m:
            name = _chapter_name(m.group(1))
            if name and (not out or out[-1][1] != name):
                if out and out[-1][0] == n:          # empty chapter: replace it
                    out[-1] = (n, name)
                else:
                    out.append((n, name))
            continue
        hm = re.match(r"\s*\**\s*(" + names + r")\s*\**\s*:\s*(.+)", line, re.I)
        if hm and parse_script(line, hosts):
            n += 1
    return out


def _duration(path: Path) -> float:
    try:
        return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                     "-of", "csv=p=0", str(path)], capture_output=True,
                                    text=True, timeout=30).stdout.strip() or 0)
    except Exception:
        return 0.0


def chapter_times(chapters, turns, parts, groups, pause: float = 0.35) -> list[dict]:
    """Start time (seconds) of each chapter. `groups` = the turns inside each audio part
    (stitch() adds a short pause after every part); inside a multi-turn part, time is
    shared out by text length."""
    starts, t = [], 0.0
    for part, group in zip(parts, groups):
        d = _duration(part)
        total = sum(len(x[1]) for x in group) or 1
        for turn in group:
            starts.append(t)
            t += d * len(turn[1]) / total
        t += pause
    return [{"t": round(starts[i], 1), "title": name} for i, name in chapters if i < len(starts)]


async def _speak(text: str, voice: str, out: Path, rate: str) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def speak_line(text: str, speaker: str, out: Path, profile: dict | None = None) -> None:
    profile = profile or PROFILES["en"]
    voice = profile["edge"][speaker]
    for attempt in range(3):
        try:
            asyncio.run(_speak(text, voice, out, profile["edge_rate"]))
            if out.exists() and out.stat().st_size > 0:
                return
        except Exception as exc:
            print(f"  ! tts retry {attempt + 1} ({voice}): {type(exc).__name__}")
            voice = profile["edge_fallback"].get(speaker, voice)   # missing voice -> plain one
    raise RuntimeError(f"TTS failed for line: {text[:60]}")


def stitch(parts: list[Path], out_mp3: Path, workdir: Path) -> None:
    if not shutil.which("ffmpeg"):
        # Same-format MP3 frames can simply be concatenated; no pauses but it plays.
        out_mp3.write_bytes(b"".join(p.read_bytes() for p in parts))
        return
    pause = workdir / "pause.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", "0.35", "-q:a", "9", str(pause)],
                   check=True)
    listing = workdir / "list.txt"
    with listing.open("w") as f:
        for p in parts:
            f.write(f"file '{p.resolve()}'\nfile '{pause.resolve()}'\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-ac", "1", "-ar", "24000", "-b:a", "64k",
                    str(out_mp3)], check=True)


# ---------------------------------------------------------------- Gemini TTS
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
CHUNK_CHARS = 3200    # ~3 minutes of audio per request


class QuotaExhausted(Exception):
    pass


TTS_BUDGET_SEC = int(os.getenv("GEMINI_TTS_BUDGET_SEC", "480"))   # then fall back to edge-tts
_TTS_DAY_LIMIT: set[str] = set()   # TTS models whose daily free quota is used up
_GEMINI_TTS_OFF = False   # set once the daily quota is gone, so later episodes skip straight to backup


def gemini_tts_models(key: str) -> list[str]:
    try:
        r = requests.get(f"{GEMINI_API}/models", headers={"x-goog-api-key": key},
                         params={"pageSize": 200}, timeout=20)
        r.raise_for_status()
        names = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                 if "tts" in m["name"] and "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception:
        names = []
    pref = os.getenv("GEMINI_TTS_MODEL", "").strip()
    names.sort(key=lambda n: (-float((re.findall(r"\d+(?:\.\d+)?", n) or ["0"])[0]),
                              "pro" in n, "lite" in n, "preview" in n))
    return list(dict.fromkeys(([pref] if pref else []) + names))[:3]


def chunk_turns(turns: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    chunks, cur, size = [], [], 0
    for t in turns:
        if cur and size + len(t[1]) > CHUNK_CHARS:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t[1])
    if cur:
        chunks.append(cur)
    return chunks


def discover_voices(key: str, language: str) -> list[dict]:
    """Voices Google offers for a language (Extended Voice Library). Logged so the best ones
    can be pinned with GEMINI_TELUGU_VOICE_A / _B. Empty list if the endpoint isn't available."""
    try:
        r = requests.get(f"{GEMINI_API}/voices", headers={"x-goog-api-key": key},
                         params={"language_code": language, "page_size": 50}, timeout=20)
        if r.status_code >= 400:
            return []
        voices = [v for v in r.json().get("voices", []) if isinstance(v, dict)]
        if voices:
            print(f"  voice library fields: {sorted(voices[0].keys())}")
        print(f"  voice library for {language}: " + "; ".join(
            f"{voice_name(v) or '?'} ({v.get('gender', '?')}, {v.get('accent', '') or v.get('regionCode', '')}, "
            f"{v.get('type', '')})" for v in voices[:20]))
        return voices
    except Exception:
        return []


def voice_name(v: dict) -> str:
    """The library's field name for a voice id has changed between API versions: try them all."""
    for k in ("voiceName", "voice_name", "name", "voiceId", "voice_id", "id", "displayName"):
        val = v.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip().split("/")[-1]
    return ""


def voice_sets(key: str, profile: dict) -> list[dict]:
    """Voice pairs to try, best first: pinned env voices, library voices for the language,
    then the default prebuilt pair."""
    a, b = profile["hosts"]
    sets = [profile["gemini"]]
    if profile.get("lang_code"):
        lib = discover_voices(key, profile["lang_code"])
        male = [n for v in lib if str(v.get("gender", "")).upper().startswith("M") and (n := voice_name(v))]
        female = [n for v in lib if str(v.get("gender", "")).upper().startswith("F") and (n := voice_name(v))]
        if male and female:
            sets.insert(0, {a: male[0], b: female[0]})
    pinned = profile.get("gemini_pinned") or {}
    if all(pinned.get(h) for h in (a, b)):
        sets.insert(0, pinned)
    out = []
    for vs in sets:
        if vs not in out and all(vs.get(h) for h in (a, b)):     # never send an empty voice name
            out.append(vs)
    return out


def gemini_speak_chunk(turns, key: str, model: str, out_mp3: Path, work: Path,
                       profile: dict, voices: dict) -> None:
    """One request = one chunk of the dialogue; each turn is its own part with
    speech_metadata.speaker (current Gemini TTS multi-speaker format)."""
    parts = [{"text": t, "speech_metadata": {"speaker": s.title(),
                                             **({"style": profile["turn_style"]}
                                                if profile.get("turn_style") else {})}}
             for s, t in turns]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "multiSpeakerVoiceConfig": {"speakerVoiceConfigs": [
                    {"speaker": sp.title(), "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v}}}
                    for sp, v in voices.items()]}}}}
    for attempt in range(4):
        r = requests.post(f"{GEMINI_API}/models/{model}:generateContent",
                          headers={"x-goog-api-key": key}, json=body, timeout=240)
        if r.status_code == 429 and ("PerDay" in r.text or "per day" in r.text.lower()):
            raise QuotaExhausted(f"Gemini TTS {model} daily free quota used up")
        if r.status_code == 429 or r.status_code >= 500:     # per-minute limit or busy: wait
            if attempt < 3:
                time.sleep(25 * (attempt + 1))
                continue
            raise RuntimeError(f"Gemini TTS busy (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        audio = [p["inlineData"] for p in r.json()["candidates"][0]["content"]["parts"]
                 if "inlineData" in p]
        if not audio:
            raise RuntimeError("no audio in response")
        mime = audio[0].get("mimeType", "")
        raw = b"".join(base64.b64decode(p["data"]) for p in audio)
        if "wav" in mime.lower() or raw[:4] == b"RIFF":
            src = work / (out_mp3.stem + ".wav")
            src.write_bytes(raw)
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
        else:                                           # raw 16-bit PCM
            rate = re.search(r"rate=(\d+)", mime)
            src = work / (out_mp3.stem + ".pcm")
            src.write_bytes(raw)
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le",
                   "-ar", rate.group(1) if rate else "24000", "-ac", "1", "-i", str(src)]
        subprocess.run(cmd + ["-ac", "1", "-ar", "24000", "-b:a", "64k", str(out_mp3)], check=True)
        words = sum(len(t.split()) for _, t in turns)
        secs = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                     "-of", "csv=p=0", str(out_mp3)],
                                    capture_output=True, text=True).stdout.strip() or 0)
        if secs < words / 4.5:          # far too short = cut-off audio
            raise RuntimeError(f"audio too short ({secs:.0f}s for {words} words)")
        return
    raise RuntimeError(f"Gemini TTS busy (HTTP {r.status_code})")


def gemini_podcast(turns, work: Path, profile: dict, tts_pref: str) -> list[Path] | None:
    global _GEMINI_TTS_OFF
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key or tts_pref == "edge" or _GEMINI_TTS_OFF or not shutil.which("ffmpeg"):
        return None
    start = time.time()
    candidates = voice_sets(key, profile)
    models = [m for m in gemini_tts_models(key)[:2] if m not in _TTS_DAY_LIMIT]
    for model in models:
        for voices in candidates:
            try:
                parts = []
                for n, chunk in enumerate(chunk_turns(turns)):
                    if time.time() - start > profile.get("tts_budget", TTS_BUDGET_SEC):
                        raise RuntimeError("Gemini TTS too slow today; switching to backup voices")
                    p = work / f"g{n:02d}.mp3"
                    gemini_speak_chunk(chunk, key, model, p, work, profile, voices)
                    parts.append(p)
                print(f"  voices: Gemini {model} {voices} ({len(parts)} part(s))")
                return parts
            except QuotaExhausted as exc:
                print(f"  ! {exc}")
                _TTS_DAY_LIMIT.add(model)
                break                                   # next model has its own free quota
            except Exception as exc:
                print(f"  ! Gemini TTS {model} {voices} failed: {_redact(str(exc))[:300]}")
                if "too slow" in str(exc):
                    return None
    if models and all(m in _TTS_DAY_LIMIT for m in models):
        print("  Gemini TTS: all free quota used today; using backup voices")
        _GEMINI_TTS_OFF = True
    return None


def review_script(script: str, llm, length: str, hosts: tuple[str, str],
                  min_ratio: float = 0.5) -> str:
    """Quality gate: an editor pass that rewrites a weak draft before it reaches the voices.
    Keeps the draft if the rewrite looks broken."""
    system = (ROOT / "prompts" / "podcast_review_prompt.md").read_text().replace("{LENGTH}", length)
    try:
        revised = llm(system, f"SCRIPT:\n{script}")
    except Exception:
        return script
    before, after = parse_script(script, hosts), parse_script(revised or "", hosts)
    words = lambda turns: sum(len(t.split()) for _, t in turns)
    if len(after) >= 6 and words(after) >= min_ratio * words(before):
        print(f"  script reviewed: {words(before)} -> {words(after)} words")
        return revised
    print("  ! script review unusable; keeping the first draft")
    return script


def latest_previous_script(archive: Path, kind: str, max_chars: int = 4000) -> str:
    """Most recent earlier script of this kind (for 'Yesterday we talked about...')."""
    if not archive.exists():
        return ""
    files = sorted(archive.glob(f"*-{kind}.txt"))
    return files[-1].read_text()[-max_chars:] if files else ""


def make_podcast(brief_md: str, date_str: str, out_dir: Path, llm,
                 length: str = "About 1,500 to 2,300 words (10 to 15 minutes spoken).",
                 show: str = "The Morning Brief", lang: str = "en",
                 prompt_file: str | None = None, out_name: str | None = None,
                 report_type: str = "MORNING", previous_script: str = "") -> Path | None:
    profile = PROFILES[lang]
    system = ((ROOT / "prompts" / (prompt_file or profile["prompt"])).read_text()
              .replace("{LENGTH}", length).replace("{SHOW}", show)
              + (ROOT / "prompts" / "intelligence_layer_short.md").read_text()
              + CHAPTER_RULES)
    user = f"REPORT_TYPE: {report_type}\nDATE: {date_str}\n\nBRIEF:\n{clean_brief_for_script(brief_md)}"
    if previous_script:
        user += f"\n\nPREVIOUS_PODCAST (what the listener already heard):\n{previous_script}"
    script = llm(system, user)
    # Length guard: weaker/backup models often write far too short. Ask once more.
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]{2,}", length)]
    target = nums[0] if nums and "at most" not in length.lower() else 0
    got = sum(len(t.split()) for _, t in parse_script(script or "", profile["hosts"]))
    if script and target and got < 0.7 * target:
        print(f"  script too short ({got} words, want {target}+); asking for the full version")
        longer = llm(system, user + f"\n\nIMPORTANT: your previous draft had only {got} words. "
                     f"Write the FULL episode now: at least {target} words, covering every "
                     f"important story in the brief with real explanation. Same rules and format.")
        if sum(len(t.split()) for _, t in parse_script(longer or "", profile["hosts"])) > got:
            script = longer
    if script and os.getenv("PODCAST_REVIEW", "1") == "1":
        script = review_script(script, llm, length, profile["hosts"],
                               min_ratio=0.85 if lang in ("te", "hi") else 0.5)
    turns = parse_script(script or "", profile["hosts"])
    chapters = parse_chapters(script or "", profile["hosts"])
    if len(turns) < 6:
        print(f"  ! {lang} podcast script too short or missing; skipping audio")
        return None
    print(f"  {lang} podcast script: {len(turns)} turns, {sum(len(t.split()) for _, t in turns)} words")

    tag = (out_name or profile["file"]).rsplit(".", 1)[0]
    work = out_dir / f"parts_{tag}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    tts_pref = os.getenv({"te": "TELUGU_TTS", "hi": "HINDI_TTS"}.get(lang, "PODCAST_TTS"), "gemini")
    parts = gemini_podcast(turns, work, profile, tts_pref)
    groups = chunk_turns(turns) if parts else []
    if not parts:
        print(f"  voices: edge-tts ({lang})")
        parts = []
        for n, (speaker, said) in enumerate(turns):
            p = work / f"{n:03d}.mp3"
            speak_line(said, speaker, p, profile)
            parts.append(p)
        groups = [[t] for t in turns]

    out_mp3 = out_dir / (out_name or profile["file"])
    stitch(parts, out_mp3, work)
    (out_dir / f"script_{tag}.txt").write_text(
        "\n".join(f"{s}: {t}" for s, t in turns))
    chap_file = out_dir / f"chapters_{tag}.json"
    chap_file.unlink(missing_ok=True)
    if chapters and len(groups) == len(parts):
        try:
            times = chapter_times(chapters, turns, parts, groups)
            if len(times) >= 2:
                chap_file.write_text(json.dumps(times, ensure_ascii=False))
                print(f"  chapters: " + ", ".join(f"{c['title']} {int(c['t']) // 60}:{int(c['t']) % 60:02d}"
                                                  for c in times))
        except Exception as exc:
            print(f"  ! chapters skipped: {type(exc).__name__}")
    print(f"  {tag} podcast ready: {out_mp3.stat().st_size / 1e6:.1f} MB")
    return out_mp3
