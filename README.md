# 🤖 AI News Digest

Emails you the top 5 AI stories every morning and evening. 100% free:
GitHub Actions runs it, free RSS feeds supply the news, GitHub Models (free tier)
picks and summarizes the stories, and Gmail sends the email.

## How it works

1. **Collect** — pulls the last 14 hours of posts from company blogs (OpenAI, Google,
   DeepMind, Microsoft, Hugging Face), tech press (TechCrunch, The Verge, Ars Technica,
   VentureBeat, MIT Tech Review, The Decoder, Wired), AI stories from CNBC, Axios and
   Engadget, popular Hacker News posts, and Google News (for Anthropic, Apple, Meta).
2. **Clean** — drops old items, merges the same story from different outlets (keeping the
   publisher's own link over a Google News redirect), and skips anything already emailed
   in the last 5 days, so the evening digest doesn't repeat the morning one.
3. **Pick** — Google Gemini's free tier (falling back to GitHub Models) chooses the
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
| `TO_EMAIL` | where to send it (can be the same address; comma-separate for several) |
| `GEMINI_API_KEY` | free key from https://aistudio.google.com/apikey (writes the summaries) |

### 4. Test it
Repo → **Actions** tab → **AI News Digest** → **Run workflow**. In about a
minute the email should land in your inbox (check Spam the first time and
mark it "Not spam").

## Schedule

Emails arrive at about **7:07 AM and 6:07 PM Texas time, all year**. GitHub cron only
understands UTC, so the workflow has two slots per edition (one for daylight time, one
for standard time); `digest.py` sends on the slot that matches today and quietly skips
the other. GitHub can start scheduled runs a few minutes late when it's busy.

To change the times, edit `TARGET_HOURS` in `digest.py` and the `cron:` lines in
`.github/workflows/digest.yml` (one UTC slot for each offset, -5 and -6).

## Memory (sent.json)

After each email, the workflow commits `sent.json`, the list of stories already sent.
It keeps 5 days of history. These daily commits also keep the repo active, so GitHub
never pauses the schedule for inactivity. Delete the file to reset the memory.

## Customizing

Set these as environment variables in the workflow's `env:` block:

- `TOP_N` — number of stories (default 5)
- `MODEL` — preferred GitHub Models model (default `openai/gpt-4o-mini`); if it is unavailable the script automatically tries other free OpenAI models from the catalog
- `LOOKBACK_HOURS` — how far back to look (default 14)

Add or remove news sources in the `FEEDS` dictionary at the top of `digest.py`.

## Good to know

- **Cost:** free. Each run takes about 1 minute, so ~60 minutes a month, well inside
  GitHub's free 2,000 minutes for private repos (public repos are unlimited).
- **Free AI limits:** GitHub Models' free tier has daily request limits that are far
  above the 2 calls a day this uses.
- **Preview locally:** `pip install -r requirements.txt && python digest.py --dry-run`
  writes `preview.html` without sending anything.

---

# 📈 Portfolio Report

`portfolio_report.py` emails a mutual fund portfolio review on the **1st and 16th of
every month** (and anytime via **Actions → Portfolio Report → Run workflow**).
Holdings live in `portfolio.json`; keep this repo **private**.

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

**Keeping it current:** after any buy/sell, update `portfolio.json` (or send a new
statement export to whoever maintains it). Funds without `transactions` use their
snapshot value and don't get XIRR/tax-lot detail.
