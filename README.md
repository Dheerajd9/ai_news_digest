# Dji's automations

This public repo runs three free, fully automatic jobs on GitHub Actions. _Last updated: 16 Oct 2026 (no Claude scheduled tasks: free cron-job.org timer instead)._

| Job | What | Where |
|---|---|---|
| 🌏 **World Brief** | Morning, evening and Sunday news email (India → USA → World) with podcasts in **English, Telugu and Hindi**, web player with chapters | [`world_brief/README.md`](world_brief/README.md) |
| 🤖 **AI News Digest** | Top 5 AI stories, morning and evening | below |
| 📈 **Portfolio Report** | Mutual fund review on the 1st and 16th + daily portfolio line and alerts | below (holdings stay private in a secret) |

Everything is free: GitHub Actions (unlimited for public repos), Gemini free tier, free backups (OpenRouter, Mistral), Gmail.

---

# 🤖 AI News Digest

Emails you the top 5 AI stories every morning and evening. 100% free:
GitHub Actions runs it, free RSS feeds supply the news, Gemini's free tier picks and
summarizes the stories, and Gmail sends the email.

## How it works

1. **Collect** — pulls the last 14 hours of posts from company blogs (OpenAI, Google,
   DeepMind, Microsoft, Hugging Face), tech press (TechCrunch, The Verge, Ars Technica,
   VentureBeat, MIT Tech Review, The Decoder, Wired), AI stories from CNBC, Axios and
   Engadget, popular Hacker News posts, and Google News (for Anthropic, Apple, Meta).
2. **Clean** — drops old items, merges the same story from different outlets (keeping the
   publisher's own link over a Google News redirect), and skips anything already emailed
   in the last 5 days, so the evening digest doesn't repeat the morning one.
3. **Pick** — Google Gemini's free tier chooses the
   5 most important stories and writes a summary + "why it matters" for each.
   Links always come straight from the feeds, never from the AI.
4. **Send** — a clean HTML email to your inbox. If the AI step ever fails,
   you still get the top 5 headlines.

## Setup (about 10 minutes)

### 1. Create a Gmail App Password
Gmail won't let scripts use your normal password, so you create a separate one.

1. Turn on 2-Step Verification: https://myaccount.google.com/security
2. Go to https://myaccount.google.com/apppasswords
3. Name it `ai-news-digest` → **Create** → copy the 16-character password.

### 2. Create the GitHub repo
1. https://github.com/new → name it `ai-news-digest` → **Private** is fine → Create.
2. Upload these files (keep the folder structure, including `.github/workflows/digest.yml`):
   ```
   git init && git add . && git commit -m "AI news digest"
   git branch -M main
   git remote add origin https://github.com/Dheerajd9/ai_news_digest.git
   git push -u origin main
   ```
   Or use **Add file → Upload files** in the browser.

### 3. Add 3 secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `GMAIL_USER` | your Gmail address (the sender) |
| `GMAIL_APP_PASSWORD` | the 16-character app password |
| `TO_EMAIL` | your own address only (private portfolio emails also go here) |
| `GEMINI_API_KEY` | free key from https://aistudio.google.com/apikey (writes the summaries) |

### 4. Test it
Repo → **Actions** tab → **AI News Digest** → **Run workflow**. In about a
minute the email should land in your inbox (check Spam the first time and
mark it "Not spam").

## Schedule

Morning edition from about **7:07 AM** and evening from about **6:07 PM** Dallas time. GitHub's
timer is unreliable, so there are backup slots every hour (morning until 1:59 PM, evening
until 9:59 PM); `digest.py` sends an edition only once per day (`digest_done.json`). The
The free cron-job.org timer (see `world_brief/README.md`, "On-time timer") also calls it with `due=true` so it arrives on time.
To send now, push `digest_run_request.txt` with `[send digest]` in the commit message.

## Memory (sent.json)

After each email, the workflow commits `sent.json`, the list of stories already sent.
It keeps 5 days of history. These daily commits also keep the repo active, so GitHub
never pauses the schedule for inactivity. Delete the file to reset the memory.

## Customizing

Set these as environment variables in the workflow's `env:` block:

- `TOP_N` — number of stories (default 5)
- `LOOKBACK_HOURS` — how far back to look (default 14)

Add or remove news sources in the `FEEDS` dictionary at the top of `digest.py`.

## Good to know

- **Cost:** free. The repo is public, so GitHub Actions minutes are unlimited.
- **Free AI limits:** Gemini's free tier is far above the 2 calls a day this uses.
  (GitHub Models was retired by GitHub on 30 July 2026.)
- **Preview locally:** `pip install -r requirements.txt && python digest.py --dry-run`
  writes `preview.html` without sending anything.

---

# 📈 Portfolio Report

`portfolio_report.py` emails a mutual fund portfolio review on the **1st and 16th of
every month** (and anytime via **Actions → Portfolio Report → Run workflow**).
Holdings live only in the **`PORTFOLIO_JSON` repository secret** (this repo is public; the
file was removed from the repo and its history). The workflow writes it to `portfolio.json`
at run time only.

What's in the report:

- **Portfolio manager view** — per-fund suggestions (keep / watch / consolidate /
  switch to Direct), a simpler target structure and tax-aware steps. Written by Gemini
  from your data plus the last 90 days of news about each fund (and Google Search when
  the free quota allows). Suggestions to verify, not advice.
- **Projections** — 5/10/15/20 years under conservative / expected / optimistic
  category assumptions, in today's money and after estimated LTCG.
- **Chances** — 5,000-path Monte Carlo using the portfolio's real 3-year volatility.
- **Benchmark check** — each active fund vs its index (Nifty 50 / Midcap 150 /
  Smallcap 250 / Nifty 500) over rolling 3-year periods.
- **Tax calendar** — long/short-term gains, unused ₹1.25 lakh exemption, lots turning
  long-term soon, Regular→Direct switch cost; a countdown banner in Jan–Mar.
- **Fund table** — your XIRR, 15-day/1/3/5-year returns, volatility, worst fall.

Funds are matched to AMFI schemes and verified against your purchase NAVs, so a
lookalike scheme can't slip in.

**Daily extras (in the 7 AM news email):** a one-line portfolio value with last-day and
month-to-date change, plus a separate **alert email** if the portfolio falls 7%+ in a
week or a fund falls 15%+ from its 3-month high (at most once a week per alert).

**Keeping it current:** after any buy/sell, update the `PORTFOLIO_JSON` secret (Settings →
Secrets and variables → Actions → PORTFOLIO_JSON → pencil → paste the new JSON). Never add
`portfolio.json` back to the repo (it is git-ignored). Funds without `transactions` use their
snapshot value and don't get XIRR/tax-lot detail.
