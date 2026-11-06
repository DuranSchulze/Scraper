# Workspace Scraper

Local tool that reads Gmail, Google Contacts and Google Sheets (read-only), extracts client
records with rules from `backend/rules.yaml`, and lets a person review each record in a local web page.
Approved records go to the `clients` table in Neon Postgres. No AI or third-party service sees the data.

## 1. Google Workspace setup (admin, once)

1. In Google Cloud Console, create a project and enable: **Gmail API**, **People API**,
   **Admin SDK API**, **Google Sheets API**.
2. Create a **service account** and download its JSON key. Store it **outside this repo**.
3. In Google Admin console → Security → Access and data control → API controls →
   **Domain-wide delegation** → Add new. Use the service account's client ID and these scopes:

   ```
   https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/contacts.readonly,https://www.googleapis.com/auth/contacts.other.readonly,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/spreadsheets.readonly
   ```
4. Share each client-tracker Sheet with the account set as `GOOGLE_ADMIN_EMAIL`.

## 2. Local setup

## Folders

| Folder | What it is |
|---|---|
| `backend/` | Python: scraper, extraction rules (`rules.yaml`), API, migrations, tests |
| `frontend/` | Next.js review GUI; talks only to the backend API |
| `.env` (root) | Your settings and secrets, shared by both. Never committed. |

Run every command below from the repository root. The `Makefile` handles the folders, so no `cd` is needed.

```bash
cp backend/.env.example .env        # fill in every value
make setup                          # installs backend and frontend dependencies
make migrate                        # creates or updates the database tables
```

Use an absolute path for `GOOGLE_SERVICE_ACCOUNT_FILE` in `.env`.

Edit `backend/rules.yaml`: department aliases, service names and keywords.

## 3. Run

```bash
make dev        # starts the API (127.0.0.1:8765) and the GUI (127.0.0.1:3000); Ctrl+C stops both
```

Or one per terminal: `make api` and `make web`.

Open http://127.0.0.1:3000. Both bind to this computer only.

Nothing is lost on a restart: records live in Neon, the job log and status are stored in the
`jobs` tables, filters and the open record are in the page URL, and unsaved form edits are kept in
the browser until you approve or reject. If the API stops during a scrape, the GUI shows
**Interrupted** about 2 minutes later with a **Resume** button; Resume skips mail already fetched.

1. Pick sources and mailboxes (blank = every user), then **Run scrape**. Records appear while it runs:
   extraction runs after each mailbox.
2. Review each pending record. Its **Stage** follows the sales trigger: inquiry, quoted (staff sent
   pricing or a quote), invoiced (staff sent an invoice, amount shown), paid (client sent payment
   proof, or staff confirmed payment). Read any **Check before approving** warnings, check the
   evidence (**Open source** shows the original email, attachments and invoice PDF text), then
   **Approve (staging)**, **Reject**, or **Merge** into another record's ID.
3. **Publish** copies approved records from staging (`candidates`) to the official `clients` table.
   Publishing again only sends records approved or edited since the last publish.
4. After editing `backend/rules.yaml`, press **Re-run extraction**. No refetch from Google is needed.
5. **Client details** (in each record) shows what the client paid, when they became a client and for how
   long, the services they bought, which department and staff mailbox handled them, and a timeline of
   deal emails. Payment rows marked **From invoice — confirm** or **Amount unknown** need a check:
   edit the amount, remove a duplicate row, or add a missing payment before approving.
   Everything except the timeline is copied to `clients` on Publish.

After upgrading: run `make migrate`, then **Re-run extraction**. Records already
approved keep their old details until you move them **Back to pending** and approve them again.

Reruns are incremental: Gmail continues from the last sync point. Tick **Full rescan** to ignore it.
Approved, rejected and merged records are never overwritten by a rerun.

## Start over (empty the database)

**This permanently deletes every scraped email, every staging record, the job history and the
official `clients` table.** There is no undo. The next scrape downloads every mailbox again.

```bash
uv run --directory backend python -m scraper.db reset          # shows the database and row counts; deletes nothing
```

```bash
uv run --directory backend python -m scraper.db reset --yes    # deletes everything listed above
```

It refuses while a job is running. Tables and migrations stay in place.

## Tests

Database tests run against a throwaway local Postgres (never Neon). Install it once:

```bash
brew install postgresql@17
```

```bash
make test       # backend tests, then frontend typecheck and lint
```

## Tables

| Table | Purpose |
|---|---|
| `raw_items` | What was fetched from Google, unchanged. |
| `signals` | One row per person per email or row; rebuilt into candidates. |
| `candidates` | Staging: extracted records and the reviewer's approved edits. |
| `clients` | Official directory, filled only by Publish. The directory app reads only this. Includes `payments`, `client_since`, `services_availed` and `departments`. |
| `sync_state` | Where each mailbox's last scrape stopped. |
| `jobs`, `job_log` | Scrape/extract runs, their status and log lines (kept across restarts). |
