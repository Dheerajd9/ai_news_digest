"""
Turn the written brief into a two-host audio podcast, free.

1. A free LLM (same Gemini -> GitHub Models chain as the brief) rewrites the brief
   as a natural conversation between ARJUN and MEERA (prompts/podcast_prompt.md).
2. edge-tts (Microsoft Edge's neural voices, no API key) speaks each line with
   Indian English voices. Change voices with PODCAST_VOICE_A / PODCAST_VOICE_B
   (list all: `edge-tts --list-voices | grep en-IN`).
3. ffmpeg joins the lines with short natural pauses into one small MP3.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

VOICES = {
    "ARJUN": os.getenv("PODCAST_VOICE_A", "en-IN-PrabhatNeural"),
    "MEERA": os.getenv("PODCAST_VOICE_B", "en-IN-NeerjaExpressiveNeural"),
}
FALLBACK_VOICE = {"MEERA": "en-IN-NeerjaNeural"}
RATE = os.getenv("PODCAST_RATE", "+4%")   # a touch faster than default sounds more natural

ROOT = Path(__file__).parent


def clean_brief_for_script(brief_md: str) -> str:
    text = re.sub(r"\[S\d+\]", "", brief_md)           # source ids
    text = re.sub(r"\[\[([^\]]+)\]\]\([^)]+\)", "", text)  # linked sources
    return re.sub(r"[ \t]+\n", "\n", text)


def parse_script(script: str) -> list[tuple[str, str]]:
    turns = []
    for line in script.splitlines():
        m = re.match(r"\s*\**\s*(ARJUN|MEERA)\s*\**\s*:\s*(.+)", line, re.I)
        if m:
            speaker, said = m.group(1).upper(), m.group(2).strip()
            said = re.sub(r"\[S\d+\]", "", said)            # source ids
            said = re.sub(r"[*_#`\[\]]", "", said)       # stray markdown
            said = re.sub(r"\([^)]*\)", "", said).strip()  # stage directions
            if said:
                turns.append((speaker, said))
    return turns


async def _speak(text: str, voice: str, out: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=RATE).save(str(out))


def speak_line(text: str, speaker: str, out: Path) -> None:
    voice = VOICES[speaker]
    for attempt in range(3):
        try:
            asyncio.run(_speak(text, voice, out))
            if out.exists() and out.stat().st_size > 0:
                return
        except Exception as exc:
            print(f"  ! tts retry {attempt + 1} ({voice}): {exc}")
            voice = FALLBACK_VOICE.get(speaker, voice)   # expressive voice missing -> plain one
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


def make_podcast(brief_md: str, date_str: str, out_dir: Path, llm) -> Path | None:
    system = (ROOT / "prompts" / "podcast_prompt.md").read_text()
    script = llm(system, f"DATE: {date_str}\n\nTODAY'S BRIEF:\n{clean_brief_for_script(brief_md)}")
    turns = parse_script(script or "")
    if len(turns) < 6:
        print("  ! podcast script too short or missing; skipping audio")
        return None
    print(f"  podcast script: {len(turns)} turns, {sum(len(t.split()) for _, t in turns)} words")

    work = out_dir / "podcast_parts"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    parts = []
    for n, (speaker, said) in enumerate(turns):
        p = work / f"{n:03d}.mp3"
        speak_line(said, speaker, p)
        parts.append(p)

    out_mp3 = out_dir / "world_brief_podcast.mp3"
    stitch(parts, out_mp3, work)
    (out_dir / "podcast_script.txt").write_text(
        "\n".join(f"{s}: {t}" for s, t in turns))
    print(f"  podcast ready: {out_mp3.stat().st_size / 1e6:.1f} MB")
    return out_mp3
