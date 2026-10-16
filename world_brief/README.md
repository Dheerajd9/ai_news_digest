# World Brief

India first, USA second, then the world. One daily email with the full news **plus podcasts in English, Telugu and Hindi**, fully automated on GitHub Actions and **100% free**.

_Last updated: 16 Oct 2026 (podcast site moved to the Daily-World-Brief organization: links no longer show a personal name; language names shown in English: Telugu, Hindi; new sections: NRI money, Hyderabad land, students/visas/tech jobs, travel India↔USA, severe weather, cricket, scams & safety, good news; all times US Eastern; no Dallas/Texas focus; new email look; English-only email text)._

## What you get, and when (US Eastern time)

| When | What |
|---|---|
| **Morning, ~7:10 AM** | **World Brief**: Top 5, what needs my attention, what actually matters today (5-10 stories: what/why/why it matters/who/next), India, USA + India–USA, Canada, 🤖 AI & robotics (highest priority), Musk & frontier companies, chips & computing, world & major world changes, government & politics, money table (incl. USD/CAD, Bitcoin, Ethereum) + why markets moved, economy, personal finance, housing & cost of living, business & future of work, immigration, energy revolution, future tech, space, science/health/climate, 🔍 truth check, 🔮 future signal, 🎓 today's lesson, what to watch, why it matters to me, then **📰 More headlines**: every other story from the day's sources as a one-line link (grouped by topic, up to 60), so nothing is silently dropped. Podcasts: 🔵 English 📻 Full (15–30 min: about 15 on a normal day, up to 30 on a big news day) + ⚡ Quick (5–7 min, short version), 🟠 Telugu and 🟢 Hindi = **the same Full episode** in that language |
| **Evening, ~6:10 PM** | **Evening Update**: only what changed since the morning (not a repeat), plus 📰 More headlines. Podcasts in English, Telugu and Hindi (4–10 min, the same episode in all three) |
| **Sunday morning** | **Weekly report**: the week's patterns and what to watch next week, in all three languages |
| **Every 2 hours** | **Breaking alert**, only for truly big news (max 5 a day) |

**Same news in every language**: the English Full episode is written first (evening: the evening episode); Telugu and Hindi are that same episode spoken in Telugu / Hindi (same stories, order, numbers and details, same chapters; `prompts/translate_mode.md`). Only if that conversion fails is the language episode written straight from the brief, so audio still goes out.

**How the podcasts explain** (`prompts/explain_style.md`, used by every podcast in every language): one host explains, the other asks the simple questions a parent would ask; numbers turned into family money ("₹1,000 grocery bill now ₹1,052"); a one-line "remember…" bridge for continuing stories; opening names the 3 big things plus a one-line Hyderabad weather (free Open-Meteo, no key; `WEATHER_PLACES` to change cities, `WEATHER=0` to switch off); short recap after each long chapter; one good-news story before the takeaways; short forms spelled out; calm tone for scary news.

**Podcast language**: Telugu is spoken Telangana Telugu and Hindi is everyday Hindi, both mixed with simple English the way Hyderabad/city families talk: numbers, prices, dates and hard words (inflation, interest rate, tariff, AI…) are said in English, never pure/bookish Telugu or Hindi.

One email per edition goes to everyone (BCC). Each email has a 🎧 **Listen** box with ▶️ Play links for every language. If a language's audio could not be made (for example the free AI was overloaded), the email says so instead of hiding it.

## How the email looks

Built for reading on a phone without missing anything (`email_style.py`):

- **Subject** shows the top 3 stories, e.g. `🌏 World Brief · Fri 16 Oct: RBI cuts rate · Gold hits record… 🎧`
- **Top 5** in a highlighted box (the email text is English only; Telugu and Hindi are in the podcasts)
- **Big coloured ▶️ buttons** for every podcast (🔵 English, 🟠 Telugu, 🟢 Hindi) with length and hosts
- **📈 Markets now**: one line with Nifty, Sensex, S&P 500, Dow, $→₹, gold (10 g), silver, Brent, Bitcoin, green ▲ / red ▼ (full table further down)
- Every story is a **card**; each section has its **own colour** and a **read time**; sources are small grey chips
- Sections include **🏙️ Hyderabad & Telangana** and **🗺️ Around the world (region by region)**: Asia-Pacific, Middle East, Europe, Russia & Central Asia, Africa, Latin America, Global
- Also: **💸 NRI money corner**, **🏡 Hyderabad land & real estate**, **🎓 Students, work visas & the tech job market**, **✈️ Travel India ↔ USA**, **🌪️ Severe weather & disasters**, **🏏 Cricket & big sports**, **🛡️ Scams & safety alerts**, **🌱 Good news** (each appears when there is real news for it; new sources were added for every one)
- **📰 More headlines** at the end: every other story from the day's ~100 sources (up to 100 one-liners), so nothing is silently dropped
- Readers can simply reply to the email with feedback

## The web player

Every ▶️ Play link opens `https://daily-world-brief.github.io/world-brief-podcast/play.html` (works on iPhone, Android and Windows):

- Language tabs for the same edition (English Full / Quick · Telugu · Hindi); switching keeps your current chapter
- Chapter buttons: ⭐ Most important · 🇮🇳 India · 🇺🇸 USA (+ Canada) · 🌍 World · 🤖 Tech & future · 💰 Money · ✅ Wrap-up
- ⏪ 15 s / 30 s ⏩, speed 0.75x–2x, ⬇️ Download, remembers where you stopped
- Recent episodes (last 10 days)
- Lock-screen / notification / headphone / car / keyboard media controls (title, chapter, cover; next/previous = chapters)

Audio is served from the player site itself (correct audio type, so iPhone plays it). That site branch is replaced by one fresh commit each run and keeps 10 days of audio, so the podcast repo never grows.

## Podcast apps (Apple Podcasts, Spotify, Pocket Casts, AntennaPod)

Each language has its own feed; add it once ("Follow a show by URL" / "Add by URL") and new episodes download by themselves:

| Language | Feed URL |
|---|---|
| 🔵 English | `https://daily-world-brief.github.io/world-brief-podcast/feed.xml` |
| 🟠 Telugu | `https://daily-world-brief.github.io/world-brief-podcast/feed-te.xml` |
| 🟢 Hindi | `https://daily-world-brief.github.io/world-brief-podcast/feed-hi.xml` |

Feeds list the last 10 days (audio older than that is removed from the site). They are hidden from podcast directories; only people with the URL can follow.

## Telegram family channel

Each edition posts a short headline message with named play links ("▶️ 🔵 English 📻 Full", "▶️ 🟠 Telugu" …, the web address stays hidden behind the name) plus every podcast as an audio message that plays inside Telegram. Setup: create a bot with @BotFather and save its token as `TELEGRAM_BOT_TOKEN`; create a channel and add the bot as an **admin** (Post messages on). Then add `run: tgsetup` to `run_request.txt`: it finds the channel automatically, saves it to `state/telegram_channel.json`, posts a test message and emails you. (Or set `TELEGRAM_CHANNEL_ID` yourself.) `TELEGRAM_CHAT_ID` (optional) stays your personal chat for breaking alerts.

## How it works

```
~115 RSS feeds (India, Hyderabad & Telangana, every world region, USA, Canada, world, economy, markets, business & jobs, personal finance & housing, AI & robotics, Musk companies, chips/energy/future tech/space, science, official sources, fact-checkers) ─> last 26 h (evening 12 h) ─> skip already-sent, merge duplicates, rank sources
   ─> exact numbers (markets.py: Nifty, Sensex, gold/silver India & US, rupee, Brent, …)
   ─> story memory (last briefs, story ledger, trends, entities, beliefs)
   ─> AI writes the brief (every bullet cites a source) ─> fact-check pass ─> real source links
   ─> podcast scripts (English Quick/Full, Telugu, Hindi) with chapter markers ─> review pass
   ─> voices ─> MP3 + chapter times ─> GitHub release + player site
   ─> one email (BCC) ─> memory saved (briefs/, state/)
```

## Free AI chain (no card, no payment)

| Job | Main | Free backups, in order |
|---|---|---|
| Writing (brief, fact-check, scripts) | Gemini free tier (several Flash models, each with its own daily limit) | Gemma (same key) → Mistral free "Experiment" plan (`MISTRAL_API_KEY`, optional) → OpenRouter free models (`OPENROUTER_API_KEY`) |
| Voices | Gemini 2-speaker TTS (2 models, Indian-English / Telugu / Hindi library voices) | Google Cloud Chirp 3 HD (`GOOGLE_TTS_API_KEY`, optional, capped at 900k characters/month so it stays in the free tier) → Microsoft voices (free, unlimited) |

If a script comes back far too short, the system asks once more for the full-length version, and if it is still short it asks the AI to expand that draft (keep every line, explain more, add skipped stories). The editor pass is never allowed to cut a script that is already at or under its target length. If Telugu or Hindi fails, it waits 90 s and retries. GitHub Models (the old backup) was retired by GitHub on 30 July 2026 and is no longer used.

## Never misses an edition

- **Several triggers, all free**: GitHub's own timer (slots every 30 min in the morning and evening windows; GitHub often runs them late or drops them) **plus** the free cron-job.org timer below, which calls the workflow on time. No Claude subscription is needed for anything.
- **Sent once**: `state/done/<date>-<edition>` markers stop duplicates.
- **AI down?** A scheduled run retries in the next slot instead of sending a headlines-only email.
- **Failures email you**: "⚠️ run failed — retrying automatically" or "⚠️ code test failed".
- **Podcast key reminder**: an email 14 days before `PODCAST_TOKEN` expires (or as soon as it stops working).

## On-time timer (free, 5 minutes, recommended)

GitHub's own timer often starts runs hours late (a morning run at noon) or skips them. cron-job.org (free, no card) can press "run" on time every day:

1. **GitHub token** (only allowed to start workflows): github.com → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token. Name `news timer`, expiration 1 year, Repository access **Only select repositories → ai_news_digest**, Permissions → Repository → **Actions: Read and write** (nothing else). Generate and copy it.
2. **cron-job.org** → sign up (free) → **Create cronjob**:
   - Title `World Brief`, URL `https://api.github.com/repos/Dheerajd9/ai_news_digest/actions/workflows/world_brief.yml/dispatches`
   - Schedule: **Custom**, minute `10`, crontab expression `10 7-10,18-20 * * *`, every day, time zone **America/New_York**
   - Advanced: Request method **POST**; Headers `Accept: application/vnd.github+json`, `Authorization: Bearer <your token>`, `X-GitHub-Api-Version: 2022-11-28`; Request body `{"ref":"main","inputs":{"edition":"due"}}`
   - Save. "Test run" should show HTTP **204** (= started).
3. Same again for the AI News Digest: title `AI Digest`, URL `…/actions/workflows/digest.yml/dispatches`, same schedule and headers, body `{"ref":"main","inputs":{"due":"true"}}`.

`due` means "send only what is due now and not sent yet", so extra calls are harmless (they stop in seconds). Renew the token yearly (cron-job.org emails you if calls start failing).

## Automatic test before every change

`tests/e2e.py` runs on every code push (fake news, fake AI, silent audio, no network). It checks morning, evening and weekly: exactly one email, English + Telugu + Hindi play links, chapters, audio copied to the player site, memory saved; plus drills for "Telugu fails", "Gemini completely down", the player-site sync and the Google Cloud voice cap. Run it locally with `python tests/e2e.py` (needs ffmpeg).

## Run something now (no clicking in GitHub)

Add a line to `world_brief/run_request.txt` and push; the **World Brief - run all editions now** workflow reads only the words after `run:`:

| Line | Does |
|---|---|
| `run: morning` / `run: evening` / `run: morning weekly` | Sends that edition if it was not sent today |
| `run: morning force` | Sends it again even if already sent |
| `run: keycheck` | Tests every key (and the weather source) and emails a ✅/❌ report |
| `run: sitesync` | Copies the last 10 days of audio onto the player site |
| `run: tgsetup` | Finds the family Telegram channel, saves it, posts a test message |

For the older AI News Digest, push `digest_run_request.txt` with `[send digest]` in the commit message.

## Who gets it

Repo → Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `TO_EMAIL` | only your own address (it also receives private portfolio emails) |
| `PARENTS_EMAIL` | everyone else, comma-separated, e.g. `amma@gmail.com,friend@yahoo.com` |
| `PARENTS_WHATSAPP` | optional `phone:apikey` for a WhatsApp message with play links (CallMeBot) |

Gmail limits: about 100 recipients per email and about 500 per day, so keep the list at about 100 people.

## All secrets

| Secret / variable | Needed? | What for |
|---|---|---|
| `GEMINI_API_KEY` | yes | main AI + voices (aistudio.google.com/apikey) |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `TO_EMAIL` | yes | sending email |
| `PODCAST_TOKEN` (secret), `PODCAST_REPO` (variable) | yes | uploading audio to `Daily-World-Brief/world-brief-podcast` (fine-grained token, Contents read/write, that repo only; renew yearly) |
| `OPENROUTER_API_KEY` | recommended | free backup writer |
| `MISTRAL_API_KEY` | optional | free backup writer |
| `GOOGLE_TTS_API_KEY` | optional | natural backup voices (Google asks for a card; skipped) |
| `PARENTS_EMAIL`, `PARENTS_WHATSAPP` | optional | more readers |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` | optional | family Telegram channel with every podcast |
| `TELEGRAM_CHAT_ID` | optional | your personal Telegram chat (breaking alerts + brief) |
| `PORTFOLIO_JSON` | for the portfolio report | your holdings (kept only as a secret, never in this public repo) |

## Files

- `digest.py` main pipeline (`EDITION=morning|evening|weekly`)
- `languages.py` Telugu and Hindi podcasts · `podcast.py` scripts, chapters, voices · `podfeed.py` uploads, feed, player site · `player.html` the web player
- `markets.py` exact numbers · `weather.py` podcast weather line · `alerts.py` breaking alerts · `keycheck.py` key test · `sitesync.py` audio backfill · `tgsetup.py` Telegram channel finder · `notify_failure.py` failure emails
- `feeds.yaml` sources · `watchlist.yaml` alert keywords · `prompts/` all writing rules (brief, evening, weekly, fact-check, podcasts in 3 languages, rule layers)
- `briefs/` archive and podcast scripts (memory) · `state/` seen list, story ledger, trends, entities, beliefs, scorecard, done markers · `logs/` last run logs (secrets redacted)
- `tests/e2e.py` the end-to-end test

## Security

- The repo is public; secrets live only in GitHub Actions secrets. Logs never print keys, emails or phone numbers. Your portfolio is read from the `PORTFOLIO_JSON` secret and its history was removed from the repo.
- Emails only contain links we trust (real article links from our feeds, the podcast site, this repo). Links the AI or a feed tries to sneak in are dropped; HTML is escaped; no tracking images; subject lines are sanitised.
- GitHub Actions are pinned to exact commits and Python packages to exact versions.
- Workflow inputs are passed as environment variables (no script injection).

## Tuning

- Voices: pin with variables `GEMINI_VOICE_A/B` (English), `GEMINI_TELUGU_VOICE_A/B`, `GEMINI_HINDI_VOICE_A/B`; force Microsoft voices with `PODCAST_TTS=edge` / `TELUGU_TTS=edge` / `HINDI_TTS=edge`.
- Languages: `PODCAST_LANGUAGES` (default `te,hi`), `LANG_EDITIONS` (default `morning,evening,weekly`).
- Player site audio days: `SITE_AUDIO_DAYS` (default 10).
- Alerts: `watchlist.yaml` keywords, `min_score`, `max_alerts_per_day`.
