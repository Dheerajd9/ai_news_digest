# 🧠 ADVANCED STORY TRACKING — ADD-ON ONLY

This is an ADD-ON to the existing news intelligence system.

Do NOT replace, rewrite or override the existing:

- News research rules
- Email structure
- Podcast structure
- Kartik & Priya rules
- Market dashboard
- Intelligence Layer
- Story ranking rules

This add-on gives the system three additional abilities:

**1. Remember unanswered questions**

**2. Track announcements/proposals until they actually happen**

**3. Maintain simple timelines for major ongoing stories**

The goal is NOT to create more news.

The goal is to remember important stories better and report them only when something meaningful changes.

---

# 1. ❓ OPEN QUESTIONS TRACKER

When an important story is unresolved, identify the important questions that still need answers.

Example:

India and USA begin trade negotiations.

Possible open questions:

- Will they reach an agreement?
- Will tariffs change?
- Which products will be affected?
- When would changes take effect?

Store these questions with the story when possible.

Example internal memory:

```json
{
  "story_id": "INDIA_US_TRADE",
  "open_questions": [
    "Will a final agreement be reached?",
    "Will tariff rates change?",
    "Which sectors will be affected?"
  ]
}
```

On future runs, compare new information against these questions.

If a new development answers one of them:

Increase the importance of the story.

Example:

PREVIOUS OPEN QUESTION:

“Will tariff rates change?”

NEW DEVELOPMENT:

“India and USA agree to reduce tariffs on selected products.”

This is meaningful new information and should be considered for the briefing.

If none of the important questions were answered and nothing else materially changed:

Do NOT repeat the story simply because new articles were published.

---

# 2. UPDATE OPEN QUESTIONS

Open questions should change as the story develops.

Example:

Monday:

India and USA begin negotiations.

Questions:

- Will they reach an agreement?
- Will tariffs change?
- When will the agreement happen?

Thursday:

Agreement announced.

Remove:

“Will they reach an agreement?”

Now add:

- When does the agreement take effect?
- Which products are included?
- Will both governments formally approve it?

Later:

Agreement takes effect.

Remove questions that have been answered.

Add new questions only when they are genuinely important.

Do NOT allow open-question lists to grow endlessly.

Keep approximately:

**1–5 important unresolved questions per major story.**

---

# 3. 🎯 PROPOSAL → REALITY TRACKER

For important government, immigration, tax, trade, regulatory and corporate developments, track the difference between:

**WHAT SOMEONE SAID**

and

**WHAT ACTUALLY HAPPENED.**

Possible lifecycle:

**DISCUSSION**

↓

**PROPOSED**

↓

**FORMALLY ANNOUNCED**

↓

**APPROVED / PASSED**

↓

**SIGNED / FINALIZED**

↓

**EFFECTIVE**

↓

**IMPLEMENTED**

↓

**REAL-WORLD EFFECT**

Not every story will use every stage.

Only use the stages that make sense.

---

# 4. NEVER CONFUSE THESE STAGES

These differences are extremely important.

A politician saying:

“We should change H-1B.”

does NOT mean:

“H-1B changed.”

A government proposing a tariff does NOT mean:

“The tariff is active.”

A bill passing one legislative chamber does NOT necessarily mean:

“It became law.”

A court hearing a case does NOT mean:

“The court changed the law.”

A company announcing an investment does NOT mean:

“The entire investment has already been spent.”

Always identify the actual stage.

---

# 5. IMMIGRATION TRACKING

Use Proposal → Reality tracking especially carefully for:

H-1B

F-1

OPT

STEM OPT

H-4

PERM

Green Cards

Visa Bulletin changes

USCIS rules

DHS rules

Department of State rules

Department of Labor rules

Canada immigration

Study permits

PGWP

Express Entry

PNP

Permanent residency

Example:

January:

New H-1B rule proposed.

STATUS:

`PROPOSED`

March:

Final rule published.

STATUS:

`FINALIZED`

June:

Rule becomes effective.

STATUS:

`EFFECTIVE`

The June development may deserve significant attention because:

**Today the rule actually begins affecting people.**

---

# 6. TAX / TRADE / POLICY TRACKING

Apply the same logic to:

Taxes

Tariffs

Import restrictions

Export restrictions

Sanctions

Government benefits

AI regulation

Technology regulation

Environmental regulation

Trade agreements

Interest-rate policy changes

Major government programs

Always ask:

**Is this being discussed?**

**Was it proposed?**

**Was it approved?**

**Has it taken effect?**

**Is it actually being implemented?**

---

# 7. COMPANY PROMISE → ACTION

This tracker can also apply to major corporate announcements.

Example:

Company announces:

“We will invest $10 billion in India.”

Do NOT automatically treat that as:

“$10 billion has been invested.”

Track when meaningful:

ANNOUNCED

↓

COMMITTED

↓

CONSTRUCTION / IMPLEMENTATION STARTED

↓

ACTUAL INVESTMENT

↓

OPERATIONAL

This is especially useful for:

Semiconductor factories

AI data centers

Manufacturing plants

Technology campuses

Major infrastructure

Large job announcements

---

# 8. 🕰️ STORY TIMELINE

For major continuing stories, maintain a SHORT timeline.

Example:

`STORY_ID: INDIA_US_TRADE`

Timeline:

Aug 12 → Negotiations announced

Aug 28 → First negotiation round completed

Sep 14 → Tariff disagreement emerged

Sep 29 → Negotiations resumed

Oct 10 → Preliminary agreement announced

Only store meaningful milestones.

Do NOT add every article.

---

# 9. WHAT DESERVES A TIMELINE?

Create timelines only for stories likely to continue or matter over time.

Examples:

Major wars/conflicts

India–USA trade negotiations

Major immigration changes

Important court cases

Major elections

Large government policies

Major AI regulation

Important technology shifts

Major corporate investments

Significant economic-policy changes

Do NOT create timelines for routine daily news.

---

# 10. TIMELINE SIZE

Keep timelines compact.

Prefer approximately:

**3–10 meaningful milestones**

for active stories.

If a story becomes very long:

Compress older milestones.

Example:

Instead of keeping 25 separate negotiation meetings:

“Aug–Sep → Multiple negotiation rounds held without agreement.”

Then retain recent important events individually.

This keeps the memory small and suitable for GitHub JSON files.

---

# 11. USE TIMELINES FOR CONTEXT

The timeline exists primarily so the system remembers the story.

Do NOT automatically display the entire timeline in the email or podcast.

Use it to create natural context.

Example:

KARTIK:

“We've been following these India-US trade talks since August. After several rounds without an agreement, there's finally a significant development today.”

PRIYA:

“Yeah. The two sides have now reached a preliminary agreement on…”

This sounds much better than explaining the entire story again.

---

# 12. PREVIOUSLY → TODAY → NEXT

For major continuing stories, internally think:

**PREVIOUSLY**

What was the situation?

↓

**TODAY**

What actually changed?

↓

**NEXT**

What unresolved question or event should we watch?

Example:

PREVIOUSLY:

India and USA were negotiating tariffs.

TODAY:

Preliminary agreement reached.

NEXT:

When will it take effect and which products are covered?

Use this reasoning to produce concise updates.

---

# 13. STORY RESOLUTION

When all important questions are answered and the event is effectively complete:

Mark the story:

`RESOLVED`

If it no longer needs active monitoring:

Mark:

`ARCHIVED`

Do not continue reporting it simply because commentary articles continue appearing.

If something major happens later:

The story can become active again.

---

# 14. SIMPLE MEMORY FORMAT

Keep this compatible with simple JSON stored in the GitHub repository.

Example:

```json
{
  "story_id": "INDIA_US_TRADE_2026",
  "title": "India-US Trade Negotiations",
  "status": "DEVELOPING",
  "stage": "NEGOTIATIONS",
  "first_seen": "2026-08-12",
  "last_updated": "2026-10-10",

  "current_summary": "India and USA are negotiating a trade agreement.",

  "open_questions": [
    "Will a final agreement be reached?",
    "Will tariffs change?",
    "Which sectors will be affected?"
  ],

  "timeline": [
    {
      "date": "2026-08-12",
      "event": "Negotiations announced"
    },
    {
      "date": "2026-08-28",
      "event": "First negotiation round completed"
    },
    {
      "date": "2026-09-29",
      "event": "Negotiations resumed"
    }
  ]
}
```

This is only an example structure.

The implementation may use a similar lightweight format.

---

# 15. KEEP MEMORY SMALL

Do NOT store huge article text inside story memory.

Store:

Short summaries

Important dates

Current status

Important milestones

Open questions

Relevant source references when needed

The objective is:

**Useful memory, not a second news database.**

---

# 16. DAILY BEHAVIOR

Every daily run should conceptually do:

**STEP 1**

Read active story memory.

**STEP 2**

Read today's new verified information.

**STEP 3**

Ask whether today's information belongs to an existing story.

**STEP 4**

If YES:

Determine what actually changed.

Update:

Status

Stage

Open questions

Timeline

Current summary

**STEP 5**

If NO:

Determine whether it deserves a new story memory entry.

**STEP 6**

Archive resolved/inactive stories when appropriate.

**STEP 7**

Use the updated understanding to create the existing email and podcast.

---

# 17. DO NOT CREATE MEMORY FOR EVERYTHING

Story memory is NOT for:

Routine weather

Small stock moves

Minor corporate announcements

Celebrity news

Minor crime

Routine sports

Small political comments

Minor product releases

Random viral stories

Create memory only when there is a reasonable chance the story will matter again.

---

# 18. FREE-RESOURCE RULE

This feature must remain compatible with:

**GitHub Actions**

**Python**

**JSON/Markdown files**

**RSS/public sources**

**Existing free/free-tier AI resources**

Do NOT require:

Paid databases

Vector databases

Paid memory systems

Complex cloud infrastructure

Store active story memory directly in the GitHub repository.

---

# 19. FINAL CHECK

Before repeating a continuing story ask:

**Did one of our important unanswered questions get answered?**

**Did the story move to a new stage?**

**Was there a meaningful new milestone?**

**Did the real-world situation actually change?**

If NO to all four:

The story probably does not need to be repeated.

If YES:

Explain:

**what changed**

rather than retelling everything.

---

# FINAL OBJECTIVE

The system should stop behaving like:

**“I found another article about this story.”**

and start behaving like:

**“I've been following this story for you. Something important finally changed.”**

The listener should feel that the system remembers what happened before, knows what questions remain unanswered, understands the difference between proposals and actual implementation, and tells them when the story genuinely moves forward.