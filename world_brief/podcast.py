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
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

# Two voice profiles share one engine: English (Kartik & Priya) and Telugu (Ravi & Lakshmi).
PROFILES = {
    "en": {
        "hosts": ("KARTIK", "PRIYA"),
        "prompt": "podcast_prompt.md",
        "file": "world_brief_podcast.mp3",
        "edge": {"KARTIK": os.getenv("PODCAST_VOICE_A", "en-IN-PrabhatNeural"),
                 "PRIYA": os.getenv("PODCAST_VOICE_B", "en-IN-NeerjaExpressiveNeural")},
        "edge_fallback": {"PRIYA": "en-IN-NeerjaNeural"},
        "edge_rate": os.getenv("PODCAST_RATE", "+4%"),
        "gemini": {"KARTIK": os.getenv("GEMINI_VOICE_A", "Charon"),
                   "PRIYA": os.getenv("GEMINI_VOICE_B", "Sulafat")},
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
        "gemini": {"RAVI": os.getenv("GEMINI_TELUGU_VOICE_A", "Charon"),
                   "LAKSHMI": os.getenv("GEMINI_TELUGU_VOICE_B", "Sulafat")},
        "style": ("Read this aloud in natural, warm spoken Telugu, like a friendly Telugu news "
                  "discussion on a Hyderabad TV channel. Ravi and Lakshmi are two Telugu hosts from "
                  "Hyderabad talking to elderly parents. Speak clearly at a calm, unhurried pace with "
                  "a natural Telangana (Hyderabad) accent and Telangana yaasa expressions, warm and "
                  "homely like family talking, never robotic or bookish:"),
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


def gemini_speak_chunk(turns, key: str, model: str, out_mp3: Path, work: Path,
                       profile: dict) -> None:
    text = profile["style"] + "\n\n" + "\n".join(f"{s.title()}: {t}" for s, t in turns)
    body = {"contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "multiSpeakerVoiceConfig": {"speakerVoiceConfigs": [
                    {"speaker": sp.title(), "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v}}}
                    for sp, v in profile["gemini"].items()]}}}}
    for attempt in range(3):
        r = requests.post(f"{GEMINI_API}/models/{model}:generateContent",
                          headers={"x-goog-api-key": key}, json=body, timeout=300)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(40 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
        part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
        rate = re.search(r"rate=(\d+)", part.get("mimeType", "")) 
        pcm = work / (out_mp3.stem + ".pcm")
        pcm.write_bytes(base64.b64decode(part["data"]))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le",
                        "-ar", rate.group(1) if rate else "24000", "-ac", "1", "-i", str(pcm),
                        "-b:a", "64k", str(out_mp3)], check=True)
        words = sum(len(t.split()) for _, t in turns)
        secs = pcm.stat().st_size / (2 * int(rate.group(1) if rate else 24000))
        if secs < words / 4.5:          # far too short = cut-off audio
            raise RuntimeError(f"audio too short ({secs:.0f}s for {words} words)")
        return
    raise RuntimeError(f"Gemini TTS busy (HTTP {r.status_code})")


def gemini_podcast(turns, work: Path, profile: dict, tts_pref: str) -> list[Path] | None:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key or tts_pref == "edge" or not shutil.which("ffmpeg"):
        return None
    for model in gemini_tts_models(key):
        try:
            parts = []
            for n, chunk in enumerate(chunk_turns(turns)):
                p = work / f"g{n:02d}.mp3"
                gemini_speak_chunk(chunk, key, model, p, work, profile)
                parts.append(p)
            print(f"  voices: Gemini {model} ({len(parts)} part(s))")
            return parts
        except Exception as exc:
            print(f"  ! Gemini TTS {model} failed: {_redact(str(exc))[:200]}")
    return None


def make_podcast(brief_md: str, date_str: str, out_dir: Path, llm,
                 length: str = "About 1,500 to 2,300 words (10 to 15 minutes spoken).",
                 show: str = "The Morning Brief", lang: str = "en") -> Path | None:
    profile = PROFILES[lang]
    system = ((ROOT / "prompts" / profile["prompt"]).read_text()
              .replace("{LENGTH}", length).replace("{SHOW}", show))
    script = llm(system, f"DATE: {date_str}\n\nTODAY'S BRIEF:\n{clean_brief_for_script(brief_md)}")
    turns = parse_script(script or "", profile["hosts"])
    if len(turns) < 6:
        print(f"  ! {lang} podcast script too short or missing; skipping audio")
        return None
    print(f"  {lang} podcast script: {len(turns)} turns, {sum(len(t.split()) for _, t in turns)} words")

    work = out_dir / f"podcast_parts_{lang}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    tts_pref = os.getenv("TELUGU_TTS" if lang == "te" else "PODCAST_TTS", "gemini")
    parts = gemini_podcast(turns, work, profile, tts_pref)
    if not parts:
        print(f"  voices: edge-tts ({lang})")
        parts = []
        for n, (speaker, said) in enumerate(turns):
            p = work / f"{n:03d}.mp3"
            speak_line(said, speaker, p, profile)
            parts.append(p)

    out_mp3 = out_dir / profile["file"]
    stitch(parts, out_mp3, work)
    (out_dir / f"podcast_script_{lang}.txt").write_text(
        "\n".join(f"{s}: {t}" for s, t in turns))
    print(f"  {lang} podcast ready: {out_mp3.stat().st_size / 1e6:.1f} MB")
    return out_mp3
