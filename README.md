# 🤖 AI News Digest

Emails you the top 5 AI stories every morning and evening. 100% free:
GitHub Actions runs it, free RSS feeds supply the news, GitHub Models (free tier)
picks and summarizes the stories, and Gmail sends the email.

## How it works

1. **Collect** — pulls the last 14 hours of posts from OpenAI, Google, DeepMind,
   Microsoft, Hugging Face, TechCrunch, The Verge, Ars Technica, VentureBeat,
   MIT Tech Review, and Google News (for Anthropic, Apple, Meta).
2. **Clean** — drops old items and merges the same story from different outlets.
3. **Pick** — a free AI model (`openai/gpt-4o-mini` via GitHub Models) chooses the
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

### 4. Test it
Repo → **Actions** tab → **AI News Digest** → **Run workflow**. In about a
minute the email should land in your inbox (check Spam the first time and
mark it "Not spam").

## Schedule

| Run | Cron (UTC) | Texas time (CDT, until Nov 1) | Texas time (CST, after Nov 1) |
|---|---|---|---|
| Morning | `7 12 * * *` | ~7:07 AM | ~6:07 AM |
| Evening | `7 23 * * *` | ~6:07 PM | ~5:07 PM |

GitHub cron only understands UTC, so the time shifts an hour with daylight saving.
Scheduled runs can also start 5–30 minutes late when GitHub is busy. To change the
times, edit the two `cron:` lines in `.github/workflows/digest.yml`.

## Customizing

Set these as environment variables in the workflow's `env:` block:

- `TOP_N` — number of stories (default 5)
- `MODEL` — any GitHub Models model, e.g. `openai/gpt-4.1` (default `openai/gpt-4o-mini`)
- `LOOKBACK_HOURS` — how far back to look (default 14)

Add or remove news sources in the `FEEDS` dictionary at the top of `digest.py`.

## Good to know

- **Cost:** free. Each run takes about 1 minute, so ~60 minutes a month, well inside
  GitHub's free 2,000 minutes for private repos (public repos are unlimited).
- **60-day rule:** GitHub pauses scheduled workflows in a repo with no commits for
  60 days and emails you first. Just click **Enable workflow** on the Actions tab,
  or make any small commit.
- **Free AI limits:** GitHub Models' free tier has daily request limits that are far
  above the 2 calls a day this uses.
- **Preview locally:** `pip install -r requirements.txt && python digest.py --dry-run`
  writes `preview.html` without sending anything.
