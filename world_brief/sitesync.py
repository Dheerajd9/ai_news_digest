"""Copy the last few days of podcast MP3s from the GitHub release onto the player site.
Triggered by a run_request.txt line "run: sitesync" (one-off backfill / repair)."""

from __future__ import annotations

import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import podfeed


def main() -> None:
    cfg = podfeed.configured()
    if not cfg:
        print("podcast repo not configured")
        return
    repo, token = cfg
    since = (datetime.now(timezone.utc) - timedelta(days=podfeed.SITE_KEEP_DAYS)).strftime("%Y-%m-%d")
    assets = [a for a in podfeed.release(repo, token).get("assets", [])
              if re.match(r"\d{4}-\d{2}-\d{2}-.*\.mp3$", a["name"]) and a["name"][:10] >= since]
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for a in assets:
            path = Path(tmp) / a["name"]
            r = requests.get(a["browser_download_url"], timeout=300)
            r.raise_for_status()
            path.write_bytes(r.content)
            files.append((path, a["name"]))
        print(f"downloaded {len(files)} episode(s)")
        podfeed.ensure_player()
        podfeed.sync_site_audio(files)


if __name__ == "__main__":
    main()
