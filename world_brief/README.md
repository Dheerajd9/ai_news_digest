# World Brief

A free daily email (and optional Telegram message) that tells you what happened in about 9 minutes. India first, USA second, then the world: politics, economy and trade, visas, tech and AI, science, upcoming events, and a bit of history. Runs on GitHub Actions, costs nothing.

## How it works (the pattern most people use)

```
RSS feeds (~45: India, Telangana, USA, world) ──> last 26h only ──> drop already-sent + merge duplicates
      │                                               (count how many outlets = importance)
      ▼
cap per category (India 40, others 25), round-robin by outlet ──> Wikipedia "on this day"
      │
      ▼
LLM (Gemini free tier, GitHub Models free backup) + brief_prompt.md
      │   model must cite [S12] ids for every bullet → we swap ids for real links
      ▼
Email (Gmail) / Telegram  ──>  commit seen.json + briefs/DATE.md back to repo
```

Why this design, not "ask ChatGPT for today's news":
- **Grounded.** The model only sees real headlines you fetched, and must cite each one. Much less hallucination, and every bullet is clickable.
- **Cheap.** About 30–40k input tokens per day fits free tiers easily.
- **No repeats.** `state/seen.json` remembers what you got for 7 days.
- **Never silent.** If every LLM fails, you still get a plain headline list.
- **Daily commit** keeps the repo active so GitHub never auto-disables the schedule (public repos stop scheduled runs after 60 days without activity).

## Setup

Already done in this repo. It reuses the existing secrets `GEMINI_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `TO_EMAIL`, plus the built-in `GITHUB_TOKEN` for the free GitHub Models backup. Optional: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Workflow: `.github/workflows/world_brief.yml`, daily 7:37 AM CDT / 6:37 AM CST. Actions tab → **World Brief + Podcast** → **Run workflow** to get one now.

## Podcast

Same brief, retold as a 9–11 minute chat between two hosts, Arjun and Meera, with Indian English neural voices:
1. `prompts/podcast_prompt.md` turns the brief into a natural two-host script (free Gemini / GitHub Models).
2. `podcast.py` speaks each line with free Microsoft Edge voices via `edge-tts` (`en-IN-PrabhatNeural`, `en-IN-NeerjaExpressiveNeural`), joins them with ffmpeg.
3. The MP3 is attached to the email, sent to Telegram if set up, and kept 14 days as a run artifact.

Change voices with env vars `PODCAST_VOICE_A` / `PODCAST_VOICE_B` (e.g. `en-US-AndrewNeural`, `en-GB-SoniaNeural`), speed with `PODCAST_RATE`, or turn audio off with `PODCAST=0`. edge-tts is free but unofficial; if Microsoft ever blocks it, the email still arrives, just without audio.

## Tuning

- **What you get:** edit the READER PROFILE block and sections in `prompts/brief_prompt.md`. This is the main lever.
- **Sources:** edit `feeds.yaml`. Google News search feeds take any query, e.g. `https://news.google.com/rss/search?q=semiconductors+when:1d&hl=en-US&gl=US&ceid=US:en`.
- **Length/cost:** `MAX_PER_CATEGORY` (default 25) and `CATEGORY_CAPS` in digest.py and `LOOKBACK_HOURS` (default 26) env vars.
- **Twice a day:** add a second cron line, e.g. `"52 23 * * *"` for evening, and set `LOOKBACK_HOURS=13`.
- **Privacy note:** Gemini free tier may use prompts to improve Google products. Fine for public news; never put personal data in the prompt.

## Ideas to level up later

- Weekly "Sunday deep dive": a second workflow that reads the last 7 files in `briefs/` and writes trends of the week.
- Embedding-based clustering (Gemini embeddings are free) instead of title similarity, for better merging.
- Audio version: send the brief to a TTS model and post the MP3 to Telegram for the commute.
- A Telugu edition for parents, using the same items and a translated prompt.

---

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
