"""
Publish each episode to your own private-ish podcast feed, free, so it shows up in
Apple Podcasts / Pocket Casts / any podcast app automatically every morning.

Why a second repo: this repo is private, and podcast apps can't log in to GitHub.
So the MP3s and feed live in a small PUBLIC repo (only public news audio goes there):
  - MP3s   -> GitHub Release assets on that repo (tag "episodes"), oldest pruned
  - feed   -> feed.xml + episodes.json committed to that repo, served by GitHub Pages
The feed is marked <itunes:block>, so it never appears in podcast directories;
only someone with the URL can subscribe.

Needs (skips quietly if missing):
  PODCAST_REPO   e.g. Dheerajd9/world-brief-podcast   (repo variable)
  PODCAST_TOKEN  fine-grained token, that repo only, Contents: Read and write  (secret)
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import requests


def _redact(text: str) -> str:
    text = re.sub(r"bot\d+:[\w-]+", "bot***", text)
    text = re.sub(r"(apikey|key|token)=[^&\s]+", r"\1=***", text, flags=re.I)
    return re.sub(r"github_pat_\w+|gh[pousr]_\w+|AIza[\w-]{20,}", "***", text)

ROOT = Path(__file__).parent
API = "https://api.github.com"
TAG = "episodes"
KEEP = int(os.getenv("PODCAST_KEEP", "45"))   # ~2 weeks of morning+evening+weekly


def gh(method: str, url: str, token: str, **kw) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", **kw.pop("headers", {})}
    r = requests.request(method, url if url.startswith("http") else API + url,
                         headers=headers, timeout=120, **kw)
    if r.status_code >= 400 and r.status_code != 404:
        raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:200]}")
    return r


def get_file(repo: str, path: str, token: str) -> tuple[str | None, str | None]:
    r = gh("GET", f"/repos/{repo}/contents/{path}", token)
    if r.status_code == 404:
        return None, None
    j = r.json()
    return base64.b64decode(j["content"]).decode(), j["sha"]


def put_file(repo: str, path: str, data: bytes, token: str, msg: str) -> None:
    _, sha = get_file(repo, path, token)
    body = {"message": msg, "content": base64.b64encode(data).decode()}
    if sha:
        body["sha"] = sha
    gh("PUT", f"/repos/{repo}/contents/{path}", token, json=body)


def release(repo: str, token: str) -> dict:
    r = gh("GET", f"/repos/{repo}/releases/tags/{TAG}", token)
    if r.status_code == 404:
        r = gh("POST", f"/repos/{repo}/releases", token,
               json={"tag_name": TAG, "name": "Podcast episodes",
                     "body": "Audio files for the World Brief podcast feed."})
    return r.json()


def duration_seconds(mp3: Path) -> int:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(mp3)], capture_output=True, text=True)
        return int(float(out.stdout.strip()))
    except Exception:
        return 0


def make_cover(path: Path) -> bool:
    """Simple 1400x1400 cover art (podcast apps require a square image)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    img = Image.new("RGB", (1400, 1400), (18, 52, 86))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 980, 1400, 1400], fill=(255, 153, 51))          # saffron band
    d.ellipse([560, 140, 840, 420], outline=(255, 255, 255), width=14)      # globe
    d.ellipse([640, 140, 760, 420], outline=(255, 255, 255), width=8)
    d.line([566, 280, 834, 280], fill=(255, 255, 255), width=8)
    fonts = "/usr/share/fonts/truetype/dejavu/"
    try:
        big = ImageFont.truetype(fonts + "DejaVuSans-Bold.ttf", 150)
        small = ImageFont.truetype(fonts + "DejaVuSans.ttf", 70)
    except OSError:
        big = small = ImageFont.load_default(size=120)
    d.text((700, 600), "WORLD", font=big, fill="white", anchor="mm")
    d.text((700, 780), "BRIEF", font=big, fill="white", anchor="mm")
    d.text((700, 1190), "India · USA · World", font=small, fill=(18, 52, 86), anchor="mm")
    img.save(path, "PNG")
    return True


def build_feed(repo: str, episodes: list[dict]) -> str:
    owner, name = repo.split("/")
    site = f"https://{owner.lower()}.github.io/{name}"
    items = []
    for e in episodes:
        items.append(f"""    <item>
      <title>{escape(e['title'])}</title>
      <description>{escape(e['description'])}</description>
      <enclosure url="{escape(e['url'])}" length="{e['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{escape(e['id'])}</guid>
      <pubDate>{e['pubDate']}</pubDate>
      <itunes:duration>{e['duration']}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>World Brief</title>
    <link>{site}</link>
    <description>Daily India-first news podcast: India, USA and the world, with Kartik and Priya.</description>
    <language>en-in</language>
    <itunes:author>World Brief bot</itunes:author>
    <itunes:image href="{site}/cover.png"/>
    <itunes:category text="News"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:block>Yes</itunes:block>
{chr(10).join(items)}
  </channel>
</rss>
"""


def configured() -> tuple[str, str] | None:
    repo, token = os.getenv("PODCAST_REPO", "").strip(), os.getenv("PODCAST_TOKEN", "").strip()
    if repo and token and re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return repo, token
    return None


def upload_asset(path: Path, asset_name: str) -> str:
    """Upload (or replace) one MP3 on the 'episodes' release; return its public URL."""
    repo, token = configured()
    rel = release(repo, token)
    for a in rel.get("assets", []):
        if a["name"] == asset_name:
            gh("DELETE", f"/repos/{repo}/releases/assets/{a['id']}", token)
    upload_url = rel["upload_url"].split("{")[0]
    asset = gh("POST", upload_url, token, params={"name": asset_name},
               headers={"Content-Type": "audio/mpeg"}, data=path.read_bytes()).json()
    return asset["browser_download_url"]


def prune_extra(suffix: str, keep: int = 21) -> None:
    """Keep only the newest `keep` assets ending in `suffix` (e.g. Telugu episodes)."""
    repo, token = configured()
    assets = sorted((a for a in release(repo, token).get("assets", [])
                     if a["name"].endswith(suffix)), key=lambda a: a["name"], reverse=True)
    for a in assets[keep:]:
        gh("DELETE", f"/repos/{repo}/releases/assets/{a['id']}", token)


def publish_episode(meta: dict) -> str:
    """Upload the English episode, update feed.xml for podcast apps; return the MP3 URL."""
    repo, token = configured()
    mp3 = Path(meta["file"])
    stamp = datetime.fromisoformat(meta["date"])
    asset_name = f"{stamp:%Y-%m-%d}-{meta['edition']}.mp3"
    url = upload_asset(mp3, asset_name)

    text, _ = get_file(repo, "episodes.json", token)
    episodes = json.loads(text) if text else []
    episodes = [e for e in episodes if e["id"] != asset_name]
    episodes.insert(0, {
        "id": asset_name, "title": meta["title"], "description": meta["description"],
        "url": url, "bytes": mp3.stat().st_size,
        "pubDate": format_datetime(stamp), "duration": duration_seconds(mp3)})
    keep, drop = episodes[:KEEP], {e["id"] for e in episodes[KEEP:]}
    if drop:
        for a in release(repo, token).get("assets", []):
            if a["name"] in drop:
                gh("DELETE", f"/repos/{repo}/releases/assets/{a['id']}", token)
    if get_file(repo, "cover.png", token)[1] is None:
        cover = ROOT / "out" / "cover.png"
        if make_cover(cover):
            put_file(repo, "cover.png", cover.read_bytes(), token, "Add cover art")
    put_file(repo, "episodes.json", json.dumps(keep, indent=1, ensure_ascii=False).encode(),
             token, f"Episode {asset_name}")
    put_file(repo, "feed.xml", build_feed(repo, keep).encode(), token, f"Feed {asset_name}")
    owner, name = repo.split("/")
    print(f"  podcast feed updated: https://{owner.lower()}.github.io/{name}/feed.xml")
    return url


def main() -> None:
    """CLI: publish out/episode.json (normally digest.py calls publish_episode itself)."""
    meta_path = ROOT / "out" / "episode.json"
    if not configured():
        print("Podcast feed: PODCAST_REPO / PODCAST_TOKEN not set, skipping.")
        return
    if not meta_path.exists():
        print("Podcast feed: no episode today, skipping.")
        return
    publish_episode(json.loads(meta_path.read_text()))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:        # feed problems must never fail the daily run
        print(f"! podcast feed failed: {type(exc).__name__}: {_redact(str(exc))[:200]}", file=sys.stderr)
