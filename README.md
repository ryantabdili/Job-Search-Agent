# IP Job Board

Turns LinkedIn job-alert emails into a running board of in-house IP roles, with a one-click LinkedIn post builder.

- **The IP Job Board app** runs in your browser, on your own Mac only. It has four tabs:
  - **Jobs:** every matching role as a card with company, title, location, pay, link, and a one-line note on why it's included. You can search, filter, mark jobs shared, and hide them.
  - **Build a post:** tick the jobs you want, edit the draft, and copy it. A counter tracks LinkedIn's 3,000-character limit.
  - **Add alerts:** check Gmail, upload saved emails, or paste an alert in.
  - **Settings:** post wording, the title list, and the job-board list, all editable without touching code.
- **`jobs.xlsx`** is an always-current spreadsheet copy of the list, for sharing or keeping records.

The board combines public LinkedIn Jobs searches with LinkedIn job-alert emails.

---

## How it works

```
LinkedIn job alerts (one per title)  →  email inbox  →  IP Job Board (jobs.db)  →  LinkedIn post + jobs.xlsx
```

1. You or Sepp set up LinkedIn job alerts for each title on his list. LinkedIn emails the new postings.
2. The bot reads those emails and pulls out each job's company, title, location, pay, and link.
3. It checks each title against the list in `config.json`: Assistant/Associate GC, VP/Senior Director/Director of IP, Senior Patent Counsel, Lead/Managing IP Counsel, Patent Counsel, and IP Counsel. Paralegal, patent agent, and engineer roles are left out.
4. Nothing is silently dropped. Anything that doesn't match goes on the **"Other jobs from alerts"** tab with the reason, so you can spot something the filter missed.
5. Jobs are tracked by LinkedIn's job ID, so the same posting showing up in five alerts is still one row. Pasted text has no IDs, so for those the bot uses fuzzy matching instead: same company, very similar title and location. When that job later arrives by email, its link gets filled in.
6. Every job gets a short note saying why it's there, like "Title matches 'Associate General Counsel' on the list; IP-focused (patent)." Anything the rules miss can be added by hand, and anything irrelevant can be hidden. Those choices stick.
7. Each job is marked **New** (under 7 days), **Active** (under 30), or **Check it's still open**. Old ones are kept out of the post.
8. Jobs posted by job boards like Ladders or BioSpace are flagged, so the post doesn't say "BioSpace is hiring" when the real employer is someone else.

---

## Step 1: Set up the job alerts (one time)

**Easiest option: put the alerts on your own LinkedIn account.** Job alerts are just saved searches. The same search returns the same postings whoever runs it, so Sepp doesn't have to give you anything, and nothing touches the firm's email.

For each title on Sepp's list:
1. On LinkedIn, go to **Jobs** and search the title, e.g. `"Senior Patent Counsel"`. The quotes keep the search tight.
2. Set location to **United States** (or whatever Sepp wants).
3. Turn on **Set alert**, then choose **Daily** and **Email**.

Titles: Assistant General Counsel · Associate General Counsel · Vice President of IP · Senior Director of IP · Director of IP · Senior Patent Counsel · Lead IP Counsel · Managing IP Counsel · Patent Counsel · Intellectual Property Counsel

Tip: add "intellectual property" or "patent" to the two General Counsel searches, or you'll get a lot of non-IP GC jobs. The bot flags those as "General in-house" either way.

**If Sepp would rather use his own alerts,** he can set an email rule to forward messages from `jobalerts-noreply@linkedin.com` to your Gmail. Heads up: a lot of law firms block auto-forwarding to outside addresses, which is one more reason the first option is easier.

---

## Step 2: Install on your Mac (one time, ~5 minutes)

Open **Terminal** (Cmd+Space, type "Terminal").

```bash
python3 --version        # needs 3.10+. If it's older or missing, install from python.org/downloads
cd ~/Documents/ip-job-bot    # wherever you unzipped it
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

To open the app from then on, just double-click **`Open Job Board.command`**. The first time, macOS may block it: right-click it, choose **Open**, then **Open** again. It opens in your browser. Close the Terminal window it starts when you're done.

(From Terminal instead: `source .venv/bin/activate && streamlit run ui.py`)

## Step 3: Connect Gmail (one time)

**The easy way:** open the app, go to **Add alerts**, and enter your Gmail address and an app password (steps 1–2 below). It saves them to a private `.env` file in the folder.

The bot reads Gmail over IMAP using an **app password**, a separate 16-character password that only works for this and can be revoked any time. Your real Google password never goes in the bot.

1. Google Account → **Security** → turn on **2-Step Verification** if it isn't already. App passwords require it.
2. Go to **myaccount.google.com/apppasswords**, create one named "job bot," and copy it.
3. (Or by hand:) in the bot folder, copy `.env.example` to a new file called `.env` and fill it in:
   ```
   GMAIL_ADDRESS=you@gmail.com
   GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
   ```
   In Terminal: `cp .env.example .env && open -e .env`. Files starting with a dot are hidden in Finder, so press Cmd+Shift+. to see them.

The bot opens the mailbox **read-only**. It can't send, delete, move, or mark anything as read.

---

## Testing it before you show Sepp

**1. Offline tests (no email needed):**
```bash
pytest -q
```
You should see `14 passed`. These run the bot on the exact alert Sepp forwarded, a mock HTML alert email, and a plain-text alert. They check title matching, deduplication (including fuzzy matching), the "why included" notes, the job-board flag, hide and add-by-hand, the post, the spreadsheet, the database, and that Gmail is opened read-only.

**2. Try the app on the real alert Sepp sent, without connecting anything:**
Open the app, go to **Add alerts → Paste an alert**, and paste the contents of `tests/fixtures/pasted_alert.txt`. Then check:
- **Jobs** shows 5 matches: Scale AI, NantHealth, AbbVie, BioSpace, and On. BioSpace is flagged as a job board. NantHealth is tagged as general in-house, not IP. Pasted text has no links, so those cards say "No link."
- The Ladders "Alliance Management" role is under **Other jobs from your alerts** with the reason.
- **Build a post** has all 5 ticked, the character counter works, and **Copy post** copies it.
- Click **Mark shared** on one job, then **Hide** on another, and check that the numbers at the top change.
- Paste the same alert again. It should say "Skipped 1 already read" and add nothing.

Then **quit the app and delete `jobs.db` and `jobs.xlsx`** so the test data doesn't mix with the real list.

**3. Real run:** after the alerts have been running a day or two, click **Check Gmail now**. Then compare it against the actual emails:
- Pick 2 or 3 alert emails and confirm every job in them is either on the list or under "Other jobs."
- Click **Open posting ↗** on a few and make sure each opens the right job.
- Read "Other jobs." If a whole type of title keeps getting missed, add a rule in **Settings**. The rules re-apply to every saved job when you save.
- If an email that **is** a job alert comes back with no listings, LinkedIn changed its layout. In Gmail, open that email, choose ⋮ → **Download message**, and send over the `.eml` file so the parser can be updated.

**4. No Gmail?** It works with any mail app. Drag alert emails out of Apple Mail or Outlook (they save as `.eml`), then upload them under **Add alerts**.

---

## Using it week to week

1. Double-click **`Open Job Board.command`**.
2. Go to **Add alerts → Check Gmail now**.
3. Go to **Build a post**, untick anything you don't want, edit, and **Copy post**. Sepp pastes it into LinkedIn.
4. Click **Mark these as shared** so they don't repeat next week.

Change the opening and closing lines under **Settings** so they sound like Sepp.

**Command line (optional):** everything also works without the app, e.g. `python jobbot.py --gmail`, `python jobbot.py --eml-folder ~/Desktop/alerts`, `python jobbot.py --text alert.txt`, and `python jobbot.py --mark-shared`. Each run also writes `post_draft.txt`.

---

---

## Files

| File | What it is |
|---|---|
| `ui.py` | The IP Job Board app |
| `Open Job Board.command` | Double-click launcher for the app |
| `jobbot.py` | The engine: inputs, database, spreadsheet, post. Also works from the command line. |
| `alerts.py` | Reads alert emails, matches titles, and checks for duplicates |
| `config.json` | Title rules, exclusions, job-board list, post wording (editable in Settings) |
| `.streamlit/config.toml` | App colors |
| `.env.example` | Template for Gmail settings (the app can write `.env` for you) |
| `tests/` | Offline tests and the alert Sepp forwarded |
| `jobs.db` | Created on first run. A SQLite database with every job the bot has seen. Don't delete it once you're using it for real. |
