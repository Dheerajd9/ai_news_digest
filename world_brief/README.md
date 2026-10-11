# World Brief

India first, USA second, then the world. A daily news email plus a two-host audio podcast, fully automated on GitHub Actions and **100% free**.

## What you get, and when (Dallas time, summer; one hour earlier in winter)

| When | What | Why that time |
|---|---|---|
| **7:37 AM daily** | **World Brief**: 13 sections, ~9 min read + ~10 min podcast | India's day is done (~6 PM IST), US overnight news in, before US markets open |
| **6:07 PM daily** | **Evening Update**: only what changed since morning, ~3 min + ~4 min podcast | US day and markets closed; India early morning |
| **Sunday 9:07 AM** | **Weekly Deep Dive**: the week's 5 big stories and their arc, what to watch next week, ~14 min podcast | Built from the week's briefs |
| **Every 2 h, 7 AM-11 PM** | **Breaking alert** only if something truly big (visa rule, crash, disaster, war) | Usually silent |

Every email starts with an **exact numbers box** (USD→INR, Nifty, Sensex, S&P 500, Nasdaq, gold, Brent, Dallas + Hyderabad weather) from data APIs, never from AI.

## How it works

```
RSS (~45 feeds) ─> last 26h/12h ─> skip already-sent, merge duplicates (count outlets)
      ─> numbers box (markets.py) ─> story memory (last 3 days of briefs/)
      ─> Gemini writes brief, every bullet cites [S12]
      ─> fact-check pass (GitHub Models) drops anything sources don't support
      ─> ids become links ─> podcast.py: Arjun & Meera script ─> edge-tts Indian voices ─> MP3
      ─> Gmail (+ Telegram) ─> archive in briefs/ ─> podfeed.py ─> your podcast app
```

Story memory: yesterday's stories are not repeated; continuing ones come as short "Update:" bullets.

## Free stack and daily usage

| Piece | Free source | Use per day |
|---|---|---|
| Main brief | Gemini free tier (`GEMINI_API_KEY`) | 2 calls (+1 on Sunday) |
| Fact-check, podcast script, alert scoring | GitHub Models (built-in `GITHUB_TOKEN`) | ~4-12 small calls |
| Voices | edge-tts (Microsoft neural, no key; unofficial) | ~150 short lines |
| Markets / weather / history | Yahoo + Stooq + ECB, Open-Meteo, Wikipedia | few requests |
| Running it | GitHub Actions | ~15 min (private repo free tier: 2,000 min/month) |
| Delivery | Gmail SMTP app password, optional Telegram | 2-3 emails |

If any piece fails, the rest still runs: no AI → headline list; no voice → email without audio; no market data → box skipped.

## Files

- `digest.py` main pipeline (editions: `EDITION=morning|evening|weekly`)
- `markets.py` exact numbers box · `podcast.py` audio · `podfeed.py` podcast-app feed · `alerts.py` breaking alerts
- `feeds.yaml` news sources · `watchlist.yaml` alert keywords and thresholds
- `prompts/` brief, evening, weekly, fact-check, podcast, alert prompts (edit READER PROFILE to tune)
- `briefs/` archive (also the story memory) · `state/seen.json` what was already sent

## Setup

Already live in this repo, reusing secrets `GEMINI_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `TO_EMAIL`. Optional: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (alerts then go to your phone instead of email).

Run any edition now: Actions → **World Brief + Podcast** → Run workflow → pick edition.

### Optional: podcast in your podcast app (5 minutes, one time)

This repo is private and podcast apps can't log in, so episodes go to a tiny **public** repo (public news audio only):

1. Create a new **public** repo named `world-brief-podcast` (tick "Add a README").
2. In that repo: Settings → Pages → Source: *Deploy from a branch* → `main` / root → Save.
3. GitHub → Settings → Developer settings → Fine-grained tokens → Generate: repository access *Only* `world-brief-podcast`, permission *Contents: Read and write*.
4. In **this** repo: Settings → Secrets and variables → Actions:
   - Secret `PODCAST_TOKEN` = that token
   - Variable `PODCAST_REPO` = `Dheerajd9/world-brief-podcast`
5. After the next run, in Apple Podcasts (Library → ⋯ → Follow a Show by URL) or Pocket Casts (Search → paste URL) add:
   `https://dheerajd9.github.io/world-brief-podcast/feed.xml`

The feed is marked hidden from podcast directories; only people with the URL can subscribe.

## Tuning

- Voices: `PODCAST_VOICE_A` / `PODCAST_VOICE_B` (e.g. `en-US-AndrewNeural`), speed `PODCAST_RATE`, audio off `PODCAST=0`.
- Alerts: `watchlist.yaml` → keywords, `min_score`, `max_alerts_per_day`.
- Length: `MAX_PER_CATEGORY`, `CATEGORY_CAPS` in digest.py.

## Bonus: manual prompt for ChatGPT / Claude chat (no code)

Paste this into a chat that has web search turned on. Good for testing what you like before automating. The automated version above is more reliable because it controls the sources.

```
Act as the chief editor of my private daily brief. Search the web for news from the last 24 hours only. Priority: India first, USA second, then the rest of the world. Use at least 12 reputable outlets: Indian (The Hindu, Indian Express, Hindustan Times, Mint, Economic Times), American (AP, NPR, Reuters), and global (BBC, Al Jazeera, DW, Nature, Ars Technica).

About me: Indian citizen working as a data/MLOps engineer in Dallas, USA. Family in Hyderabad, Telangana. I care about Indian politics, economy and policy; Telangana/Hyderabad; US politics; India-US relations; H-1B, green card, OPT and Canada Express Entry rules; trade, tariffs, imports/exports, the rupee and remittances; AI/tech; science; history. No celebrity or film news; cricket only if historic.

Rules:
- Rank by real impact (people affected, money involved, irreversibility, precedent), not drama.
- India and USA each get their own section daily, but never drop a truly major world story.
- Neutral on politics in both countries: what happened, what each side says. No opinions.
- Every bullet ends with a source link. If you can't verify something, leave it out. Never invent numbers or quotes. Keep rupee amounts as reported (crore/lakh).
- Each story appears once. Merge the same story from several outlets.
- Simple English, short sentences, explain jargon briefly. Max ~1,400 words. If a section is quiet, write "Quiet day."

Format:
**In one line:** the single most important development for me.
1. Top 5 must-know — bold headline, 1–2 sentences, "Why it matters:" one line.
2. India — 4–8 bullets (Politics / Economy / States).
3. Telangana & Hyderabad — 1–3 bullets.
4. USA — 3–6 bullets.
5. India-US, visas & trade — 2–6 bullets.
6. World — 3–6 bullets by region, note India's angle where relevant.
7. Global economy & markets — 2–4 bullets.
8. Tech & AI (incl. Indian IT) — 3–5 bullets.
9. Science, health & climate (incl. ISRO, monsoon) — 2–4 bullets.
10. Coming up in the next 7 days — up to 5 bullets.
11. On this day in history — 2–3 events, at least one Indian if possible.
12. One idea to understand today — one concept, 3–4 sentences, everyday example.
13. For me — 2–3 bullets linking today's news to my career, money, family or visa.
```
